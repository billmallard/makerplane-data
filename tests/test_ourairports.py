"""OurAirports merge — augment a NASR-built airports.sqlite with foreign
airports, offline (fixture rows, no network)."""
import sqlite3

from packtools import ourairports

# Mirrors the schema build_airport_db.py writes (the columns merge_countries uses).
SCHEMA = """
CREATE TABLE airports (site_no TEXT PRIMARY KEY, icao TEXT NOT NULL, name TEXT,
  lat REAL NOT NULL, lon REAL NOT NULL, elev_ft REAL, mag_var REAL, state TEXT, city TEXT);
CREATE TABLE runways (site_no TEXT, rwy_id TEXT, length_ft REAL, width_ft REAL,
  surface TEXT, lighting TEXT, PRIMARY KEY (site_no, rwy_id));
CREATE TABLE runway_ends (site_no TEXT, rwy_id TEXT, end_id TEXT, true_alignment_deg REAL,
  lat REAL, lon REAL, elev_ft REAL, displaced_thr_lat REAL, displaced_thr_lon REAL,
  displaced_thr_len_ft REAL, tdz_elev_ft REAL, marking_type TEXT, apch_lgt_code TEXT,
  end_lgts_flag TEXT, cntrln_lgts_flag TEXT, tdz_lgt_flag TEXT,
  PRIMARY KEY (site_no, rwy_id, end_id));
"""

AIRPORTS = [
    # CYQG Windsor — kept (medium), has direct thresholds in the runway fixture
    {"ident": "CYQG", "type": "medium_airport", "iso_country": "CA",
     "name": "Windsor", "latitude_deg": "42.2756", "longitude_deg": "-82.9556",
     "elevation_ft": "622", "iso_region": "CA-ON", "municipality": "Windsor"},
    # CSYN small — kept (synth runway: no thresholds, has heading+length)
    {"ident": "CSYN", "type": "small_airport", "iso_country": "CA",
     "name": "Synth Strip", "latitude_deg": "45.0", "longitude_deg": "-75.0",
     "elevation_ft": "300", "iso_region": "CA-ON", "municipality": "Nowhere"},
    # a heliport — must be skipped (not in AIRPORT_TYPES)
    {"ident": "CXH1", "type": "heliport", "iso_country": "CA",
     "name": "Helipad", "latitude_deg": "43.0", "longitude_deg": "-79.0",
     "elevation_ft": "100", "iso_region": "CA-ON", "municipality": "X"},
    # a US airport — must be ignored (wrong country)
    {"ident": "KDET", "type": "medium_airport", "iso_country": "US",
     "name": "Detroit City", "latitude_deg": "42.409", "longitude_deg": "-83.01",
     "elevation_ft": "626", "iso_region": "US-MI", "municipality": "Detroit"},
]

RUNWAYS = [
    {"airport_ident": "CYQG", "closed": "0", "le_ident": "07", "he_ident": "25",
     "length_ft": "9000", "width_ft": "150", "surface": "ASP", "lighted": "1",
     "le_latitude_deg": "42.270", "le_longitude_deg": "-82.970", "le_elevation_ft": "620",
     "he_latitude_deg": "42.281", "he_longitude_deg": "-82.941", "he_elevation_ft": "622",
     "le_heading_degT": "68", "le_displaced_threshold_ft": "200",
     "he_displaced_threshold_ft": "0"},
    {"airport_ident": "CSYN", "closed": "0", "le_ident": "09", "he_ident": "27",
     "length_ft": "3000", "width_ft": "75", "surface": "GRE", "lighted": "0",
     "le_latitude_deg": "", "le_longitude_deg": "", "le_elevation_ft": "",
     "he_latitude_deg": "", "he_longitude_deg": "", "he_elevation_ft": "",
     "le_heading_degT": "90", "le_displaced_threshold_ft": "",
     "he_displaced_threshold_ft": ""},
]


def _db(tmp_path):
    p = tmp_path / "airports.sqlite"
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    # a pre-existing US (NASR) row that must survive untouched
    con.execute("INSERT INTO airports VALUES ('04508.*A','DET','Detroit City',"
                "42.409,-83.01,626,-7.0,'MI','Detroit')")
    con.commit(); con.close()
    return p


def test_merge_adds_country_and_keeps_us(tmp_path):
    p = _db(tmp_path)
    counts = ourairports.merge_countries(p, ("CA",), airports=AIRPORTS, runways=RUNWAYS)
    con = sqlite3.connect(p); con.row_factory = sqlite3.Row
    idents = {r["site_no"] for r in con.execute("SELECT site_no FROM airports")}
    # CA airports added, heliport + foreign-country dropped, US row preserved
    assert {"CYQG", "CSYN", "04508.*A"} <= idents
    assert "CXH1" not in idents and "KDET" not in idents
    assert counts["airports"] == 2

    # direct thresholds used verbatim; surface normalized ASP -> ASPH
    rwy = con.execute("SELECT * FROM runways WHERE site_no='CYQG'").fetchone()
    assert rwy["rwy_id"] == "07/25" and rwy["surface"] == "ASPH" and rwy["lighting"] == "HIGH"
    le = con.execute("SELECT * FROM runway_ends WHERE site_no='CYQG' AND end_id='07'").fetchone()
    assert abs(le["lat"] - 42.270) < 1e-6 and le["displaced_thr_len_ft"] == 200
    assert le["marking_type"] == "" and le["apch_lgt_code"] == ""    # NASR-only fields null

    # synthesized runway has thresholds straddling the airport ref along heading 90
    assert counts["runways_direct"] == 1 and counts["runways_synth"] == 1
    ends = con.execute("SELECT end_id, lat, lon FROM runway_ends WHERE site_no='CSYN' "
                       "ORDER BY end_id").fetchall()
    assert len(ends) == 2
    lons = sorted(e["lon"] for e in ends)
    assert lons[0] < -75.0 < lons[1]            # 09/27 straddles lon -75 east-west
    con.close()


def test_build_country_pack_from_cached_csvs(tmp_path):
    """build_country_pack creates the schema and merges from cached CSVs (no
    network), producing a standalone provider DB."""
    cache = tmp_path / "cache"; cache.mkdir()
    (cache / "ourairports_airports.csv").write_text(
        "ident,type,iso_country,name,latitude_deg,longitude_deg,elevation_ft,"
        "iso_region,municipality\n"
        "CYQG,medium_airport,CA,Windsor,42.2756,-82.9556,622,CA-ON,Windsor\n"
        "KDET,medium_airport,US,Detroit,42.409,-83.01,626,US-MI,Detroit\n",
        encoding="utf-8")
    (cache / "ourairports_runways.csv").write_text(
        "airport_ident,closed,le_ident,he_ident,length_ft,width_ft,surface,lighted,"
        "le_latitude_deg,le_longitude_deg,le_elevation_ft,he_latitude_deg,"
        "he_longitude_deg,he_elevation_ft,le_heading_degT,le_displaced_threshold_ft,"
        "he_displaced_threshold_ft\n"
        "CYQG,0,07,25,9000,150,ASP,1,42.270,-82.970,620,42.281,-82.941,622,68,200,0\n",
        encoding="utf-8")
    out = tmp_path / "airports.sqlite"
    counts = ourairports.build_country_pack(out, ("CA",), cache_dir=cache)
    assert out.exists() and counts["airports"] == 1   # CA only, US ignored
    con = sqlite3.connect(out)
    assert con.execute("SELECT icao FROM airports").fetchone()[0] == "CYQG"
    assert con.execute("SELECT surface FROM runways").fetchone()[0] == "ASPH"
    con.close()


def test_merge_is_idempotent(tmp_path):
    p = _db(tmp_path)
    ourairports.merge_countries(p, ("CA",), airports=AIRPORTS, runways=RUNWAYS)
    ourairports.merge_countries(p, ("CA",), airports=AIRPORTS, runways=RUNWAYS)
    con = sqlite3.connect(p)
    assert con.execute("SELECT COUNT(*) FROM airports").fetchone()[0] == 3   # 2 CA + 1 US, no dupes
    assert con.execute("SELECT COUNT(*) FROM runway_ends WHERE site_no='CYQG'").fetchone()[0] == 2
    con.close()
