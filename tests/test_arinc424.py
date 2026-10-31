# SPDX-License-Identifier: Apache-2.0
"""Permissive ARINC 424 (CIFP) parser (AER-1600 / PA1).

Field-decode unit tests plus assertions against the committed golden
fixture (tests/fixtures/cifp/FAACIFP18, real 2609-cycle records --
see the README next to it)."""

from pathlib import Path

import pytest

from packtools import arinc424 as a

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cifp" / "FAACIFP18"


# --- field decode helpers --------------------------------------------------

def test_parse_latlon():
    # KSBA "GOLET" enroute waypoint, verified against the real record.
    assert a._parse_latlon("N34165286", "W119515175") == (34.28135, -119.864375)


def test_parse_latlon_rejects_malformed():
    assert a._parse_latlon("", "") is None
    assert a._parse_latlon("N341652", "W119515175") is None


def test_decode_altitude_plain_feet():
    assert a._decode_altitude("00413") == 413
    assert a._decode_altitude("18000") == 18000
    assert a._decode_altitude("     ") is None


def test_decode_altitude_flight_level():
    assert a._decode_altitude("FL180") == 18000


def test_decode_altitude_unknn_is_permissive_not_fatal():
    # Real Bahamas-region airway records carry the literal "UNKNN".
    assert a._decode_altitude("UNKNN") is None


def test_decode_tenths():
    assert a._decode_tenths("0750") == 75.0
    assert a._decode_tenths("    ") is None


def test_decode_dist_or_time_distance():
    assert a._decode_dist_or_time("0055") == (5.5, None)


def test_decode_dist_or_time_holding_time():
    # "T010" = holding leg, 1.0 minute (real KSBA I07 missed-approach hold).
    assert a._decode_dist_or_time("T010") == (None, 1.0)


def test_decode_rnp():
    assert a._decode_rnp("010") == 0.10
    assert a._decode_rnp("   ") is None


def test_decode_wdc_flags_faf_map_and_first_missed_leg():
    # KABQ H21-Y common route: KAGNE (FAF), RW21 (flyover + MAP), HUMKU
    # (first leg of the missed) -- real WDC values from the fixture.
    assert a._decode_wdc_flags("E  F", "IF") == a.FLAG_FAF
    assert (a._decode_wdc_flags("GY M", "TF")
            == a.FLAG_FLYOVER | a.FLAG_MAP)
    assert a._decode_wdc_flags("E M ", "TF") == a.FLAG_FIRST_MISSED_LEG


def test_decode_wdc_flags_hold_from_path_term():
    assert a._decode_wdc_flags("EE  ", "HM") == a.FLAG_HOLD


def test_fix_type():
    assert a._fix_type("D", "") == "vor"
    assert a._fix_type("D", "B") == "ndb"
    assert a._fix_type("E", "A") == "waypoint"
    assert a._fix_type("P", "C") == "waypoint"
    assert a._fix_type("P", "G") == "runway"
    assert a._fix_type(None, None) is None


def test_transition_role():
    assert a.transition_role("sid", "1", "RW07 ") == "runway"
    assert a.transition_role("sid", "3", "GVO  ") == "enroute"
    assert a.transition_role("approach", "I", None) == "common"


# --- fix index: the airport-scoping bug (regression) -----------------------

def test_fix_index_disambiguates_airport_scoped_idents_by_airport():
    """Dozens of different airports in FAA region "K2" have a runway ident
    "RW07"; resolving by ICAO region alone silently returns someone else's
    runway (verified against the live file during development: KSBA's
    RW07 resolved to a Utah coordinate before this was fixed). The index
    key for airport-scoped fixes must be the owning airport, not the
    region code."""
    idx = a.build_fix_index(FIXTURE)
    ksba_rw07 = idx[("RW07", "KSBA", "P", "G")]
    kabq_rw21 = idx[("RW21", "KABQ", "P", "G")]
    # Real published coordinates -- Santa Barbara vs. Albuquerque, nowhere
    # near each other; a scoping bug collapses these to the same value.
    assert ksba_rw07 == pytest.approx((34.4275, -119.85464167))
    assert kabq_rw21 == pytest.approx((35.04174167, -106.60707222))


def test_fix_index_area_scoped_by_icao_region():
    idx = a.build_fix_index(FIXTURE)
    assert idx[("GVO", "K2", "D", "")] == pytest.approx((34.53131944, -120.09108889))


# --- golden fixture: airways -----------------------------------------------

def test_iter_airway_legs_a315():
    idx = a.build_fix_index(FIXTURE)
    legs = [l for l in a.iter_airway_legs(FIXTURE, idx) if l.route_ident == "A315"]
    assert [l.seq for l in legs] == [100, 110, 120, 130, 140, 150, 160, 170, 180, 190, 200]
    first = legs[0]
    assert first.fix_id == "ZBV"
    assert first.fix_type == "vor"
    assert first.fix_lat is not None and first.fix_lon is not None
    assert first.min_alt_ft == 5000
    assert first.max_alt_ft == 60000


def test_iter_airway_legs_all_fixes_resolve():
    idx = a.build_fix_index(FIXTURE)
    legs = list(a.iter_airway_legs(FIXTURE, idx))
    assert len(legs) == 17
    assert all(l.fix_lat is not None and l.fix_lon is not None for l in legs)


def test_iter_airway_legs_carries_area_code():
    # AER-1979: the ER stream carries US, Canadian, Pacific and Latin
    # American legs in one interleaved sequence, distinguished only by
    # Customer/Area Code -- the parser must not drop it.
    idx = a.build_fix_index(FIXTURE)
    legs = [l for l in a.iter_airway_legs(FIXTURE, idx) if l.route_ident == "A315"]
    assert legs and all(l.area == "USA" for l in legs)


# --- golden fixture: procedures ---------------------------------------------

def test_iter_procedure_legs_sid_has_vector_legs():
    idx = a.build_fix_index(FIXTURE)
    legs = [l for l in a.iter_procedure_legs(FIXTURE, idx)
            if l.airport == "KSBA" and l.proc_ident == "FLOUT5"]
    va_legs = [l for l in legs if l.path_term == "VA"]
    assert len(va_legs) == 3  # RW07, RW15B, RW25 runway transitions
    for leg in va_legs:
        assert leg.fix_id is None          # a vector leg has no fix
        assert leg.course is not None      # but does have a heading


def test_iter_procedure_legs_ils_faf_and_map_flags():
    idx = a.build_fix_index(FIXTURE)
    legs = {(l.transition, l.seq): l for l in a.iter_procedure_legs(FIXTURE, idx)
            if l.airport == "KSBA" and l.proc_ident == "I07"}
    faf = legs[(None, 20)]
    assert faf.fix_id == "NAPPS"
    assert faf.flags & a.FLAG_FAF
    map_leg = legs[(None, 30)]
    assert map_leg.fix_id == "RW07"
    assert map_leg.fix_type == "runway"
    assert map_leg.flags & a.FLAG_MAP
    assert map_leg.flags & a.FLAG_FLYOVER


def test_iter_procedure_legs_rnav_has_rf_legs_with_geometry():
    idx = a.build_fix_index(FIXTURE)
    legs = [l for l in a.iter_procedure_legs(FIXTURE, idx)
            if l.airport == "KABQ" and l.proc_ident == "H21-Y"]
    rf_legs = [l for l in legs if l.path_term == "RF"]
    assert len(rf_legs) >= 3
    for leg in rf_legs:
        assert leg.fix_lat is not None and leg.fix_lon is not None
        assert leg.rnp is not None
        assert leg.centre_fix is not None
        assert leg.centre_lat is not None and leg.centre_lon is not None


def test_iter_procedure_legs_rf_centre_resolves_by_airport():
    # KABQ H21-Y's RF legs cite centre CFDXH/CFDXG/CFDXF, all "PC" terminal
    # waypoints -- airport-scoped idents, same as any other terminal fix
    # (see module docstring). Real coordinates from the cycle-2609 fixture.
    idx = a.build_fix_index(FIXTURE)
    legs = {(l.transition, l.seq): l for l in a.iter_procedure_legs(FIXTURE, idx)
            if l.airport == "KABQ" and l.proc_ident == "H21-Y"}
    leg = legs[("FOXRR", 30)]
    assert leg.path_term == "RF"
    assert leg.centre_fix == "CFDXH"
    assert leg.centre_lat == pytest.approx(35.08599722, abs=1e-6)
    assert leg.centre_lon == pytest.approx(-106.62291667, abs=1e-6)


def test_iter_procedure_legs_af_legs_unchanged_by_centre_fix():
    # AF (DME arc) legs carry recd_navaid/theta/rho, not a Center Fix --
    # cols 107-116 are blank on a real AF record, and must stay unresolved
    # rather than accidentally picking up a neighbouring field.
    idx = a.build_fix_index(FIXTURE)
    legs = [l for l in a.iter_procedure_legs(FIXTURE, idx)
            if l.airport == "09J" and l.proc_ident == "VOR-A"]
    af_legs = [l for l in legs if l.path_term == "AF"]
    assert len(af_legs) >= 1
    for leg in af_legs:
        assert leg.recd_navaid is not None
        assert leg.theta is not None and leg.rho is not None
        assert leg.centre_fix is None
        assert leg.centre_lat is None and leg.centre_lon is None


def test_iter_procedure_legs_vor_has_procedure_turn_and_arc():
    idx = a.build_fix_index(FIXTURE)
    legs = [l for l in a.iter_procedure_legs(FIXTURE, idx)
            if l.airport == "09J" and l.proc_ident == "VOR-A"]
    path_terms = {l.path_term for l in legs}
    assert "PI" in path_terms   # procedure turn
    assert "AF" in path_terms   # DME arc


def test_iter_procedure_legs_all_fixes_resolve():
    idx = a.build_fix_index(FIXTURE)
    legs = list(a.iter_procedure_legs(FIXTURE, idx))
    assert len(legs) == 76
    unresolved = [l for l in legs if l.fix_id and l.fix_lat is None]
    assert unresolved == []


def test_file_cycle():
    assert a.file_cycle(FIXTURE) == "2609"


# --- fix families added for MSA centres (AER-4446, 2611 records) ------------

def test_fix_index_airport_reference_point():
    # KSBA's PA record: the airport is its own fix, keyed by itself.
    idx = a.build_fix_index(FIXTURE)
    assert idx[("KSBA", "KSBA", "P", "A")] == (34.42619167, -119.84149167)


def test_fix_index_terminal_ndb_subsection_sits_in_column_6():
    # KABR's "AB" (RENEY) PN record: subsection at [5:6], not [12:13].
    idx = a.build_fix_index(FIXTURE)
    assert idx[("AB", "KABR", "P", "N")] == (45.38610556, -98.32851111)


def test_fix_index_dme_only_navaid_uses_its_dme_position():
    # MSP has no VOR position on its D record, only a DME one.
    idx = a.build_fix_index(FIXTURE)
    assert idx[("MSP", "K3", "D", "")] == (44.8965, -93.23653056)


def test_fix_index_prefers_the_vor_position_when_both_are_present():
    # GVO's VOR and DME positions are the same point; RZS's are too. A
    # navaid with both keeps [32:51], so no existing key moves.
    idx = a.build_fix_index(FIXTURE)
    assert idx[("GVO", "K2", "D", "")] == (34.53131944, -120.09108889)


# --- MSA (PS) records (AER-4446) ---------------------------------------------

def _msa():
    return {(m.airport, m.centre_ident, m.multiple_code): m
            for m in a.iter_msa(FIXTURE, a.build_fix_index(FIXTURE))}


def _sectors(m):
    return [(s.bearing_start, s.bearing_end, s.altitude_ft, s.radius_nm) for s in m.sectors]


def test_msa_multi_sector_matches_the_ksba_ils_rwy_7_plate():
    # The plate's "MSA RZS 25 NM" circle: 030-120 5900, 120-210 8100,
    # 210-300 8800, 300-030 5200 (bearings to the VORTAC, magnetic).
    m = _msa()[("KSBA", "RZS", None)]
    assert _sectors(m) == [(30, 120, 5900, 25), (120, 210, 8100, 25),
                           (210, 300, 8800, 25), (300, 30, 5200, 25)]
    assert (m.mag_true, m.centre_type) == ("M", "vor")
    assert (m.centre_lat, m.centre_lon) == (34.50953333, -119.77098889)


def test_msa_single_sector_matches_the_ksba_rnav_rwy_7_plate():
    # "MSA RW07 25 NM", 8100 all round: one sector coded start == end.
    m = _msa()[("KSBA", "RW07", None)]
    assert _sectors(m) == [(180, 180, 8100, 25)]
    assert m.centre_type == "runway" and m.centre_lat is not None


def test_msa_centre_types_resolve_against_their_own_airport_or_region():
    msa = _msa()
    assert msa[("KSBA", "KSBA", None)].centre_type == "airport"
    assert (msa[("KSBA", "KSBA", None)].centre_lat,
            msa[("KSBA", "KSBA", None)].centre_lon) == (34.42619167, -119.84149167)
    assert msa[("KABR", "AB", None)].centre_type == "ndb"
    assert msa[("KABR", "AB", None)].centre_lat == 45.38610556
    assert msa[("KMSP", "MSP", None)].centre_lat == 44.8965


def test_msa_multiple_code_keeps_two_msas_on_one_centre_apart():
    msa = _msa()
    assert _sectors(msa[("KABQ", "ABQ", None)])[0] == (20, 200, 9100, 25)
    assert _sectors(msa[("KABQ", "ABQ", "A")])[0] == (20, 200, 9000, 25)


def test_msa_alaska_is_coded_under_the_can_area_and_is_kept():
    m = _msa()[("PABE", "BET", None)]
    assert m.area == "CAN" and _sectors(m) == [(270, 360, 2200, 25), (360, 270, 1500, 25)]


def test_msa_centre_the_file_does_not_define_stays_null():
    # KHRJ's MSA names NDB HQT, which has no record in the 2611 file.
    m = _msa()[("KHRJ", "HQT", None)]
    assert (m.centre_lat, m.centre_lon) == (None, None)
    assert _sectors(m) == [(180, 180, 3400, 25)]


def test_msa_fields_cover_every_nonblank_column():
    # Lossless: every character a PS record carries lands in a named field.
    covered = set()
    for s in a._MSA.values():
        covered |= set(range(s.start, s.stop))
    for line in a.read_records(FIXTURE):
        if line[4] == "P" and line[12] == "S":
            assert all(ch == " " or i in covered for i, ch in enumerate(line)), line
    m = _msa()[("KSBA", "RZS", None)]
    assert m.fields["sectors"].startswith("03012005925") and m.fields["cycle"] == "2502"


def test_msa_continuation_records_are_kept_verbatim(tmp_path):
    lines = [l for l in a.read_records(FIXTURE) if l[4] == "P" and l[12] == "S"][:1]
    cont = lines[0][:38] + "2" + "X" * 93
    f = tmp_path / "FAACIFP18"
    f.write_text("\n".join([lines[0], cont]) + "\n", encoding="latin-1")
    (m,) = list(a.iter_msa(f, {}))
    assert m.continuations == [cont]
