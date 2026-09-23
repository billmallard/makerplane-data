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
