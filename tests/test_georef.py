# SPDX-License-Identifier: Apache-2.0
"""Per-plate georeferencing derivation (AER-1610 / PA11)."""

import math

import pytest

from packtools.build.georef import (
    ControlPoint,
    GeoreferenceError,
    derive_plate_georef,
    fit_similarity_transform,
)


def test_fit_similarity_transform_recovers_a_known_pure_translation():
    # Two points offset by exactly the same lat/lon delta and the same
    # pixel delta -- a=1 (no scale/rotation), b=0.
    points = [
        ControlPoint("A", lat=40.0, lon=-100.0, px=100.0, py=200.0),
        ControlPoint("B", lat=40.0, lon=-99.9, px=100.0 + 60 * 0.1 * math.cos(math.radians(40.0)), py=200.0),
    ]
    t = fit_similarity_transform(points)
    assert t.a == pytest.approx(1.0, abs=1e-6)
    assert t.b == pytest.approx(0.0, abs=1e-6)


def test_fit_similarity_transform_needs_at_least_two_points():
    with pytest.raises(GeoreferenceError, match="need >=2"):
        fit_similarity_transform([ControlPoint("A", 40.0, -100.0, 0.0, 0.0)])


def test_fit_similarity_transform_rejects_duplicate_points():
    dup = ControlPoint("A", 40.0, -100.0, 5.0, 5.0)
    with pytest.raises(GeoreferenceError, match="degenerate"):
        fit_similarity_transform([dup, dup])


def _synthetic_plate(n_fixes=5, *, scale_px_per_nm=8.0, rotation_deg=0.0,
                      origin_px=(200.0, 300.0), noise_nm=0.0):
    """A fabricated plate: fixes on a straight line east of a fictional
    airport, projected through a known transform, so fit + residual can be
    checked against an exact expected answer. Distinct from the real-plate
    test below, which exercises the actual PDF/CIFP data end to end."""
    import random
    rng = random.Random(1979)
    origin_lat, origin_lon = 40.0, -100.0
    a = scale_px_per_nm * math.cos(math.radians(rotation_deg))
    b = scale_px_per_nm * math.sin(math.radians(rotation_deg))
    control_fixes = {}
    word_positions = {}
    for i in range(n_fixes):
        fid = f"FIX{i}"
        lat = origin_lat + 0.02 * i
        lon = origin_lon - 0.03 * i
        control_fixes[fid] = (lat, lon)
        x_nm = (lon - origin_lon) * 60.0 * math.cos(math.radians(origin_lat))
        y_nm = (lat - origin_lat) * 60.0
        px = a * x_nm - b * y_nm + origin_px[0] + rng.uniform(-noise_nm, noise_nm) * scale_px_per_nm
        py = b * x_nm + a * y_nm + origin_px[1] + rng.uniform(-noise_nm, noise_nm) * scale_px_per_nm
        word_positions[fid] = [(px, py)]
    return control_fixes, word_positions


def test_derive_plate_georef_exact_fit_has_near_zero_residual():
    control_fixes, word_positions = _synthetic_plate(n_fixes=4)
    result = derive_plate_georef(word_positions, control_fixes)
    assert result.status == "ok"
    assert result.control_points == 2
    assert result.residual_nm == pytest.approx(0.0, abs=1e-6)


def test_derive_plate_georef_reports_a_real_measured_residual():
    # A deliberate, known offset injected into exactly one held-out fix's
    # printed position -- proves the residual number is measuring the right
    # thing (that fix's disagreement), not a coincidental zero.
    control_fixes, word_positions = _synthetic_plate(n_fixes=4, scale_px_per_nm=10.0)
    offset_fix = sorted(control_fixes)[3]  # the 3rd held-out point (index 2..3 -> "FIX3")
    px, py = word_positions[offset_fix][0]
    word_positions[offset_fix] = [(px + 20.0, py)]  # 20 px east @ 10 px/nm = 2 nm
    result = derive_plate_georef(word_positions, control_fixes)
    assert result.status == "ok"
    assert result.residual_nm == pytest.approx(2.0, abs=0.05)


def test_derive_plate_georef_ambiguous_fixes_are_excluded_from_the_fit():
    control_fixes, word_positions = _synthetic_plate(n_fixes=3)
    # Give one fix a second occurrence elsewhere on the page -- it must drop
    # out of the candidate pool entirely, not just get picked arbitrarily.
    word_positions["FIX0"].append((9.0, 9.0))
    result = derive_plate_georef(word_positions, control_fixes)
    assert result.status == "insufficient_control_points"
    assert result.control_points == 2  # FIX1, FIX2 only


def test_derive_plate_georef_too_few_unambiguous_fixes():
    control_fixes, word_positions = _synthetic_plate(n_fixes=2)
    result = derive_plate_georef(word_positions, control_fixes)
    assert result.status == "insufficient_control_points"
    assert result.residual_nm is None
    assert "need >=3" in result.detail


def test_derive_plate_georef_missing_fix_on_page_is_not_a_candidate():
    control_fixes, word_positions = _synthetic_plate(n_fixes=3)
    del word_positions["FIX2"]  # printed CIFP fix that never made it onto the page's text layer
    result = derive_plate_georef(word_positions, control_fixes)
    assert result.status == "insufficient_control_points"
    assert result.control_points == 2  # FIX0, FIX1


# --- real data: ADK (PADK) "ILS Y OR LOC Y RWY 23", cycle 2609 -----------
#
# tests/fixtures/dtpp/01244IYLY23.PDF, downloaded live 2026-09-23 from
# https://aeronav.faa.gov/d-tpp/2609/01244IYLY23.PDF (the plate the ADK
# fixture in tests/fixtures/dtpp/metafile_2609_sample.xml catalogs).
# fix coordinates are the real CIFP cycle 2609 values (same live pull,
# packtools.arinc424.build_fix_index against PADK's I23-Y approach); pixel
# positions are the real word-centre positions pymupdf's
# page.get_text("words") returns for this exact PDF, captured once and
# pinned here so this test needs neither pymupdf nor network to run.
_ADK_I23Y_CONTROL_FIXES = {
    "GIDKE": (51.99214722, -176.32959444),
    "SALSE": (51.97878056, -176.25224444),
    "GUISE": (51.95093056, -176.44881944),
    "TICCU": (52.01614444, -176.10220556),
    "LONOK": (52.15180556, -175.54861389),
    "COMAT": (51.80951944, -176.92393333),
}
_ADK_I23Y_WORD_POSITIONS = {
    "GIDKE": [(247.12, 214.0), (226.72, 407.58), (288.56, 185.39)],
    "SALSE": [(303.01, 252.44)],
    "GUISE": [(204.63, 241.45), (166.96, 418.01)],
    "TICCU": [(169.62, 158.45), (357.49, 271.5), (200.92, 139.56), (353.08, 228.39)],
    "LONOK": [(232.83, 168.71)],
    "COMAT": [(36.63, 326.59), (95.87, 399.43)],
}


def test_derive_plate_georef_real_adk_plate_has_only_two_unambiguous_fixes():
    """Documents the real, measured outcome for this pass's method (module
    docstring): on the real live ADK ILS-Y plate, only SALSE and LONOK print
    exactly once -- GIDKE/GUISE/TICCU/COMAT are all ambiguous (2-4 hits: plan
    view + missed-approach text + profile). 2 unambiguous points is enough
    to fit a transform but not to also hold one out, so this real plate
    ships with no geo tag under this pass's unambiguous-only policy -- a
    real negative result, not a hypothetical one."""
    result = derive_plate_georef(_ADK_I23Y_WORD_POSITIONS, _ADK_I23Y_CONTROL_FIXES)
    assert result.status == "insufficient_control_points"
    assert result.control_points == 2
