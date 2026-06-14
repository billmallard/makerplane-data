"""Manifest signing — ed25519, minisign wire-compatible.

The catalog manifest is the only thing the Pi trusts; everything else
(pack integrity) hangs off the per-pack sha256 values *inside* the signed
manifest. So this module is the root of the trust chain:

    committed public key  ->  manifest.json.minisig  ->  manifest sha256s
                                                          ->  each .pack

Design choices:

  * **Signatures and public keys are real minisign format.** A human (or a
    third party) can verify a manifest with the stock ``minisign`` binary:
    ``minisign -Vm manifest.json -p keys/minisign.pub``. No lock-in to our
    tooling on the *verify* side.

  * **The SECRET key is NOT minisign's password-encrypted format.** CI signs
    unattended, so the secret is a plain base64 blob (key_id ‖ seed) stored
    in a GitHub Actions secret / offline backup. Only our tooling signs;
    that never needs the stock binary.

  * **Pure PyNaCl, no native dependency.** The Pi verifies with the same
    code path, so the build and consume sides can never drift.

  * Legacy ``"Ed"`` signature type (signs the raw bytes) — the manifest is a
    few KB, so prehashing buys nothing. Verification also accepts ``"ED"``
    (prehashed) in case a manifest is ever signed with the stock binary.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from pathlib import Path

import nacl.signing
import nacl.exceptions

_SIG_LEGACY = b"Ed"   # signature is over the raw message
_SIG_PREHASH = b"ED"  # signature is over BLAKE2b-512(message)

_DEFAULT_UNTRUSTED = "signature from makerplane-data minisign key"


# --------------------------------------------------------------------------
# key material
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class SecretKey:
    key_id: bytes            # 8 bytes
    signing_key: nacl.signing.SigningKey

    def to_b64(self) -> str:
        """Serialise as base64(key_id ‖ 32-byte seed) for a GH secret/env."""
        seed = bytes(self.signing_key)  # SigningKey bytes == 32-byte seed
        return base64.b64encode(self.key_id + seed).decode("ascii")

    @classmethod
    def from_b64(cls, blob: str) -> "SecretKey":
        raw = base64.b64decode(blob.strip())
        if len(raw) != 40:
            raise ValueError("secret key blob must be 40 bytes (8 key_id + 32 seed)")
        return cls(raw[:8], nacl.signing.SigningKey(raw[8:]))

    def public_key_text(self, comment: str = "makerplane-data public key") -> str:
        vk = self.signing_key.verify_key
        blob = _SIG_LEGACY + self.key_id + bytes(vk)
        return f"untrusted comment: {comment}\n{base64.b64encode(blob).decode('ascii')}\n"


@dataclass(frozen=True)
class PublicKey:
    key_id: bytes
    verify_key: nacl.signing.VerifyKey

    @classmethod
    def from_text(cls, text: str) -> "PublicKey":
        line = _payload_line(text)
        blob = base64.b64decode(line)
        if len(blob) != 42:
            raise ValueError("public key payload must be 42 bytes (2 alg + 8 id + 32 key)")
        return cls(blob[2:10], nacl.signing.VerifyKey(blob[10:]))


def generate_keypair(comment: str = "makerplane-data public key") -> tuple[SecretKey, str]:
    """Return a fresh (SecretKey, public_key_text). key_id is random."""
    sk = SecretKey(secrets.token_bytes(8), nacl.signing.SigningKey.generate())
    return sk, sk.public_key_text(comment)


# --------------------------------------------------------------------------
# signing / verification
# --------------------------------------------------------------------------

def sign(data: bytes, secret: SecretKey, *,
         trusted_comment: str = "",
         untrusted_comment: str = _DEFAULT_UNTRUSTED) -> str:
    """Produce minisign-format ``.minisig`` text for ``data``."""
    signature = secret.signing_key.sign(data).signature          # 64 bytes
    sig_blob = _SIG_LEGACY + secret.key_id + signature
    # The global signature binds the trusted comment to the signature so it
    # cannot be swapped. minisign signs (signature ‖ trusted_comment_bytes).
    global_sig = secret.signing_key.sign(
        signature + trusted_comment.encode("utf-8")).signature
    return (
        f"untrusted comment: {untrusted_comment}\n"
        f"{base64.b64encode(sig_blob).decode('ascii')}\n"
        f"trusted comment: {trusted_comment}\n"
        f"{base64.b64encode(global_sig).decode('ascii')}\n"
    )


def verify(data: bytes, sig_text: str, public_key_text: str) -> str:
    """Verify a minisign signature. Returns the trusted comment on success.

    Raises ``BadSignatureError`` (or ValueError on a malformed signature)
    on any failure. The caller treats *any* exception as "do not install".
    """
    pk = PublicKey.from_text(public_key_text)
    lines = [ln for ln in sig_text.splitlines()]
    try:
        sig_blob = base64.b64decode(lines[1])
        trusted_comment = lines[2].split("trusted comment: ", 1)[1] if len(lines) > 2 else ""
        global_sig = base64.b64decode(lines[3]) if len(lines) > 3 else None
    except (IndexError, ValueError) as e:
        raise ValueError(f"malformed signature file: {e}") from e

    sig_alg, key_id, signature = sig_blob[:2], sig_blob[2:10], sig_blob[10:]
    if key_id != pk.key_id:
        raise ValueError("signature key id does not match public key")

    if sig_alg == _SIG_LEGACY:
        message = data
    elif sig_alg == _SIG_PREHASH:
        message = hashlib.blake2b(data, digest_size=64).digest()
    else:
        raise ValueError(f"unknown signature algorithm {sig_alg!r}")

    pk.verify_key.verify(message, signature)               # raises on bad sig
    if global_sig is not None:
        pk.verify_key.verify(signature + trusted_comment.encode("utf-8"), global_sig)
    return trusted_comment


# --------------------------------------------------------------------------
# file helpers
# --------------------------------------------------------------------------

def sign_file(path: str | Path, secret: SecretKey, **kw) -> Path:
    path = Path(path)
    sig_path = path.with_name(path.name + ".minisig")
    sig_path.write_text(sign(path.read_bytes(), secret, **kw), encoding="ascii")
    return sig_path


def verify_file(path: str | Path, public_key_text: str,
                sig_path: str | Path | None = None) -> str:
    path = Path(path)
    sig_path = Path(sig_path) if sig_path else path.with_name(path.name + ".minisig")
    return verify(path.read_bytes(), sig_path.read_text(encoding="ascii"), public_key_text)


def sha256_file(path: str | Path, _chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_chunk), b""):
            h.update(block)
    return h.hexdigest()


def _payload_line(text: str) -> str:
    """Return the first non-comment, non-empty base64 line of a key file."""
    for line in text.splitlines():
        s = line.strip()
        if s and not s.lower().startswith("untrusted comment:") \
              and not s.lower().startswith("trusted comment:"):
            return s
    raise ValueError("no key payload line found")
