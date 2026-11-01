# SPDX-License-Identifier: Apache-2.0
"""Per-state diff maps for OSM packs (catalogue v2, TV7).

Renders, from two records' per-state metrics (``OSM-002`` feature counts
or ``OSM-006`` lengths/areas), an SVG map of each state or province
coloured by its change against the previous edition, with a table of the
same numbers beside it. California at zero roads (makerplane-data#17)
shows up as one dark-red state reading -100%.

Pure function of the two records plus packtools/data/admin1_na.json, so
the report site renders it on each edition's page and ``packtool diffmap``
writes the same picture to a file. Stdlib only.

Colour: one diverging blue/red ramp with a neutral grey midpoint (losses
red, gains blue); a state with nothing to compare is drawn hatched. Every
state carries a direct label and a ``<title>`` tooltip, and the table
repeats the numbers, so nothing is read from colour alone.
"""

from __future__ import annotations

import html
import math

from . import admin1

#: (upper bound of the change, fill, legend label), losses to gains.
BINS = [
    (-0.5, "#b8302f", "down 50% or more"),
    (-0.1, "#f0a3a2", "down 10% to 50%"),
    (0.1, "#f0efec", "within 10%"),
    (0.5, "#9ec5f4", "up 10% to 50%"),
    (math.inf, "#256abf", "up 50% or more"),
]
NO_DATA = "no previous value"
INK, MUTED, EDGE = "#1c2430", "#5d6675", "#ffffff"
#: Fills dark enough that their labels are drawn in white.
DARK = {BINS[0][1], BINS[-1][1]}
#: States holding less than this share of the largest state's value (both
#: editions) are drawn but do not set the map's extent: the CONUS packs
#: carry a few hundred border-slice features in Canada and Mexico, which
#: would otherwise stretch the map over a continent.
EXTENT_SHARE = 0.01
ATTRIBUTION = ("Map data (c) OpenStreetMap contributors, ODbL. "
               "Boundaries: Natural Earth (public domain), simplified.")


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def changes(cur: dict, prev: dict, table: str) -> dict[str, tuple]:
    """``{state: (old, new, fraction or None)}`` for one table of two
    per-state metrics (``{table: {state: number}}``). OUTSIDE is dropped."""
    a = (prev or {}).get(table) or {}
    b = (cur or {}).get(table) or {}
    out = {}
    for st in sorted(set(a) | set(b)):
        if st == admin1.OUTSIDE:
            continue
        old, new = a.get(st), b.get(st, 0)
        out[st] = (old, new, (new - old) / old if old else None)
    return out


def _fill(frac):
    if frac is None:
        return "url(#nodata)"
    for top, colour, _ in BINS:
        if frac < top:
            return colour
    return BINS[-1][1]


def _pct(frac) -> str:
    return "n/a" if frac is None else f"{frac * 100:+.0f}%"


def render(cur: dict, prev: dict, table: str, *, unit: str = "features",
           title: str = "", width: int = 900) -> str:
    """The SVG (a ``<figure>`` with the map, legend and a table). ``cur``
    and ``prev`` are the two records' ``metrics[<check id>]``."""
    ch = changes(cur, prev, table)
    data = admin1.load()
    regions = [r for r in data["regions"] if r["code"] in ch]
    if not regions:
        return ""
    big = max(max(o or 0, n) for o, n, _ in ch.values())
    framed = [r for r in regions
              if max(ch[r["code"]][0] or 0, ch[r["code"]][1]) >= EXTENT_SHARE * big] or regions
    x0 = min(r["bbox"][0] for r in framed)
    y0 = min(r["bbox"][1] for r in framed)
    x1 = max(r["bbox"][2] for r in framed)
    y1 = max(r["bbox"][3] for r in framed)
    k = math.cos(math.radians((y0 + y1) / 2))
    scale = width / ((x1 - x0) * k)
    height = int((y1 - y0) * scale) + 1

    def pt(lon, lat):
        return f"{(lon - x0) * k * scale:.1f},{(y1 - lat) * scale:.1f}"

    shapes, labels = [], []
    for r in regions:
        old, new, frac = ch[r["code"]]
        d = " ".join("M" + " L".join(pt(x, y) for x, y in ring) + "Z" for ring in r["rings"])
        tip = (f"{r['name']}: {_pct(frac)} ({'-' if old is None else f'{old:,.0f}'} -> "
               f"{new:,.0f} {unit})")
        shapes.append(f'<path d="{d}" fill="{_fill(frac)}" stroke="{EDGE}" stroke-width="1" '
                      f'fill-rule="evenodd"><title>{_e(tip)}</title></path>')
        # Label at the centre of the region's largest ring's bounding box.
        ring = max(r["rings"], key=len)
        cx = (min(x for x, _ in ring) + max(x for x, _ in ring)) / 2
        cy = (min(y for _, y in ring) + max(y for _, y in ring)) / 2
        lx, ly = pt(cx, cy).split(",")
        w = (max(x for x, _ in ring) - min(x for x, _ in ring)) * k * scale
        h = (max(y for _, y in ring) - min(y for _, y in ring)) * scale
        inside = 20 < float(lx) < width - 20 and 14 < float(ly) < height - 14
        if w > 40 and h > 28 and inside:
            ink = EDGE if _fill(frac) in DARK else INK
            labels.append(f'<text x="{lx}" y="{ly}" text-anchor="middle" font-size="10" '
                          f'fill="{ink}">{_e(r["code"].split("-")[-1])}'
                          f'<tspan x="{lx}" dy="11">{_e(_pct(frac))}</tspan></text>')
    legend = "".join(
        f'<span class="dm-key"><i style="background:{c}"></i>{_e(lbl)}</span>'
        for _top, c, lbl in BINS)
    legend += f'<span class="dm-key"><i class="dm-hatch"></i>{_e(NO_DATA)}</span>'
    rows = sorted(ch.items(), key=lambda kv: (kv[1][2] is None, kv[1][2] or 0))
    names = admin1.names()
    trs = "".join(
        f"<tr><td>{_e(names.get(st, st))}</td><td>{'-' if old is None else f'{old:,.0f}'}</td>"
        f"<td>{new:,.0f}</td><td>{_e(_pct(frac))}</td></tr>" for st, (old, new, frac) in rows)
    svg = (f'<svg viewBox="0 0 {width} {height}" overflow="hidden" role="img" aria-label="{_e(title or table)}">'
           '<defs><pattern id="nodata" width="6" height="6" patternUnits="userSpaceOnUse" '
           'patternTransform="rotate(45)"><rect width="6" height="6" fill="#fcfcfb"/>'
           f'<line x1="0" y1="0" x2="0" y2="6" stroke="{MUTED}" stroke-width="1"/></pattern></defs>'
           + "".join(shapes) + "".join(labels) + "</svg>")
    return (f'<figure class="diffmap"><figcaption>{_e(title or table)}</figcaption>{svg}'
            f'<p class="dm-legend">{legend}</p><p class="dm-attr">{_e(ATTRIBUTION)}</p>'
            f'<details><summary>Table: {_e(table)} by state or province</summary>'
            f'<table class="grid"><tr><th>State or province</th><th>Previous</th><th>This '
            f'edition</th><th>Change</th></tr>{trs}</table></details></figure>')


CSS = (".diffmap svg{width:100%;height:auto;display:block}"
       ".dm-legend{display:flex;flex-wrap:wrap;gap:12px;font-size:13px;color:#5d6675}"
       ".dm-key i{display:inline-block;width:14px;height:10px;margin-right:4px;"
       "vertical-align:middle;border:1px solid #e6e8ec}"
       ".dm-hatch{background:repeating-linear-gradient(45deg,#fcfcfb 0 3px,#5d6675 3px 4px)}"
       ".dm-attr{font-size:12px;color:#5d6675}")


def page(cur_rec: dict, prev_rec: dict) -> str:
    """Every diff map two records support, for the report page: one per
    table of OSM-002 (feature counts) and OSM-006 (length or area)."""
    cm, pm = cur_rec.get("metrics") or {}, prev_rec.get("metrics") or {}
    pcycle = (prev_rec.get("pack") or {}).get("cycle", "?")
    out = []
    for check, what in (("OSM-002", "features"), ("OSM-006", None)):
        cur, prev = cm.get(check), pm.get(check)
        if not cur or not prev:
            continue
        for table in sorted(cur):
            unit = what or {"length_km": "km", "area_km2": "km2"}.get(
                (admin1.MEASURES.get(table) or ("",))[0], "")
            label = "features" if what else ("length" if unit == "km" else "area")
            out.append(render(cur, prev, table, unit=unit,
                              title=f"{table}: {label} by state or province against "
                                    f"{pcycle} ({check})"))
    return "".join(x for x in out if x)
