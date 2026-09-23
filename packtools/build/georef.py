# SPDX-License-Identifier: Apache-2.0
"""Per-plate georeferencing derivation (PA10 spike -> PA11 build).

``docs/dtpp_plates_spike.md`` §2 proposed the method: cross-reference a
procedure's CIFP-parsed fix coordinates (already built by
``packtools/build/procedures.py``, PA1) against the same fix idents' text-
layer positions on the plate's own vector-PDF page, solve a 2D similarity
transform (uniform scale + rotation + translation) from the matched control
points, and hold at least one back as an independent residual-error check
-- guardrail 5 gates ownship on that measured residual clearing a budget,
not on the transform merely existing.

Scope of this pass, stated plainly rather than buried in a caveat: the
spike flagged that the same fix ident commonly appears 2-3+ times on one
page (plan view, profile view, missed-approach text block) with no
text-only signal for which occurrence is the plan-view one ("Label
ambiguity" / "Orientation is not guaranteed north-up" in the spike).
Picking whichever occurrence best agrees with a transform derived from
that very data is circular, not independent verification -- so this
derivation uses ONLY fix idents that appear exactly once on the page, both
to fit the transform and to hold out as the check point. Page-region
clustering (using plan-view boundaries or vector-graphics symbols to
disambiguate repeats) is real future work, not attempted here.

This is not a rare edge case: measured against the real, live ADK/PADK
"ILS Y OR LOC Y RWY 23" plate (cycle 2609,
``tests/fixtures/dtpp/01244IYLY23.PDF``) cross-referenced against its own
real CIFP fix coordinates, only 2 of the 7 plan-view-relevant fixes on that
plate are unambiguous -- not enough to both fit (needs 2) and hold out a
check point (needs >=1 more), so that real plate legitimately ships with no
geo tag under this pass's method (``tests/test_georef.py``). Guardrail 5's
"ships without a geo tag, does not fail the build" fallback is the common
outcome today, not the exception -- doing better needs the clustering work
above, which is out of scope for this pass.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..geo import great_circle_nm

_NM_PER_DEG_LAT = 60.0

#: 2 to fit the transform + at least 1 independent held-out check.
_MIN_UNAMBIGUOUS = 3


class GeoreferenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ControlPoint:
    fix_id: str
    lat: float
    lon: float
    px: float
    py: float


def _local_nm(lat: float, lon: float, origin_lat: float, origin_lon: float) -> tuple[float, float]:
    """Local tangent-plane offset in nm from (origin_lat, origin_lon) --
    accurate enough over one procedure's small extent (a handful of nm)."""
    x_nm = (lon - origin_lon) * _NM_PER_DEG_LAT * math.cos(math.radians(origin_lat))
    y_nm = (lat - origin_lat) * _NM_PER_DEG_LAT
    return x_nm, y_nm


@dataclass(frozen=True)
class GeoTransform:
    """2D similarity (uniform scale + rotation + translation) from a local
    tangent-plane nm frame centred on (origin_lat, origin_lon) to PDF page
    points: ``px = a*x_nm - b*y_nm + tx``, ``py = b*x_nm + a*y_nm + ty``."""
    a: float
    b: float
    tx: float
    ty: float
    origin_lat: float
    origin_lon: float

    @property
    def scale_px_per_nm(self) -> float:
        return math.hypot(self.a, self.b)

    def project(self, lat: float, lon: float) -> tuple[float, float]:
        x_nm, y_nm = _local_nm(lat, lon, self.origin_lat, self.origin_lon)
        return (self.a * x_nm - self.b * y_nm + self.tx,
                self.b * x_nm + self.a * y_nm + self.ty)

    def unproject(self, px: float, py: float) -> tuple[float, float]:
        """Inverse of :meth:`project`: where on the ground this transform
        says a page point corresponds to -- what the residual check below
        uses to turn "the held-out fix's label sits here" into a (lat, lon)
        comparable against its true CIFP coordinate."""
        scale2 = self.a * self.a + self.b * self.b
        if scale2 == 0:
            raise GeoreferenceError("degenerate transform (zero scale)")
        dx, dy = px - self.tx, py - self.ty
        x_nm = (self.a * dx + self.b * dy) / scale2
        y_nm = (-self.b * dx + self.a * dy) / scale2
        lat = self.origin_lat + y_nm / _NM_PER_DEG_LAT
        lon = self.origin_lon + x_nm / (_NM_PER_DEG_LAT * math.cos(math.radians(self.origin_lat)))
        return lat, lon


def fit_similarity_transform(points: list[ControlPoint]) -> GeoTransform:
    """Least-squares fit of geo (local nm frame) -> PDF pixel space. A
    similarity transform has 4 degrees of freedom (a, b, tx, ty above);
    exactly 2 points solve it exactly, >=3 is a least-squares fit."""
    if len(points) < 2:
        raise GeoreferenceError(
            f"need >=2 control points to solve a similarity transform, got {len(points)}")
    origin_lat = sum(p.lat for p in points) / len(points)
    origin_lon = sum(p.lon for p in points) / len(points)
    # px_i = a*x_i - b*y_i + tx ; py_i = b*x_i + a*y_i + ty -- linear in
    # (a, b, tx, ty), so each control point contributes two rows.
    rows = []
    for p in points:
        x, y = _local_nm(p.lat, p.lon, origin_lat, origin_lon)
        rows.append((x, -y, 1.0, 0.0, p.px))
        rows.append((y, x, 0.0, 1.0, p.py))
    a, b, tx, ty = _solve_least_squares_4(rows)
    return GeoTransform(a=a, b=b, tx=tx, ty=ty, origin_lat=origin_lat, origin_lon=origin_lon)


def _solve_least_squares_4(rows: list[tuple[float, float, float, float, float]]) -> tuple[float, float, float, float]:
    """Normal-equations least squares (A^T A x = A^T b) for a 4-unknown
    linear system. Pure Python -- this repo has no numpy dependency
    anywhere and a 4x4 solve does not need one."""
    ata = [[0.0] * 4 for _ in range(4)]
    atb = [0.0] * 4
    for *coeffs, rhs in rows:
        for i in range(4):
            atb[i] += coeffs[i] * rhs
            for j in range(4):
                ata[i][j] += coeffs[i] * coeffs[j]
    return _solve_linear_4(ata, atb)


def _solve_linear_4(m: list[list[float]], v: list[float]) -> tuple[float, float, float, float]:
    """Gaussian elimination with partial pivoting for a 4x4 system."""
    aug = [row[:] + [v[i]] for i, row in enumerate(m)]
    n = 4
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-9:
            raise GeoreferenceError(
                "control points are degenerate (collinear or duplicated) -- "
                "cannot solve a similarity transform")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        pv = aug[col][col]
        aug[col] = [x / pv for x in aug[col]]
        for r in range(n):
            if r != col:
                factor = aug[r][col]
                aug[r] = [rv - factor * cv for rv, cv in zip(aug[r], aug[col])]
    return tuple(aug[i][n] for i in range(n))  # type: ignore[return-value]


@dataclass(frozen=True)
class GeoResult:
    status: str                              # "ok" | "insufficient_control_points" | "degenerate"
    residual_nm: float | None = None         # worst-case error across held-out check point(s)
    control_points: int = 0                  # unambiguous points used to fit (not the check points)
    transform: GeoTransform | None = None
    detail: str = ""


def derive_plate_georef(word_positions: dict[str, list[tuple[float, float]]],
                        control_fixes: dict[str, tuple[float, float]]) -> GeoResult:
    """Attempt the CIFP-cross-reference derivation for one plate page.

    ``word_positions``: fix ident (as printed) -> list of (x_center,
    y_center) pixel occurrences on the page (from a PDF text-layer extract,
    e.g. pymupdf's ``page.get_text("words")``; the caller reduces each word
    bbox to its centre and groups by text -- kept out of this function so it
    carries no pymupdf/fitz dependency and stays trivially testable).
    ``control_fixes``: fix ident -> (lat, lon), from the CIFP-built
    procedures pack, for every fix this procedure's legs reference.

    Only fix idents present in both inputs with EXACTLY ONE occurrence in
    ``word_positions`` are used -- see the module docstring for why
    ambiguous (repeated) idents are excluded rather than disambiguated.
    """
    unambiguous = [
        ControlPoint(fix_id=fid, lat=lat, lon=lon, px=positions[0][0], py=positions[0][1])
        for fid, (lat, lon) in control_fixes.items()
        for positions in [word_positions.get(fid, [])]
        if len(positions) == 1
    ]
    if len(unambiguous) < _MIN_UNAMBIGUOUS:
        return GeoResult(
            status="insufficient_control_points", control_points=len(unambiguous),
            detail=f"{len(unambiguous)} unambiguous control point(s) found; "
                   f"need >={_MIN_UNAMBIGUOUS} (2 to fit the transform + "
                   f">=1 independent held-out check)")

    # Deterministic ordering so the same plate always derives the same
    # transform: fit from the first 2 (by fix_id), hold out the rest.
    unambiguous.sort(key=lambda c: c.fix_id)
    fit_points, check_points = unambiguous[:2], unambiguous[2:]
    try:
        transform = fit_similarity_transform(fit_points)
    except GeoreferenceError as e:
        return GeoResult(status="degenerate", control_points=len(fit_points), detail=str(e))

    # For each held-out fix: where the transform says its own printed page
    # position corresponds to on the ground, vs. where CIFP says it really
    # is -- the brief's "reproject the held-out check point" (§2.2).
    residuals_nm = [
        great_circle_nm((cp.lat, cp.lon), transform.unproject(cp.px, cp.py))
        for cp in check_points
    ]
    # Worst case, not average or best -- an ownship gate should not be able
    # to pass by cherry-picking whichever held-out check happened to agree.
    residual_nm = max(residuals_nm)
    return GeoResult(status="ok", residual_nm=residual_nm,
                     control_points=len(fit_points), transform=transform,
                     detail=f"checked against {len(check_points)} held-out fix(es)")
