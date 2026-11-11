# SPDX-License-Identifier: Apache-2.0
"""Diff a mask-only terrain re-edition against the edition it replaces.

A re-edition that rebuilds only the ``.wmask`` water masks (AER-4595: the
AER-4594 union fix) must leave everything else in the pack byte-identical.
This compares a staged pack with the published one and reports:

- **content**: every member that is not a ``.wmask`` (the ``.hgt`` tiles,
  mosaic ``.json`` sidecars) must match by CRC-32 and size, read from the
  two central directories, so no tile data is fetched. ``pack_meta.json``
  is skipped: it names the edition, so it always differs.
- **members**: the two packs must hold the same member names.
- **masks**: per level, how many files changed and how many posts were
  gained and lost. A mask of the wrong byte length is a hard failure.
- **borders** (``--border``): posts on named edges of one tile's mask that
  must all be water, e.g. ``N27W097:5:SE`` -- the spot check QA's repro
  used (L5 N27W097 had 3/0/6/0 of 113 border posts set).

Lost posts are reported, not failed by default. The union fix alone only
adds water (a post covered by an odd count of rings is covered by at least
one polygon), so with an unchanged water source lost would be 0; but the
masks are rasterized from whichever water pack is current, and a newer one
can legitimately drop water. ``--max-lost`` turns it into a gate.

Either side may be a pack file or an ``https://`` URL; a URL is read with
HTTP Range requests (the central directory, then only the ``.wmask``
members), so diffing against a multi-GB published pack downloads a few
percent of it. Stdlib only, like the report.
"""

from __future__ import annotations

import json
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .terrain_ref import _RangeReader

SKIP = {"pack_meta.json"}
_MIP = re.compile(r"^\.mip/(\d+)/[NS]\d{2}/([NS]\d{2}[EW]\d{3})\.wmask$")
_MOSAIC = re.compile(r"^\.mip/mosaic/L(\d+)\.wmask$")
EDGES = "NSEW"


class _Pack:
    """A terrain pack, local or remote, opened for member reads."""

    def __init__(self, src: str | Path):
        self.label = str(src)
        s = str(src)
        if s.startswith(("http://", "https://")):
            self._remote = _RangeReader(s)
            self.zip = zipfile.ZipFile(self._remote)
        else:
            self._remote = None
            self.zip = zipfile.ZipFile(s)
        self.infos = {i.filename: i for i in self.zip.infolist() if not i.is_dir()}

    def read(self, name: str) -> bytes:
        if self._remote is not None:
            return self._remote.member(self.infos[name])
        return self.zip.read(name)


def level_key(name: str) -> str | None:
    """``"L5"`` for a native tile's mip mask, ``"mosaic L5"`` for a
    mosaic mask, ``None`` for anything else."""
    m = _MIP.match(name)
    if m:
        return f"L{m.group(1)}"
    m = _MOSAIC.match(name)
    return f"mosaic L{m.group(1)}" if m else None


def mask_shape(name: str, pack: _Pack) -> tuple[int, int]:
    """``(rows, cols)`` of a mask: implied by the level for a mip tile
    (``3600 / 2**L + 1``), from the sibling ``.json`` for a mosaic."""
    m = _MIP.match(name)
    if m:
        side = 3600 // (2 ** int(m.group(1))) + 1
        return side, side
    meta = json.loads(pack.read(name[:-len(".wmask")] + ".json"))
    return int(meta["rows"]), int(meta["cols"])


def border_counts(mask: bytes, rows: int, cols: int) -> dict[str, int]:
    """Set posts on each edge of a row-packed mask (MSB first, rows padded
    to a byte). Row 0 is the north edge, column 0 the west edge."""
    rb = -(-cols // 8)

    def bit(r: int, c: int) -> int:
        return (mask[r * rb + c // 8] >> (7 - c % 8)) & 1

    return {
        "N": sum(bit(0, c) for c in range(cols)),
        "S": sum(bit(rows - 1, c) for c in range(cols)),
        "W": sum(bit(r, 0) for r in range(rows)),
        "E": sum(bit(r, cols - 1) for r in range(rows)),
    }


def parse_border(spec: str) -> tuple[str, int, str]:
    """``"N27W097:5:SE"`` -> ``("N27W097", 5, "SE")``."""
    try:
        tile, level, edges = spec.split(":")
        level_n = int(level)
    except ValueError:
        raise ValueError(f"--border {spec!r}: expected TILE:LEVEL:EDGES, e.g. N27W097:5:SE")
    tile, edges = tile.upper(), edges.upper()
    if not re.fullmatch(r"[NS]\d{2}[EW]\d{3}", tile) or not edges or set(edges) - set(EDGES):
        raise ValueError(f"--border {spec!r}: expected TILE:LEVEL:EDGES, e.g. N27W097:5:SE")
    return tile, level_n, edges


@dataclass
class LevelStat:
    files: int = 0
    changed: int = 0
    gained: int = 0
    lost: int = 0
    worst_lost: list = field(default_factory=list)     # (lost, name), largest first


@dataclass
class BorderResult:
    spec: str
    member: str
    side: int
    staged: dict
    published: dict | None
    want: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and all(self.staged[e] == self.side for e in self.want)


@dataclass
class Diff:
    staged: str
    published: str
    content_changed: list = field(default_factory=list)
    only_staged: list = field(default_factory=list)
    only_published: list = field(default_factory=list)
    bad_size: list = field(default_factory=list)
    levels: dict = field(default_factory=dict)
    borders: list = field(default_factory=list)

    @property
    def lost(self) -> int:
        return sum(s.lost for s in self.levels.values())

    @property
    def gained(self) -> int:
        return sum(s.gained for s in self.levels.values())

    def failures(self, max_lost: int | None = None) -> list[str]:
        out = []
        if self.content_changed:
            out.append(f"{len(self.content_changed)} non-mask member(s) changed, "
                       f"e.g. {self.content_changed[:3]}")
        if self.only_staged:
            out.append(f"{len(self.only_staged)} member(s) only in the staged pack, "
                       f"e.g. {self.only_staged[:3]}")
        if self.only_published:
            out.append(f"{len(self.only_published)} member(s) only in the published pack, "
                       f"e.g. {self.only_published[:3]}")
        if self.bad_size:
            out.append(f"{len(self.bad_size)} mask(s) of the wrong byte length, "
                       f"e.g. {self.bad_size[:3]}")
        for b in self.borders:
            if not b.ok:
                out.append(f"border {b.spec}: {b.error or _edges_text(b.staged, b.side)}")
        if max_lost is not None and self.lost > max_lost:
            out.append(f"{self.lost:,} water post(s) lost, more than --max-lost {max_lost:,}")
        return out


def _edges_text(counts: dict, side: int) -> str:
    return " ".join(f"{e} {counts[e]}/{side}" for e in EDGES)


def _mask_delta(new: bytes, old: bytes) -> tuple[int, int]:
    """``(gained, lost)`` posts between two equal-length masks."""
    n, o = int.from_bytes(new, "big"), int.from_bytes(old, "big")
    return (n & ~o).bit_count(), (o & ~n).bit_count()


def diff(staged: str | Path, published: str | Path, *, borders=(), jobs: int = 16,
         keep_worst: int = 5) -> Diff:
    s, p = _Pack(staged), _Pack(published)
    d = Diff(staged=s.label, published=p.label)
    names_s, names_p = set(s.infos) - SKIP, set(p.infos) - SKIP
    d.only_staged = sorted(names_s - names_p)
    d.only_published = sorted(names_p - names_s)
    masks = []
    for name in sorted(names_s & names_p):
        if name.endswith(".wmask"):
            masks.append(name)
            continue
        a, b = s.infos[name], p.infos[name]
        if (a.CRC, a.file_size) != (b.CRC, b.file_size):
            d.content_changed.append(name)

    def one(name):
        return name, s.read(name), p.read(name)

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        for name, new, old in ex.map(one, masks):
            key = level_key(name) or "other"
            st = d.levels.setdefault(key, LevelStat())
            st.files += 1
            if len(new) != len(old):
                d.bad_size.append(name)
                continue
            if key != "other":
                rows, cols = mask_shape(name, s)
                if len(new) != rows * -(-cols // 8):
                    d.bad_size.append(name)
                    continue
            if new == old:
                continue
            st.changed += 1
            g, lost = _mask_delta(new, old)
            st.gained += g
            st.lost += lost
            if lost:
                st.worst_lost.append((lost, name))
                st.worst_lost.sort(reverse=True)
                del st.worst_lost[keep_worst:]

    for spec in borders:
        tile, level, edges = parse_border(spec)
        member = f".mip/{level}/{tile[:3]}/{tile}.wmask"
        side = 3600 // (2 ** level) + 1
        if member not in s.infos:
            d.borders.append(BorderResult(spec, member, side, {}, None, edges,
                                          error=f"{member} is not in the staged pack"))
            continue
        before = (border_counts(p.read(member), side, side) if member in p.infos else None)
        d.borders.append(BorderResult(spec, member, side,
                                      border_counts(s.read(member), side, side), before, edges))
    return d


def render(d: Diff, max_lost: int | None = None) -> str:
    lines = [f"staged:    {d.staged}", f"published: {d.published}", ""]
    lines.append(f"{'level':<12} {'masks':>7} {'changed':>8} {'gained':>14} {'lost':>12}")
    for key in sorted(d.levels, key=lambda k: (k.startswith("mosaic"), k)):
        st = d.levels[key]
        lines.append(f"{key:<12} {st.files:>7,} {st.changed:>8,} {st.gained:>14,} {st.lost:>12,}")
    lines.append(f"{'total':<12} {sum(s.files for s in d.levels.values()):>7,} "
                 f"{sum(s.changed for s in d.levels.values()):>8,} {d.gained:>14,} {d.lost:>12,}")
    worst = sorted((w for st in d.levels.values() for w in st.worst_lost), reverse=True)[:5]
    if worst:
        lines.append("")
        lines.append("most posts lost (a newer water source can drop water; the union fix cannot):")
        lines += [f"  {n:>10,}  {name}" for n, name in worst]
    for b in d.borders:
        lines.append("")
        lines.append(f"border {b.spec} ({b.member}, {b.side} posts an edge; want {b.want} all water)")
        if b.error:
            lines.append(f"  {b.error}")
            continue
        if b.published is not None:
            lines.append(f"  published  {_edges_text(b.published, b.side)}")
        lines.append(f"  staged     {_edges_text(b.staged, b.side)}   {'OK' if b.ok else 'FAIL'}")
    lines.append("")
    fails = d.failures(max_lost)
    if fails:
        lines += [f"FAIL {f}" for f in fails]
    else:
        lines.append("OK: non-mask members identical, member sets equal, mask sizes valid"
                     + (", borders water" if d.borders else ""))
    return "\n".join(lines)
