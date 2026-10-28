# SPDX-License-Identifier: Apache-2.0
"""publish.backfill_licenses -- fill blank manifest licenses from attribution,
re-sign, and touch nothing else (AER-4098)."""

import json

import pytest

from packtools import signing
from packtools.manifest import Manifest, PackEntry
from packtools.publish import MANIFEST_KEY, SIG_KEY, backfill_licenses
from packtools.sources import (COPERNICUS_DEM_LICENSE, OSM_LICENSE,
                               OURAIRPORTS_LICENSE, SOURCES)
from packtools.upload import LocalStore

GEN = "2026-10-28T00:00:00Z"


def _entry(id, cycle, attribution, license="", license_url=""):
    return PackEntry(id=id, kind="water", cycle=cycle, bytes=1, sha256="ab" * 32,
                     url=f"https://e/{id}-{cycle}.pack", attribution=attribution,
                     license=license, license_url=license_url)


@pytest.fixture
def store(tmp_path):
    s = LocalStore(tmp_path / "r2")
    m = Manifest.new("2026-10-01T00:00:00Z")
    for e in [
        _entry("airports-conus", "2610", "FAA NASR (public domain)"),
        _entry("airports-canada", "2026.06", "OurAirports (public domain)"),
        _entry("terrain-us-west", "2024edr1", "Copernicus GLO-30 (redistribution permitted)"),
        _entry("water-na", "2026q2r6", "OpenStreetMap contributors (ODbL); Natural Earth"),
        _entry("highways-conus", "2026q3r1", "OpenStreetMap contributors (ODbL)",
               license="already-set", license_url="https://keep/"),
        _entry("mystery", "1", "Somebody Else"),
    ]:
        m.upsert(e)
    s.put_bytes(MANIFEST_KEY, m.to_bytes())
    return s


def _packs(store):
    return {(p.id, p.cycle): p for p in Manifest.from_bytes(store.get_bytes(MANIFEST_KEY)).packs}


def test_fills_blanks_from_attribution_and_resigns(store):
    sk, pub = signing.generate_keypair()
    before = json.loads(store.get_bytes(MANIFEST_KEY))
    filled = backfill_licenses(store, sk, generated=GEN, sign=signing.sign, log=lambda *_: None)

    assert {(i, c) for i, c, *_ in filled} == {
        ("airports-conus", "2610"), ("airports-canada", "2026.06"),
        ("terrain-us-west", "2024edr1"), ("water-na", "2026q2r6")}
    p = _packs(store)
    # FAA matches what the cyclical Source itself declares.
    assert p[("airports-conus", "2610")].license == SOURCES["airports-conus"].license
    assert p[("airports-canada", "2026.06")].license == OURAIRPORTS_LICENSE
    assert p[("terrain-us-west", "2024edr1")].license == COPERNICUS_DEM_LICENSE
    assert p[("water-na", "2026q2r6")].license == OSM_LICENSE
    # a set license is never overwritten; an unknown upstream is never guessed
    assert p[("highways-conus", "2026q3r1")].license == "already-set"
    assert p[("highways-conus", "2026q3r1")].license_url == "https://keep/"
    assert p[("mystery", "1")].license == ""

    raw = store.get_bytes(MANIFEST_KEY)
    signing.verify(raw, store.get_bytes(SIG_KEY).decode("ascii"), pub)   # raises if bad
    # nothing but license/license_url (and generated) moved
    after = json.loads(raw)
    assert after["generated"] == GEN
    for a, b in zip(before["packs"], after["packs"]):
        assert {k for k in a.keys() | b.keys() if a.get(k) != b.get(k)} <= {"license", "license_url"}


def test_dry_run_writes_nothing(store):
    before = store.get_bytes(MANIFEST_KEY)
    filled = backfill_licenses(store, None, generated=GEN, sign=signing.sign,
                               dry_run=True, log=lambda *_: None)
    assert len(filled) == 4
    assert store.get_bytes(MANIFEST_KEY) == before
    assert store.get_bytes(SIG_KEY) is None


def test_nothing_to_fill_does_not_resign(store):
    sk, _ = signing.generate_keypair()
    backfill_licenses(store, sk, generated=GEN, sign=signing.sign, log=lambda *_: None)
    sig = store.get_bytes(SIG_KEY)
    assert backfill_licenses(store, sk, generated="2026-10-29T00:00:00Z",
                             sign=signing.sign, log=lambda *_: None) == []
    assert store.get_bytes(SIG_KEY) == sig
