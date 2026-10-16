# SPDX-License-Identifier: Apache-2.0
"""fetch_pack: a build step's pack input is trusted only through the signed
manifest -- signature, current-edition selection, sha256 (AER-2287)."""

import datetime as dt
import hashlib

import pytest

from packtools import fetch_pack, signing
from packtools.manifest import Manifest, PackEntry

TODAY = dt.date(2026, 10, 16)
BODY = b"SQLite format 3\x00" + b"water" * 100


def _entry(cycle, body=BODY):
    return PackEntry(id="water-na", kind="water", cycle=cycle, bytes=len(body),
                     sha256=hashlib.sha256(body).hexdigest(),
                     url=f"https://x/packs/water-na-{cycle}.pack")


def _signed(*entries):
    sk, pub = signing.generate_keypair()
    m = Manifest.new("2026-10-16T00:00:00Z")
    for e in entries:
        m.upsert(e)
    raw = m.to_bytes()
    return raw, signing.sign(raw, sk), pub


def test_resolve_picks_current_edition():
    raw, sig, pub = _signed(_entry("2026q2r5"), _entry("2026q2r6"))
    assert fetch_pack.resolve_entry(raw, sig, pub, "water-na", TODAY).cycle == "2026q2r6"


def test_resolve_rejects_tampered_manifest():
    raw, sig, pub = _signed(_entry("2026q2r6"))
    with pytest.raises(Exception):
        fetch_pack.resolve_entry(raw.replace(b"2026q2r6", b"2026q2r7"), sig, pub,
                                 "water-na", TODAY)


def test_resolve_rejects_unlisted_id():
    raw, sig, pub = _signed(_entry("2026q2r6"))
    with pytest.raises(LookupError):
        fetch_pack.resolve_entry(raw, sig, pub, "water-eu", TODAY)


def _fake_download(body, calls):
    def download(url, dest):
        calls.append(url)
        dest.write_bytes(body)
        return dest
    return download


def test_fetch_downloads_then_reuses_matching_cache(tmp_path):
    e, calls = _entry("2026q2r6"), []
    p = fetch_pack.fetch_verified_pack(e, tmp_path, download=_fake_download(BODY, calls),
                                       log=lambda *a: None)
    assert p.name == "water-na-2026q2r6.pack" and p.read_bytes() == BODY
    again = fetch_pack.fetch_verified_pack(e, tmp_path, download=_fake_download(BODY, calls),
                                           log=lambda *a: None)
    assert again == p and calls == [e.url]          # second call hit the cache


def test_fetch_rejects_and_deletes_hash_mismatch(tmp_path):
    e, calls = _entry("2026q2r6"), []
    with pytest.raises(ValueError, match="does not match"):
        fetch_pack.fetch_verified_pack(e, tmp_path, download=_fake_download(b"evil", calls),
                                       log=lambda *a: None)
    assert not (tmp_path / "water-na-2026q2r6.pack").exists()


def test_fetch_replaces_corrupt_cache(tmp_path):
    e, calls = _entry("2026q2r6"), []
    (tmp_path / "water-na-2026q2r6.pack").write_bytes(b"truncated")
    p = fetch_pack.fetch_verified_pack(e, tmp_path, download=_fake_download(BODY, calls),
                                       log=lambda *a: None)
    assert p.read_bytes() == BODY and calls == [e.url]
