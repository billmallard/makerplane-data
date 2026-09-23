# SPDX-License-Identifier: Apache-2.0
"""packtools.geo -- shared great-circle helper."""

import pytest

from packtools.geo import great_circle_nm


def test_great_circle_nm_same_point_is_zero():
    p = (34.4, -119.8)
    assert great_circle_nm(p, p) == pytest.approx(0.0, abs=1e-9)


def test_great_circle_nm_matches_procedures_rf_arc_fixture():
    # Same two points AER-1700's RF-arc fixture asserts 2.8028 nm for
    # (packtools/build/procedures.py, tests/test_build_procedures.py) --
    # pinned here too so the extraction into packtools/geo.py can't silently
    # change the formula's output.
    centre = (35.08599722, -106.62291667)
    fix = (35.125675, -106.59285556)
    assert great_circle_nm(centre, fix) == pytest.approx(2.8028, abs=1e-3)


def test_great_circle_nm_known_distance_lax_jfk():
    # LAX -> JFK great-circle distance is a widely published ~2144 nm.
    lax = (33.9425, -118.4081)
    jfk = (40.6413, -73.7781)
    assert great_circle_nm(lax, jfk) == pytest.approx(2144, abs=5)
