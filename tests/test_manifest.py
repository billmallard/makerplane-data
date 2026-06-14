"""Manifest schema — roundtrip, validation, currency queries, and the
end-to-end build+sign+verify path with a real (tiny) sqlite pack."""

import datetime as dt
import sqlite3

import pytest

from packtools import signing
from packtools.manifest import Manifest, PackEntry, ManifestError, validate
from packtools.packmeta import PackMeta, embed_sqlite
from packtools.regions import load_regions, manifest_regions_block


def D(s):
    return dt.date.fromisoformat(s)


GEN = "2026-06-14T03:10:00Z"


def _entry(**kw):
    base = dict(id="navdata-conus", kind="navdata", cycle="2606",
                bytes=1024, sha256="a" * 64, url="https://x/p.pack",
                effective="2026-06-11", expires="2026-07-09")
    base.update(kw)
    return PackEntry(**base)


def test_manifest_bytes_roundtrip():
    m = Manifest.new(GEN)
    m.upsert(_entry())
    raw = m.to_bytes()
    m2 = Manifest.from_bytes(raw)
    assert m2.to_bytes() == raw          # canonical form is stable
    assert m2.packs[0].cycle == "2606"


def test_canonical_bytes_are_deterministic():
    m = Manifest.new(GEN)
    m.upsert(_entry(cycle="2607", effective="2026-07-09", expires="2026-08-06"))
    m.upsert(_entry(cycle="2606"))
    # Insert order should not change the serialized bytes (sorted).
    n = Manifest.new(GEN)
    n.upsert(_entry(cycle="2606"))
    n.upsert(_entry(cycle="2607", effective="2026-07-09", expires="2026-08-06"))
    assert m.to_bytes() == n.to_bytes()


def test_upsert_replaces_same_id_cycle():
    m = Manifest.new(GEN)
    m.upsert(_entry(bytes=1))
    m.upsert(_entry(bytes=2))
    assert len(m.packs) == 1 and m.packs[0].bytes == 2


def test_select_by_date_window():
    m = Manifest.new(GEN)
    m.upsert(_entry(cycle="2606", effective="2026-06-11", expires="2026-07-09"))
    m.upsert(_entry(cycle="2607", effective="2026-07-09", expires="2026-08-06"))
    assert m.select("navdata-conus", D("2026-06-14")).cycle == "2606"
    assert m.select("navdata-conus", D("2026-07-20")).cycle == "2607"
    assert m.select("navdata-conus", D("2030-01-01")) is None


def test_non_cyclical_entry_always_covers():
    e = _entry(id="terrain-na", kind="terrain", cycle="2024ed",
               effective=None, expires=None)
    assert e.covers(D("2000-01-01")) and e.covers(D("2099-01-01"))
    assert e.days_until_expiry(D("2026-06-14")) is None


def test_prune_old_cycles_keeps_recent():
    m = Manifest.new(GEN)
    for c, eff, exp in [("2605", "2026-05-14", "2026-06-11"),
                        ("2606", "2026-06-11", "2026-07-09"),
                        ("2607", "2026-07-09", "2026-08-06")]:
        m.upsert(_entry(cycle=c, effective=eff, expires=exp))
    m.prune_old_cycles(keep=2)
    assert sorted(p.cycle for p in m.packs) == ["2606", "2607"]


def test_validate_rejects_bad_manifests():
    with pytest.raises(ManifestError):
        validate({"manifest_version": 999, "generated": GEN, "packs": []})
    with pytest.raises(ManifestError):
        validate({"manifest_version": 1, "generated": GEN,
                  "packs": [{"id": "x", "kind": "navdata", "cycle": "1"}]})  # missing fields
    with pytest.raises(ManifestError):
        validate({"manifest_version": 1, "generated": GEN,
                  "packs": [{"id": "x", "kind": "BOGUS", "cycle": "1",
                             "bytes": 1, "sha256": "a" * 64, "url": "u"}]})
    with pytest.raises(ManifestError):
        validate({"manifest_version": 1, "generated": GEN,
                  "packs": [{"id": "x", "kind": "navdata", "cycle": "1",
                             "bytes": 1, "sha256": "short", "url": "u"}]})


def test_validate_rejects_expires_without_effective():
    with pytest.raises(ManifestError):
        validate({"manifest_version": 1, "generated": GEN, "packs": [{
            "id": "x", "kind": "navdata", "cycle": "1", "bytes": 1,
            "sha256": "a" * 64, "url": "u", "expires": "2026-07-09"}]})


def test_regions_block_loads():
    regions = load_regions()
    block = manifest_regions_block(regions)
    assert "conus" in block and "bbox" in block["conus"]


def test_end_to_end_build_sign_verify(tmp_path):
    """The Phase A acceptance path in miniature: build a pack, embed
    pack_meta, register it in a manifest, sign the manifest, verify."""
    pack = tmp_path / "navdata-conus-2606.pack"
    con = sqlite3.connect(str(pack))
    con.execute("CREATE TABLE airports (icao TEXT)")
    con.execute("INSERT INTO airports VALUES ('KSBA')")
    con.commit()
    con.close()

    meta = PackMeta(id="navdata-conus", kind="navdata", cycle="2606",
                    effective="2026-06-11", expires="2026-07-09",
                    attribution="FAA NASR (public domain)")
    embed_sqlite(pack, meta)

    entry = PackEntry.from_pack(pack, meta,
                                url="https://data.makerplane.org/packs/navdata-conus-2606.pack",
                                regions=["conus"])
    assert entry.bytes == pack.stat().st_size
    assert len(entry.sha256) == 64

    m = Manifest.new(GEN)
    m.upsert(entry)
    manifest_path = tmp_path / "manifest.json"
    m.write(manifest_path)

    sk, pub = signing.generate_keypair()
    signing.sign_file(manifest_path, sk, trusted_comment="generated 2026-06-14")

    # The Pi-side verification: verify the exact bytes, then trust the sha256.
    trusted = signing.verify_file(manifest_path, pub)
    assert trusted == "generated 2026-06-14"
    reloaded = Manifest.read(manifest_path)
    assert signing.sha256_file(pack) == reloaded.packs[0].sha256
