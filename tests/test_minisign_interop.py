# SPDX-License-Identifier: Apache-2.0
"""Interop with the stock ``minisign`` binary.

``packtools.signing`` both signs and (on the Pi) verifies, so every test in
test_signing.py would still pass if the module drifted from the minisign wire
format in a way that matched on both sides. These tests hold the module's
docstring claim -- "a third party can verify a manifest with the stock
``minisign`` binary" -- against the real binary, in both directions.

Skipped when ``minisign`` is not on PATH (``$MINISIGN`` overrides the path).
CI sets ``MAKERPLANE_REQUIRE_MINISIGN=1`` so a missing binary fails the run
instead of silently skipping the only check of the claim.
"""

import base64
import os
import shutil
import subprocess

import nacl.exceptions
import pytest

from packtools import signing
from packtools.manifest import Manifest

MINISIGN = os.environ.get("MINISIGN") or shutil.which("minisign")

if not MINISIGN:
    if os.environ.get("MAKERPLANE_REQUIRE_MINISIGN") == "1":
        raise RuntimeError("MAKERPLANE_REQUIRE_MINISIGN=1 but no minisign binary found")
    pytestmark = pytest.mark.skip(reason="stock minisign binary not installed")

COMMENT = "generated 2026-06-14"


def _minisign(*args, cwd=None):
    return subprocess.run([MINISIGN, *args], cwd=cwd, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, timeout=30)


@pytest.fixture
def signed(tmp_path):
    """A manifest's real canonical bytes signed by packtools, the way the
    publisher signs it, plus the public key file. Returns (paths, secret)."""
    sk, pub = signing.generate_keypair()
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(Manifest.new("2026-06-14T00:00:00Z").to_bytes())
    signing.sign_file(manifest, sk, trusted_comment=COMMENT)
    pubfile = tmp_path / "minisign.pub"
    pubfile.write_text(pub, encoding="ascii")
    return manifest, pubfile, sk


def _verify(manifest, pubfile, sig=None):
    args = ["-V", "-m", str(manifest), "-p", str(pubfile)]
    if sig is not None:
        args += ["-x", str(sig)]
    return _minisign(*args)


# --- packtools signs, stock minisign verifies ------------------------------

def test_stock_minisign_verifies_packtools_signature(signed):
    manifest, pubfile, _sk = signed
    r = _verify(manifest, pubfile)
    assert r.returncode == 0, r.stderr
    assert f"Trusted comment: {COMMENT}" in r.stdout


def test_stock_minisign_rejects_tampered_manifest(signed):
    manifest, pubfile, _sk = signed
    raw = bytearray(manifest.read_bytes())
    raw[len(raw) // 2] ^= 0x01
    manifest.write_bytes(bytes(raw))
    r = _verify(manifest, pubfile)
    assert r.returncode != 0
    assert "verification failed" in (r.stdout + r.stderr).lower()


def test_stock_minisign_rejects_swapped_trusted_comment(signed):
    # The data signature still holds; only the global signature binds the
    # comment. Stock minisign must check it as packtools.verify does.
    manifest, pubfile, _sk = signed
    sig = manifest.with_name(manifest.name + ".minisig")
    lines = sig.read_text(encoding="ascii").splitlines()
    lines[2] = "trusted comment: generated 2099-01-01"
    sig.write_text("\n".join(lines) + "\n", encoding="ascii")
    r = _verify(manifest, pubfile)
    assert r.returncode != 0


def test_stock_minisign_rejects_other_key(signed, tmp_path):
    manifest, _pubfile, _sk = signed
    _other, other_pub = signing.generate_keypair()
    other = tmp_path / "other.pub"
    other.write_text(other_pub, encoding="ascii")
    r = _verify(manifest, other)
    assert r.returncode != 0


# --- stock minisign signs, packtools verifies ------------------------------
# signing.verify accepts the prehashed "ED" type only for this case, and
# nothing else in the suite produces one.

@pytest.mark.parametrize("legacy", [False, True], ids=["ED-prehashed", "Ed-legacy"])
def test_packtools_verifies_stock_minisign_signature(tmp_path, legacy):
    sec, pub = tmp_path / "stock.key", tmp_path / "stock.pub"
    r = _minisign("-G", "-W", "-s", str(sec), "-p", str(pub))
    assert r.returncode == 0, r.stderr
    data = tmp_path / "manifest.json"
    data.write_bytes(Manifest.new("2026-06-14T00:00:00Z").to_bytes())
    args = ["-S", "-s", str(sec), "-m", str(data), "-t", COMMENT]
    if legacy:
        args.append("-l")
    r = _minisign(*args)
    assert r.returncode == 0, r.stderr

    pub_text = pub.read_text(encoding="ascii")
    sig_text = data.with_name(data.name + ".minisig").read_text(encoding="ascii")
    expected = b"Ed" if legacy else b"ED"
    assert base64.b64decode(sig_text.splitlines()[1])[:2] == expected

    assert signing.verify(data.read_bytes(), sig_text, pub_text) == COMMENT
    with pytest.raises(nacl.exceptions.BadSignatureError):
        signing.verify(data.read_bytes() + b" ", sig_text, pub_text)
