# SPDX-License-Identifier: Apache-2.0
"""Differential: a built CIFP airspace pack against the FAA's ADDS open data.

ADDS (adds-faa.opendata.arcgis.com) publishes Class B/C/D/E airspace and
special-use airspace as sector polygons with their limits. It is built from
NASR, not CIFP: the same agency, a different product and pipeline, so it
catches parser and densification errors here, not errors the two FAA
products share. (AER-4445.)

For each pack row it looks for ADDS sectors of the same airspace with the
same floor and ceiling and keeps the best match by area overlap. Per row:
intersection-over-union (IoU) and the Hausdorff distance between the two
outlines, in NM, on a local flat projection about the row's centroid.

    python3 scripts/airspace_adds_diff.py PACK --cache DIR --out DIR [--plot ID ...]

Needs ``shapely`` (and ``matplotlib`` for ``--plot``); neither is a
dependency of packtools. Fetches into ``--cache`` once and reuses it.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import statistics
import time
import urllib.parse
import urllib.request
from pathlib import Path

import shapely
from shapely.geometry import shape
from shapely.ops import unary_union

_BASE = "https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/arcgis/rest/services"
LAYERS = {
    "class": ("Class_Airspace", "LOCAL_TYPE IN ('CLASS_B','CLASS_C','CLASS_D')"),
    "sua": ("Special_Use_Airspace", "1=1"),
}
_PAGE = 200


def fetch_layer(name: str, cache: Path) -> list[dict]:
    out = cache / f"adds_{name}.geojson"
    if out.exists():
        return json.loads(out.read_text())["features"]
    service, where = LAYERS[name]
    feats, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"where": where, "outFields": "*", "f": "geojson",
                                    "outSR": 4326, "resultOffset": offset,
                                    "resultRecordCount": _PAGE, "orderByFields": "OBJECTID"})
        with urllib.request.urlopen(f"{_BASE}/{service}/FeatureServer/0/query?{q}",
                                    timeout=120) as r:
            page = json.load(r)
        got = page.get("features") or []
        feats += got
        if len(got) < _PAGE:
            break
        offset += _PAGE
        time.sleep(0.2)
    cache.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return feats


def _pack_limit(value, unit, ref):
    """A pack limit as (feet, "SFC"|"AGL"|"MSL"|"UNL") in ADDS's terms."""
    if value == "UNLTD":
        return (None, "UNL")
    v = int(value)
    if unit == "6":
        return (v * 100, "MSL")
    if ref == "0":
        return (v, "SFC" if v == 0 else "AGL")
    return (v, "MSL")


def _adds_limit(p: dict, side: str):
    """ADDS limit -> the same shape. ADDS writes "x ft above ground" as
    value x with code SFC; unlimited as -9998 or UNLTD."""
    val, code, uom = p.get(f"{side}_VAL"), p.get(f"{side}_CODE"), p.get(f"{side}_UOM")
    if val in (None, "") or str(val) in ("-9998", "UNLTD"):
        return (None, "UNL")
    v = int(float(val))
    if uom == "FL":
        v, code = v * 100, "MSL"
    if code in ("SFC", "AGL"):
        return (v, "SFC" if v == 0 else "AGL")
    if code == "STD":
        code = "MSL"
    return (v, code or "MSL")


def _local(geom, lat0, lon0):
    k = math.cos(math.radians(lat0))
    return shapely.transform(geom, lambda xy: (xy - (lon0, lat0)) * (60 * k, 60))


def _same_limit(a, b) -> bool:
    """CIFP writes "up to but not including 3,000" as 2999 and "above
    1,000" as 1001; ADDS writes 3000 and 1000. Equal within 1 ft."""
    if a[1] != b[1]:
        return False
    return a[0] == b[0] or (a[0] is not None and b[0] is not None and abs(a[0] - b[0]) <= 1)


def _ur_key(name: str) -> str:
    return "".join(ch for ch in (name or "").upper() if ch.isalnum())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pack")
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--plot", nargs="*", default=[], help="row ids to draw against ADDS")
    a = ap.parse_args(argv)

    by_class: dict[tuple, list] = {}
    for f in fetch_layer("class", a.cache):
        p = f["properties"]
        if f.get("geometry"):
            by_class.setdefault((p.get("ICAO_ID") or p.get("IDENT"), p.get("CLASS")),
                                []).append(f)
    by_sua: dict[str, list] = {}
    for f in fetch_layer("sua", a.cache):
        if f.get("geometry"):
            by_sua.setdefault(_ur_key(f["properties"].get("NAME")), []).append(f)

    con = sqlite3.connect(f"file:{a.pack}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT id, name, icao_class, lower_limit, lower_limit_unit, lower_limit_reference, "
        "upper_limit, upper_limit_unit, upper_limit_reference, geometry, properties_json "
        "FROM airspaces ORDER BY id").fetchall()
    cls_letter = {"1": "B", "2": "C", "3": "D"}
    results, plots = [], {}
    for (rid, name, icls, lv, lu, lr, uv, uu, ur, geom, props) in rows:
        pr = json.loads(props)
        mine = shape(json.loads(geom))
        lo, hi = _pack_limit(lv, lu, lr), _pack_limit(uv, uu, ur)
        if pr["section"] == "UC":
            ident = pr["designation"]
            cands = by_class.get((ident, cls_letter[icls]), [])
            if not cands and ident.startswith("K"):
                cands = by_class.get((ident[1:], cls_letter[icls]), [])
        else:
            cands = by_sua.get(_ur_key(name), [])
        same = [f for f in cands
                if _same_limit(_adds_limit(f["properties"], "LOWER"), lo)
                and _same_limit(_adds_limit(f["properties"], "UPPER"), hi)]
        c = mine.centroid
        m = _local(mine, c.y, c.x)
        best = None
        local = []
        for f in same:
            theirs = shape(f["geometry"])
            t = _local(theirs, c.y, c.x)
            if not t.is_valid:
                t = t.buffer(0)
            local.append((f, theirs, t))
        # One ADDS sector, or the union of every same-limit ADDS sector that
        # overlaps this row (ADDS sometimes splits one CIFP sector in two).
        trials = [(f, theirs, t) for f, theirs, t in local]
        touching = [x for x in local if x[2].intersects(m)]
        if len(touching) > 1:
            trials.append((touching[0][0], unary_union([x[1] for x in touching]),
                           unary_union([x[2] for x in touching])))
        for f, theirs, t in trials:
            union = m.union(t).area
            iou = m.intersection(t).area / union if union else 0.0
            if best is None or iou > best[0]:
                best = (iou, m.hausdorff_distance(t), f, theirs)
        status = ("no-adds-airspace" if not cands else "no-limit-match" if not same
                  else "matched")
        results.append({
            "id": rid, "name": name, "section": pr["section"], "status": status,
            "lower": f"{lo[0]} {lo[1]}", "upper": f"{hi[0]} {hi[1]}",
            "candidates": len(cands), "same_limits": len(same),
            "iou": round(best[0], 4) if best else "",
            "hausdorff_nm": round(best[1], 3) if best else "",
            "adds_name": best[2]["properties"].get("NAME") if best else "",
            "adds_objectid": best[2]["properties"].get("OBJECTID") if best else "",
        })
        if rid in a.plot:
            plots[rid] = (mine, best[3] if best else None, name, results[-1])

    a.out.mkdir(parents=True, exist_ok=True)
    with open(a.out / "airspace_adds_diff.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(results[0]))
        w.writeheader()
        w.writerows(results)
    summary = {}
    for sec in ("UC", "UR"):
        rs = [r for r in results if r["section"] == sec]
        ious = sorted(r["iou"] for r in rs if r["status"] == "matched")
        hd = sorted(r["hausdorff_nm"] for r in rs if r["status"] == "matched")
        summary[sec] = {
            "rows": len(rs),
            **{s: sum(1 for r in rs if r["status"] == s)
               for s in ("matched", "no-limit-match", "no-adds-airspace")},
            "iou_median": statistics.median(ious) if ious else None,
            "iou_p05": ious[len(ious) // 20] if ious else None,
            "iou_ge_0.99": sum(1 for x in ious if x >= 0.99),
            "iou_ge_0.95": sum(1 for x in ious if x >= 0.95),
            "hausdorff_nm_median": statistics.median(hd) if hd else None,
            "hausdorff_nm_p95": hd[int(len(hd) * 0.95)] if hd else None,
        }
    (a.out / "airspace_adds_diff_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))

    if plots:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        for rid, (mine, theirs, name, r) in plots.items():
            fig, ax = plt.subplots(figsize=(6, 6), dpi=110)
            if theirs is not None:
                for g in getattr(theirs, "geoms", [theirs]):  # one sector or a union
                    ax.plot(*g.exterior.xy, color="#c2410c", lw=3.0, alpha=0.55,
                            label="FAA ADDS (NASR)")
            ax.plot(*mine.exterior.xy, color="#1d4ed8", lw=1.0, label="this pack (CIFP)")
            ax.set_aspect(1 / math.cos(math.radians(mine.centroid.y)))
            ax.set_title(f"{rid}  {name}\nfloor {r['lower']}, ceiling {r['upper']}; "
                         f"IoU {r['iou']}, Hausdorff {r['hausdorff_nm']} NM", fontsize=9)
            ax.set_xlabel("longitude")
            ax.tick_params(axis="x", labelrotation=30)
            ax.set_ylabel("latitude")
            h, lab = ax.get_legend_handles_labels()
            ax.legend(dict(zip(lab, h)).values(), dict(zip(lab, h)).keys(), fontsize=8,
                      loc="best")
            fig.tight_layout()
            fig.savefig(a.out / f"{rid}.png")
            plt.close(fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
