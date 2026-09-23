# SPDX-License-Identifier: Apache-2.0
"""Small shared geodesy helpers with no other home.

Kept deliberately tiny -- this is not a geodesy library, just the one
formula more than one build module needs (RF-arc radius in
build/procedures.py, plate georeferencing residual in build/georef.py) and
should not each carry their own copy of.
"""

from __future__ import annotations

import math

EARTH_RADIUS_NM = 3440.065


def great_circle_nm(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Great-circle distance in nautical miles between two (lat, lon) points
    in decimal degrees (haversine)."""
    lat1, lon1, lat2, lon2 = (math.radians(v) for v in (*p1, *p2))
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return EARTH_RADIUS_NM * 2 * math.asin(min(1.0, math.sqrt(a)))
