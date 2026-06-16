"""Merge OurAirports (public domain) airports into a NASR-built airports.sqlite.

FAA NASR is US-only, so the airport DB has no Canadian (or other foreign)
airports — not even border fields. OurAirports publishes a worldwide airport
dataset in the public domain whose ``runways.csv`` carries per-end threshold
lat/lon/elevation, which is exactly what the SVS ``runway_ends`` schema needs.

This module augments an already-built airports.sqlite *in place*, additively:
it inserts airports/runways for the requested ISO countries (default Canada)
using the same schema the NASR builder wrote, so ``NASRAirportDB`` reads the
combined database unchanged. The US/NASR rows are never touched (keys don't
collide: ICAO idents vs NASR numeric site_no), and the NASR-only fields
(markings, approach lighting, TDZE) are left null for the foreign records —
those are FAA-only attributes no free source provides.

Runway ends without published threshold coordinates are synthesized from the
airport reference point + true heading + length, so towered/paved fields draw
correctly even where OurAirports lacks exact thresholds.
"""
from __future__ import annotations

import csv
import io
import math
import sqlite3
from pathlib import Path

OURAIRPORTS_BASE = "https://davidmegginson.github.io/ourairports-data"
# Only real airports (skip heliports/seaplane bases/closed/balloonports).
AIRPORT_TYPES = ("large_airport", "medium_airport", "small_airport")
FT_PER_DEG_LAT = 364567.0
ATTRIBUTION = "OurAirports (public domain)"

# Mirrors the schema tools/build_airport_db.py writes, so a provider DB built
# here is read by the same NASRAirportDB the SVS uses. Only the columns this
# module populates are required; the rest are NASR-only and stay null/empty.
SCHEMA = """
CREATE TABLE IF NOT EXISTS airports (site_no TEXT PRIMARY KEY, icao TEXT NOT NULL,
  name TEXT, lat REAL NOT NULL, lon REAL NOT NULL, elev_ft REAL, mag_var REAL,
  state TEXT, city TEXT);
CREATE INDEX IF NOT EXISTS idx_airports_lat ON airports(lat);
CREATE INDEX IF NOT EXISTS idx_airports_icao ON airports(icao);
CREATE TABLE IF NOT EXISTS runways (site_no TEXT, rwy_id TEXT, length_ft REAL,
  width_ft REAL, surface TEXT, lighting TEXT, PRIMARY KEY (site_no, rwy_id));
CREATE TABLE IF NOT EXISTS runway_ends (site_no TEXT, rwy_id TEXT, end_id TEXT,
  true_alignment_deg REAL, lat REAL, lon REAL, elev_ft REAL, displaced_thr_lat REAL,
  displaced_thr_lon REAL, displaced_thr_len_ft REAL, tdz_elev_ft REAL,
  marking_type TEXT, apch_lgt_code TEXT, end_lgts_flag TEXT, cntrln_lgts_flag TEXT,
  tdz_lgt_flag TEXT, PRIMARY KEY (site_no, rwy_id, end_id));
"""


def init_schema(sqlite_path: Path | str) -> None:
    """Create an empty airports DB with the canonical schema."""
    con = sqlite3.connect(str(sqlite_path))
    try:
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()


def build_country_pack(out_path: Path | str, countries=("CA",), *,
                       cache_dir=None) -> dict:
    """Build a standalone airports.sqlite for ``countries`` (default Canada)
    from OurAirports — a self-contained provider DB the SVS merges with the US
    NASR primary. Returns the merge counts."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    init_schema(out_path)
    return merge_countries(out_path, countries, cache_dir=cache_dir)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm_surface(s: str) -> str:
    """Map OurAirports' free-form surface strings to NASR-style codes so the
    renderer's paved-only filter (ASPH/CONC/PEM) recognises them."""
    s = (s or "").upper()
    if "ASP" in s or "BIT" in s or "TAR" in s:
        return "ASPH"
    if "CON" in s:
        return "CONC"
    if "PEM" in s:
        return "PEM"
    return s.strip()


def fetch_csvs(cache_dir: Path | str | None = None):
    """Return (airports_rows, runways_rows) from OurAirports. Caches the two
    CSVs under cache_dir when given; otherwise fetches fresh each call."""
    import requests
    out = []
    for name in ("airports", "runways"):
        text = None
        if cache_dir is not None:
            f = Path(cache_dir) / f"ourairports_{name}.csv"
            if f.exists():
                text = f.read_text(encoding="utf-8")
        if text is None:
            text = requests.get(f"{OURAIRPORTS_BASE}/{name}.csv", timeout=120).text
            if cache_dir is not None:
                Path(cache_dir).mkdir(parents=True, exist_ok=True)
                (Path(cache_dir) / f"ourairports_{name}.csv").write_text(text, encoding="utf-8")
        out.append(list(csv.DictReader(io.StringIO(text))))
    return out[0], out[1]


def merge_countries(sqlite_path: Path | str, countries=("CA",), *,
                    airports=None, runways=None, cache_dir=None) -> dict:
    """Insert OurAirports airports/runways for ``countries`` into the airports
    sqlite at ``sqlite_path`` (built by build_airport_db.py). Additive and
    idempotent (INSERT OR IGNORE). Pass ``airports``/``runways`` row lists to
    skip the network (used by tests). Returns a counts dict."""
    if airports is None or runways is None:
        airports, runways = fetch_csvs(cache_dir)
    countries = set(countries)
    ap = {a["ident"]: a for a in airports
          if a.get("iso_country") in countries and a.get("ident")
          and a.get("type") in AIRPORT_TYPES}

    con = sqlite3.connect(str(sqlite_path))
    try:
        for ident, a in ap.items():
            con.execute(
                "INSERT OR IGNORE INTO airports "
                "(site_no, icao, name, lat, lon, elev_ft, mag_var, state, city) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (ident, ident, a.get("name"), _f(a.get("latitude_deg")),
                 _f(a.get("longitude_deg")), _f(a.get("elevation_ft")), None,
                 a.get("iso_region"), a.get("municipality")))

        n_ap = len(ap)
        n_direct = n_synth = n_skip = 0
        for r in runways:
            ident = r.get("airport_ident")
            if ident not in ap or r.get("closed") == "1":
                continue
            le, he = r.get("le_ident"), r.get("he_ident")
            if not (le and he):
                continue
            length = _f(r.get("length_ft"))
            hdg = _f(r.get("le_heading_degT"))
            le_lat, le_lon = _f(r.get("le_latitude_deg")), _f(r.get("le_longitude_deg"))
            he_lat, he_lon = _f(r.get("he_latitude_deg")), _f(r.get("he_longitude_deg"))
            le_elev, he_elev = _f(r.get("le_elevation_ft")), _f(r.get("he_elevation_ft"))

            if le_lat and he_lat:
                n_direct += 1
            elif hdg is not None and length:
                a = ap[ident]
                lat0, lon0 = _f(a.get("latitude_deg")), _f(a.get("longitude_deg"))
                elev0 = _f(a.get("elevation_ft"))
                if lat0 is None or lon0 is None:
                    n_skip += 1
                    continue
                half = length / 2.0
                d_n = half * math.cos(math.radians(hdg)) / FT_PER_DEG_LAT
                d_e = (half * math.sin(math.radians(hdg)) / FT_PER_DEG_LAT
                       / max(math.cos(math.radians(lat0)), 1e-6))
                le_lat, le_lon = lat0 - d_n, lon0 - d_e
                he_lat, he_lon = lat0 + d_n, lon0 + d_e
                le_elev = he_elev = elev0
                n_synth += 1
            else:
                n_skip += 1
                continue

            rwy_id = f"{le}/{he}"
            con.execute(
                "INSERT OR IGNORE INTO runways "
                "(site_no, rwy_id, length_ft, width_ft, surface, lighting) "
                "VALUES (?,?,?,?,?,?)",
                (ident, rwy_id, length, _f(r.get("width_ft")),
                 _norm_surface(r.get("surface")),
                 "HIGH" if r.get("lighted") == "1" else ""))
            for end_id, lat, lon, elev, dthr in (
                    (le, le_lat, le_lon, le_elev, _f(r.get("le_displaced_threshold_ft"))),
                    (he, he_lat, he_lon, he_elev, _f(r.get("he_displaced_threshold_ft")))):
                con.execute(
                    "INSERT OR IGNORE INTO runway_ends "
                    "(site_no, rwy_id, end_id, true_alignment_deg, lat, lon, elev_ft, "
                    "displaced_thr_lat, displaced_thr_lon, displaced_thr_len_ft, "
                    "tdz_elev_ft, marking_type, apch_lgt_code, end_lgts_flag, "
                    "cntrln_lgts_flag, tdz_lgt_flag) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ident, rwy_id, end_id, hdg, lat, lon, elev, None, None,
                     dthr, None, "", "", "", "", ""))
        con.commit()
    finally:
        con.close()
    return {"countries": sorted(countries), "airports": n_ap,
            "runways_direct": n_direct, "runways_synth": n_synth,
            "runways_skipped": n_skip}
