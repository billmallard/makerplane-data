"""Signing — roundtrip, tamper detection, and the trust-chain invariants.

A test keypair is generated fresh per run (no committed secrets in tests).
"""

import base64

import pytest
import nacl.exceptions

from packtools import signing


def test_keypair_roundtrip_through_b64():
    sk, _pub = signing.generate_keypair()
    sk2 = signing.SecretKey.from_b64(sk.to_b64())
    assert sk2.key_id == sk.key_id
    assert bytes(sk2.signing_key) == bytes(sk.signing_key)


def test_public_key_text_parses():
    sk, pub = signing.generate_keypair("test key")
    pk = signing.PublicKey.from_text(pub)
    assert pk.key_id == sk.key_id


def test_sign_then_verify_ok():
    sk, pub = signing.generate_keypair()
    data = b'{"manifest_version": 1, "packs": []}\n'
    sig = signing.sign(data, sk, trusted_comment="cycle 2606")
    assert signing.verify(data, sig, pub) == "cycle 2606"


def test_tampered_data_fails():
    sk, pub = signing.generate_keypair()
    sig = signing.sign(b"original bytes", sk)
    with pytest.raises(nacl.exceptions.BadSignatureError):
        signing.verify(b"tampered bytes", sig, pub)


def test_wrong_key_fails():
    sk, _pub = signing.generate_keypair()
    _sk2, pub2 = signing.generate_keypair()   # different key entirely
    sig = signing.sign(b"data", sk)
    # key_id mismatch is caught before the crypto check.
    with pytest.raises(ValueError):
        signing.verify(b"data", sig, pub2)


def test_tampered_trusted_comment_fails():
    sk, pub = signing.generate_keypair()
    data = b"payload"
    sig = signing.sign(data, sk, trusted_comment="cycle 2606")
    # Swap the trusted comment but keep the (still-valid-over-data) signature.
    lines = sig.splitlines()
    lines[2] = "trusted comment: cycle 9999"
    forged = "\n".join(lines) + "\n"
    with pytest.raises(nacl.exceptions.BadSignatureError):
        signing.verify(data, forged, pub)


def test_malformed_signature_raises_valueerror():
    sk, pub = signing.generate_keypair()
    with pytest.raises(ValueError):
        signing.verify(b"data", "untrusted comment: junk\nnot-base64!!!\n", pub)


def test_sign_and_verify_file(tmp_path):
    sk, pub = signing.generate_keypair()
    f = tmp_path / "manifest.json"
    f.write_bytes(b'{"x": 1}\n')
    sig_path = signing.sign_file(f, sk, trusted_comment="t")
    assert sig_path.name == "manifest.json.minisig"
    assert signing.verify_file(f, pub) == "t"


def test_sha256_file(tmp_path):
    import hashlib
    f = tmp_path / "blob"
    payload = b"some pack bytes" * 1000
    f.write_bytes(payload)
    assert signing.sha256_file(f) == hashlib.sha256(payload).hexdigest()
