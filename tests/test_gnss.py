# SPDX-License-Identifier: Apache-2.0
"""gnss pack kind (RAIM1, AER-6755): NAVCEN almanac + NANUs -> mp-gnss/1.

The fixture under tests/fixtures/gnss/navcen-20261127 is a real recording of
navcen.uscg.gov made on 2026-11-27 with ``packtool make-gnss --record``; the
expected values below were read off those files by hand (README beside them).
"""

import datetime as dt
import json
import shutil
from pathlib import Path

import pytest

from packtools import gnss, packmeta, signing
from packtools.cli import main as packtool
from packtools.manifest import Manifest, PackEntry
from packtools.packmeta import PackMeta
from packtools.upload import LocalStore
from pyefis_data.config import Config
from pyefis_data.core import LocalDirRemote, Updater

FIXTURE = Path(__file__).parent / "fixtures" / "gnss" / "navcen-20261127"
NOW = dt.datetime(2026, 11, 27, 16, 0, tzinfo=dt.timezone.utc)
UTC = dt.timezone.utc


def _doc():
    return gnss.build(gnss.fixture_getter(FIXTURE), NOW)


# --- the recorded build ----------------------------------------------------

def test_fixture_build_almanac():
    doc = _doc()
    assert doc["schema"] == "mp-gnss/1"
    a = doc["almanac"]
    # "******** Week 399 almanac for PRN-01 ********", TOA 61440 s: week 399
    # mod 1024 is full week 2447 (the third rollover), 2026-11-29 17:04 GPS.
    assert (a["week"], a["week_mod1024"], a["toa_s"]) == (2447, 399, 61440.0)
    assert a["toa_gps"] == "2026-11-29T17:04:00Z"
    sats = doc["almanac"]["satellites"]
    assert [s["prn"] for s in sats] == list(range(1, 33))
    prn1 = sats[0]
    assert prn1["health"] == 0
    assert prn1["e"] == pytest.approx(0.2304553986e-2)
    assert prn1["sqrt_a"] == pytest.approx(5153.681152)
    assert prn1["omega0"] == pytest.approx(-1.751797821)


def test_fixture_build_nanus():
    doc = _doc()
    assert doc["nanu"]["latest"] == 2026105
    assert doc["nanu"]["latest_dtg"] == "2026-11-25T16:57:00Z"
    by_nanu = {w["nanu"]: w for w in doc["nanu"]["outages"]}
    # The newest NANU: PRN06 Delta-V, JDAY 337/1600 - 338/0400.
    assert by_nanu[2026105] == {"prn": 6, "svn": 67, "type": "FCSTDV", "ref": None,
                                "nanu": 2026105, "start": "2026-12-03T16:00:00Z",
                                "stop": "2026-12-04T04:00:00Z"}
    # UNUSUFN 2026103 was closed by UNUSABLE 2026104: only the closed window
    # stands, and NAVCEN's own DTG on 2026104 (220629Z) precedes the NANU it
    # references (220937Z), so order is by number, not DTG.
    assert 2026103 not in by_nanu
    assert (by_nanu[2026104]["start"], by_nanu[2026104]["stop"]) == \
        ("2026-11-22T09:37:00Z", "2026-11-22T13:04:00Z")
    # A forecast that later got its summary is not listed twice.
    assert 2026101 not in by_nanu and by_nanu[2026102]["ref"] == 2026101
    assert len(doc["nanu"]["outages"]) == 9


def test_fixture_build_provenance():
    doc = _doc()
    assert doc["license"] == "LicenseRef-US-Government-Work"
    assert "NAVCEN" in doc["attribution"]
    alm = {s["url"]: s for s in doc["almanac"]["sources"]}
    nanu = {s["url"]: s for s in doc["nanu"]["sources"]}
    yuma = FIXTURE / "sites/default/files/gps/almanac/current_yuma.alm"
    assert list(alm) == [gnss.YUMA_URL]
    assert alm[gnss.YUMA_URL]["sha256"] == signing.sha256_file(yuma)
    assert gnss.CURRENT_NANU_URL in nanu and gnss.CONSTELLATION_URL in nanu
    assert gnss.YUMA_URL not in nanu
    assert all(u.startswith("https://navcen.uscg.gov/") for u in {**alm, **nanu})


def test_almanac_and_nanu_sections_are_separable():
    """RAIM7: a receiver-decoded almanac replaces the ``almanac`` object alone,
    so each section must stand on its own, with its own age and sources."""
    doc = _doc()
    assert set(doc) == {"schema", "generated", "almanac", "nanu",
                        "license", "license_url", "attribution"}
    a, n = doc["almanac"], doc["nanu"]
    assert {"toa_gps", "satellites", "sources", "format", "origin"} <= set(a)
    assert {"latest_dtg", "outages", "general", "sources"} <= set(n)
    assert not any(k in json.dumps(a) for k in ("outages", "latest_dtg"))


def test_build_is_deterministic():
    assert gnss.dumps(_doc()) == gnss.dumps(_doc())


def test_fixture_refuses_an_unrecorded_url():
    with pytest.raises(gnss.GnssError, match="no recording"):
        gnss.fixture_getter(FIXTURE)(gnss.nanu_url(2026001))


def test_stale_almanac_refuses():
    with pytest.raises(gnss.GnssError, match="stale"):
        gnss.build(gnss.fixture_getter(FIXTURE), NOW + dt.timedelta(days=20))


# --- the YUMA parser is strict ---------------------------------------------

def _yuma():
    return (FIXTURE / "sites/default/files/gps/almanac/current_yuma.alm").read_text()


def test_yuma_missing_field_refuses():
    text = _yuma().replace("Af1(s/s):", "Afx(s/s):", 1)
    with pytest.raises(gnss.GnssError, match="unknown field"):
        gnss.parse_yuma(text)


def test_yuma_out_of_range_refuses():
    text = _yuma().replace("SQRT(A)  (m 1/2):           5153.681152",
                           "SQRT(A)  (m 1/2):           6153.681152", 1)
    assert text != _yuma()
    with pytest.raises(gnss.GnssError, match="outside"):
        gnss.parse_yuma(text)


def test_yuma_truncated_refuses():
    blocks = _yuma().split("\n\n")
    with pytest.raises(gnss.GnssError, match="below the floor"):
        gnss.parse_yuma("\n\n".join(blocks[:20]))


def test_full_week_rollover():
    assert gnss.full_week(399, NOW) == 2447
    # Just after a rollover, week 2 is the new epoch's, not the old one's.
    after = gnss.GPS_EPOCH + dt.timedelta(weeks=3072 + 2)
    assert gnss.full_week(2, after) == 3074
    assert gnss.full_week(1023, after) == 3071


# --- the NANU parser and the outage reduction ------------------------------

def _nanu(number, typ, *, prn=6, svn=67, ref="N/A", start=("03 DEC 2026", "1600", 337),
          stop=("04 DEC 2026", "0400", 338)):
    sd, st, sj = start
    if stop is None:
        stop_lines = ("       STOP JDAY: UFN\n       STOP TIME ZULU: N/A\n"
                      "       STOP CALENDAR DATE: N/A\n")
    else:
        stop_lines = (f"       STOP JDAY: {stop[2]:03d}\n       STOP TIME ZULU: {stop[1]}\n"
                      f"       STOP CALENDAR DATE: {stop[0]}\n")
    return (f"NOTICE ADVISORY TO NAVSTAR USERS (NANU) {number}\n"
            f"SUBJ: SVN{svn} (PRN{prn:02d}) TEST\n"
            f"1.     NANU TYPE: {typ}\n       NANU NUMBER: {number}\n"
            f"       NANU DTG: 251657Z NOV 2026\n       REFERENCE NANU: {ref}\n"
            f"       REF NANU DTG: N/A\n       SVN: {svn}\n       PRN: {prn:02d}\n"
            f"       START JDAY: {sj:03d}\n       START TIME ZULU: {st}\n"
            f"       START CALENDAR DATE: {sd}\n" + stop_lines)


def test_unknown_nanu_type_refuses():
    with pytest.raises(gnss.GnssError, match="unknown NANU TYPE 'FCSTNEW'"):
        gnss.parse_nanu(_nanu(2026200, "FCSTNEW"))


def test_jday_mismatch_refuses():
    with pytest.raises(gnss.GnssError, match="JDAY 336 is not 03 DEC 2026"):
        gnss.parse_nanu(_nanu(2026200, "FCSTDV", start=("03 DEC 2026", "1600", 336)))


def test_replace_without_reference_refuses():
    with pytest.raises(gnss.GnssError, match="must reference"):
        gnss.parse_nanu(_nanu(2026200, "FCSTSUMM"))


def test_outage_reduction():
    p = gnss.parse_nanu
    nanus = [
        p(_nanu(2026200, "FCSTMX", prn=3, svn=69)),
        p(_nanu(2026201, "FCSTCANC", prn=3, svn=69, ref="2026200")),       # cancelled
        p(_nanu(2026202, "FCSTDV", prn=5, svn=50)),
        p(_nanu(2026203, "FCSTRESCD", prn=5, svn=50, ref="2026202",         # moved a day
                start=("04 DEC 2026", "1600", 338), stop=("05 DEC 2026", "0400", 339))),
        p(_nanu(2026204, "UNUSUFN", prn=9, svn=68, stop=None)),             # open-ended
        p(_nanu(2026205, "DECOM", prn=11, svn=78, stop=None)),
        p(_nanu(2026206, "USABINIT", prn=11, svn=90,                        # PRN reassigned
                start=("05 DEC 2026", "1200", 339), stop=None)),
    ]
    out = {w["nanu"]: w for w in gnss.outages(nanus)}
    assert set(out) == {2026203, 2026204, 2026205}
    assert out[2026203]["start"] == dt.datetime(2026, 12, 4, 16, 0, tzinfo=UTC)
    assert out[2026204]["stop"] is None
    assert out[2026205]["stop"] == dt.datetime(2026, 12, 5, 12, 0, tzinfo=UTC)


def test_walk_crosses_the_year_boundary():
    """A January build walks from 2027003 down into 2026's last NANU, which it
    finds by binary search rather than by probing every number."""
    pages = {gnss.nanu_url(n): _nanu(n, "FCSTSUMM", ref=str(n - 1),
                                     start=("03 JAN 2027", "1600", 3),
                                     stop=("03 JAN 2027", "1700", 3)).encode()
             for n in (2027001, 2027002, 2027003)}
    for n in range(2026001, 2026013):
        dtg = "011657Z OCT 2026" if n < 2026012 else "301657Z DEC 2026"
        pages[gnss.nanu_url(n)] = _nanu(n, "FCSTDV").replace(
            "251657Z NOV 2026", dtg).encode()
    pages[gnss.CURRENT_NANU_URL] = pages[gnss.nanu_url(2027003)]
    pages[gnss.YUMA_URL] = b""
    pages[gnss.CONSTELLATION_URL] = b'<a href="/sites/default/files/gps/nanu/2027/2027003.nnu">'
    asked = []

    def get(url):
        asked.append(url)
        return pages.get(url)

    got = gnss.collect(get, dt.datetime(2027, 1, 4, tzinfo=UTC), lookback_days=45)
    numbers = [n.number for n in got["nanus"]]
    assert numbers[-3:] == [2027001, 2027002, 2027003]
    assert 2026012 in numbers and 2026011 in numbers   # stops at the first old DTG
    assert 2026010 not in numbers
    assert len(asked) < 30


# --- the container and the pack -------------------------------------------

def _payload(tmp_path):
    return gnss.write(_doc(), tmp_path / "gnss.json")


def test_json_pack_meta_round_trip(tmp_path):
    p = _payload(tmp_path)
    meta = PackMeta(id="gnss-global", kind="gnss", cycle="261127",
                    effective="2026-11-27", expires="2026-12-04",
                    attribution=gnss.ATTRIBUTION, license=gnss.LICENSE)
    packmeta.embed_json(p, meta)
    assert packmeta.is_json_pack(p)
    assert packmeta.read(p) == meta
    doc = json.loads(p.read_text())
    assert doc["schema"] == "mp-gnss/1" and doc["pack_meta"]["kind"] == "gnss"
    first = p.read_bytes()
    packmeta.embed_json(p, meta)                    # re-embedding is a no-op
    assert p.read_bytes() == first


def test_build_pack_defaults_for_gnss(tmp_path):
    p = _payload(tmp_path)
    sk, _pub = signing.generate_keypair()
    (tmp_path / "k.sec").write_text(sk.to_b64())
    assert packtool(["build-pack", str(p), "--id", "gnss-global", "--kind", "gnss",
                     "--date", "2026-11-27", "--sec", str(tmp_path / "k.sec"),
                     "--out", str(tmp_path / "out")]) == 0
    m = json.loads((tmp_path / "out" / "manifest.json").read_text())
    (e,) = m["packs"]
    assert (e["cycle"], e["effective"], e["expires"]) == ("261127", "2026-11-27", "2026-12-11")
    assert e["license"] == gnss.LICENSE and "NAVCEN" in e["attribution"]


def test_build_pack_refuses_to_publish_gnss(tmp_path):
    with pytest.raises(SystemExit, match="no gnss checks"):
        packtool(["build-pack", str(_payload(tmp_path)), "--id", "gnss-global",
                  "--kind", "gnss", "--upload"])


def test_updater_installs_gnss(tmp_path):
    """The device path: verify the signature, sha256 the pack, swap it in at
    <root>/gnss/current/gnss.json, and track it by default."""
    pack = tmp_path / "build" / "gnss-global-261127.pack"
    pack.parent.mkdir(parents=True)
    shutil.copy2(_payload(tmp_path), pack)
    meta = PackMeta(id="gnss-global", kind="gnss", cycle="261127",
                    effective="2026-11-27", expires="2026-12-11",
                    attribution=gnss.ATTRIBUTION, license=gnss.LICENSE)
    packmeta.embed_json(pack, meta)
    origin = "https://test.local"
    entry = PackEntry.from_pack(pack, meta, url=f"{origin}/packs/{pack.name}")
    store = LocalStore(tmp_path / "r2")
    sk, pub = signing.generate_keypair()
    m = Manifest.new("2026-11-27T00:00:00Z")
    store.put_file(f"packs/{pack.name}", pack)
    m.upsert(entry)
    store.put_bytes("manifest.json", m.to_bytes())
    store.put_bytes("manifest.json.minisig", signing.sign(m.to_bytes(), sk).encode("ascii"))

    cfg = Config(base_url=origin, root=tmp_path / "pi")          # default track_kinds
    up = Updater(cfg, pub, remote=LocalDirRemote(store.root), today=dt.date(2026, 11, 28))
    up.update()
    f = tmp_path / "pi" / "gnss" / "current" / "gnss.json"
    doc = json.loads(f.read_text())
    assert doc["almanac"]["week"] == 2447 and doc["pack_meta"]["cycle"] == "261127"
    st = {r.pack_id: r for r in up.status()}
    assert st["gnss-global"].status == "current"
    assert st["gnss-global"].name == "GPS Almanac & NANUs"
