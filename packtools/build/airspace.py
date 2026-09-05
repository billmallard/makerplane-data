# SPDX-License-Identifier: Apache-2.0
"""openAIP airspace builder — static per-country GeoJSON -> sqlite pack.

Static-first by design (AER-547): openAIP's live listing/FAQ pages sit behind
a JS shell that 403s scripted fetches, so there is no verified way yet to
auto-detect when a country's export updates. This builder only turns an
already-downloaded export into a pack; fetching stays a manual/periodic step
until that changes (see docs/openaip_evaluation.md "Refresh cadence").

Schema follows the shape confirmed live against openAIP's own OpenAPI schema
(api.core.openaip.net/api/schemas/response/airspace/airspace-schema.json,
2026-08-23; see docs/openaip_evaluation.md): each feature carries an id,
name, type/icaoClass enums, ISO alpha-2 country, lower/upper limits (each
either a bare value or a ``{value, unit, referenceDatum}`` object), and a
Polygon geometry. The full ``properties`` object is also kept verbatim as
JSON so nothing is lost if openAIP's schema grows fields this builder
doesn't know about yet.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE airspaces (
    id                     TEXT PRIMARY KEY,
    name                   TEXT,
    type                   TEXT,
    icao_class             TEXT,
    country                TEXT NOT NULL,
    lower_limit            TEXT,
    lower_limit_unit       TEXT,
    lower_limit_reference  TEXT,
    upper_limit            TEXT,
    upper_limit_unit       TEXT,
    upper_limit_reference  TEXT,
    activity               TEXT,
    remarks                TEXT,
    geometry               TEXT NOT NULL,
    source_updated_at      TEXT,
    properties_json        TEXT NOT NULL
)
"""

_COLUMNS = 16  # positional values a row of the schema above takes


class AirspaceBuildError(RuntimeError):
    pass


def _limit(value) -> tuple[str | None, str | None, str | None]:
    """Normalize an openAIP altitude limit to (value, unit, reference).

    openAIP represents a limit either as a structured object
    (``{"value": 3500, "unit": "FT", "referenceDatum": "MSL"}``) or a bare
    scalar (e.g. ``"SFC"``). Treat the shape as read-only upstream data we
    don't control rather than assuming one form."""
    if value is None:
        return None, None, None
    if isinstance(value, dict):
        v = value.get("value")
        return (
            None if v is None else str(v),
            value.get("unit"),
            value.get("referenceDatum", value.get("reference")),
        )
    return str(value), None, None


def _feature_row(feature: dict) -> tuple:
    props = feature.get("properties") or {}
    geometry = feature.get("geometry")
    fid = props.get("_id") or props.get("id")
    if not fid:
        raise AirspaceBuildError("feature is missing a stable id (_id/id)")
    if geometry is None:
        raise AirspaceBuildError(f"feature {fid!r} has no geometry")
    country = props.get("country")
    if not country:
        raise AirspaceBuildError(f"feature {fid!r} has no properties.country")
    lower_v, lower_u, lower_r = _limit(props.get("lowerLimit"))
    upper_v, upper_u, upper_r = _limit(props.get("upperLimit"))
    return (
        str(fid),
        props.get("name"),
        None if props.get("type") is None else str(props.get("type")),
        None if props.get("icaoClass") is None else str(props.get("icaoClass")),
        str(country).upper(),
        lower_v, lower_u, lower_r,
        upper_v, upper_u, upper_r,
        None if props.get("activity") is None else str(props.get("activity")),
        props.get("remarks"),
        json.dumps(geometry, separators=(",", ":")),
        props.get("updatedAt"),
        json.dumps(props, separators=(",", ":"), sort_keys=True),
    )


def build_airspace(input_dir: str | Path, out_path: str | Path) -> Path:
    """Build one sqlite airspace pack from openAIP static GeoJSON export(s).

    ``input_dir`` holds one or more ``*.geojson`` FeatureCollection files (a
    single country's static export; a fetch that was split into parts lands
    here as multiple files, all folded into one pack). Matches the
    ``Builder = Callable[[Path, Path], Path]`` shape every other entry in
    :data:`packtools.build.BUILDERS` follows.
    """
    input_dir = Path(input_dir)
    out_path = Path(out_path)
    files = sorted(input_dir.glob("*.geojson"))
    if not files:
        raise AirspaceBuildError(f"no *.geojson files found under {input_dir}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    con = sqlite3.connect(str(out_path))
    try:
        con.execute(_SCHEMA)
        placeholders = ",".join("?" * _COLUMNS)
        n = 0
        for f in files:
            data = json.loads(f.read_text())
            features = data.get("features")
            if features is None:
                raise AirspaceBuildError(f"{f} is not a GeoJSON FeatureCollection")
            rows = [_feature_row(feat) for feat in features]
            con.executemany(f"INSERT INTO airspaces VALUES ({placeholders})", rows)
            n += len(rows)
        con.commit()
    finally:
        con.close()
    if n == 0:
        raise AirspaceBuildError(f"no airspace features found under {input_dir}")
    return out_path
