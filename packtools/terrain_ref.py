# SPDX-License-Identifier: Apache-2.0
"""Copernicus terrain as an independent reference for point elevations.

The cross-source checks (catalogue v2, TV7) compare an FAA elevation
against the terrain under it: airport field elevation at the ARP
(``XS-001``), obstacle base (AMSL minus AGL, ``XS-002``), and the reverse
direction on a terrain pack (``TER-005``). This module reads the terrain.

A source is any of:

- a terrain pack file (the zip ``make_terrain`` writes);
- a directory holding the same ``<N44>/<N44W122>.hgt`` tree (a
  ``make_terrain`` input or an unzipped pack);
- an ``https://`` URL of a published terrain pack, read with HTTP Range
  requests so only the tiles that hold points are fetched (about 4 MB a
  tile instead of the whole multi-GB pack).

Tiles are the pyEfis layout ``convert_glo30`` writes: big-endian int16
metres (EGM2008), square, row 0 at the north edge, edge-inclusive (3601
samples span one degree). Samples are bilinear between the four
surrounding posts. A void post, or a point whose tile no source holds,
gives ``None`` rather than a number.

Stdlib only: the report runs on the build host and in CI without numpy.
"""

from __future__ import annotations

import array
import io
import json
import math
import struct
import sys
import threading
import urllib.request
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

#: The SRTM/GLO-30 void and the mosaic builder's no-tile sentinel.
VOID = (-32768, -9999)

M_PER_FT = 0.3048

USER_AGENT = "makerplane-data-report"


def tile_name(lat: float, lon: float) -> str:
    """The 1-degree tile holding ``(lat, lon)``, named by its SW corner."""
    a, b = math.floor(lat), math.floor(lon)
    return f"{'N' if a >= 0 else 'S'}{abs(a):02d}{'E' if b >= 0 else 'W'}{abs(b):03d}"


def _member(tile: str) -> str:
    return f"{tile[:3]}/{tile}.hgt"


class _RangeReader(io.RawIOBase):
    """A seekable read-only file over HTTP Range requests, so ``zipfile``
    reads the central directory and single members of a remote pack."""

    def __init__(self, url: str, timeout: int = 120):
        self.url, self.timeout, self.pos = url, timeout, 0
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            self.size = int(r.headers["Content-Length"])
            self.etag = r.headers.get("ETag")

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.size + off)
        return self.pos

    def get(self, start: int, length: int) -> bytes:
        """``length`` bytes at ``start``, in one request. Stateless, so
        threads can share one reader."""
        end = min(self.size, start + length) - 1
        headers = {"Range": f"bytes={start}-{end}", "User-Agent": USER_AGENT}
        # If-Match pins every range to the object the HEAD saw: a pack
        # replaced mid-read fails (412) instead of splicing two editions.
        if self.etag:
            headers["If-Match"] = self.etag
        req = urllib.request.Request(self.url, headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            if r.status != 206:
                raise OSError(f"range request answered {r.status}, not 206")
            data = r.read()
        if len(data) != end - start + 1:
            raise OSError(f"range request returned {len(data)} of {end - start + 1} bytes")
        return data

    def readinto(self, b):
        if self.pos >= self.size:
            return 0
        data = self.get(self.pos, len(b))
        b[:len(data)] = data
        self.pos += len(data)
        return len(data)

    def member(self, info: zipfile.ZipInfo) -> bytes:
        """One zip member's bytes, fetched directly (local header plus
        data, two requests) and checked against its CRC-32."""
        head = self.get(info.header_offset, 30)
        if head[:4] != b"PK\x03\x04":
            raise OSError(f"{info.filename}: no local file header")
        n_name, n_extra = struct.unpack("<HH", head[26:30])
        raw = self.get(info.header_offset + 30 + n_name + n_extra, info.compress_size)
        if info.compress_type == zipfile.ZIP_DEFLATED:
            raw = zlib.decompress(raw, -15)
        elif info.compress_type != zipfile.ZIP_STORED:
            raise OSError(f"{info.filename}: compression type {info.compress_type}")
        if zlib.crc32(raw) != info.CRC:
            raise OSError(f"{info.filename}: CRC-32 mismatch")
        return raw


@dataclass
class Source:
    """One terrain source: its label for the record and its tile set."""
    label: str
    tiles: set
    _zip: zipfile.ZipFile | None = None
    _dir: Path | None = None
    _remote: _RangeReader | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def read(self, tile: str) -> bytes:
        if self._remote is not None:
            return self._remote.member(self._zip.getinfo(_member(tile)))
        if self._zip is not None:
            with self._lock:                         # a ZipFile is not thread-safe
                return self._zip.read(_member(tile))  # zipfile checks the member CRC
        return (self._dir / _member(tile)).read_bytes()


def _label_zip(z: zipfile.ZipFile, fallback: str) -> str:
    try:
        meta = json.loads(z.read("pack_meta.json"))
        return f"{meta['id']} {meta['cycle']}"
    except Exception:
        return fallback


def open_source(spec: str | Path) -> Source:
    """Open one ``--terrain`` source. Raises ``ValueError`` if it holds no tile."""
    s = str(spec)
    if s.startswith(("https://", "http://")):
        reader = _RangeReader(s)
        z = zipfile.ZipFile(io.BufferedReader(reader, buffer_size=1 << 16))
        src = Source(_label_zip(z, s.rsplit("/", 1)[-1]), set(), _zip=z, _remote=reader)
    else:
        p = Path(s)
        if p.is_dir():
            tiles = {f.stem for f in p.glob("[NS][0-9][0-9]/*.hgt")}
            if not tiles:
                raise ValueError(f"terrain source {p.name}: no <N44>/<N44W122>.hgt tiles")
            return Source(f"directory {p.name}", tiles, _dir=p)
        z = zipfile.ZipFile(p)
        src = Source(_label_zip(z, p.name), set(), _zip=z)
    src.tiles = {Path(n).stem for n in src._zip.namelist()
                 if n.endswith(".hgt") and not n.startswith(".mip/")}
    if not src.tiles:
        raise ValueError(f"terrain source {src.label}: no native tiles")
    return src


class TerrainRef:
    """Bilinear point elevations over one or more terrain sources. The
    first source that holds a tile wins (regional packs overlap, and an
    overlapping tile is the same Copernicus data in both)."""

    def __init__(self, sources, *, workers: int = 8):
        self.sources = [s if isinstance(s, Source) else open_source(s) for s in sources]
        self.workers = workers
        self.tiles_read = 0

    @property
    def labels(self) -> list[str]:
        return [s.label for s in self.sources]

    def holds(self, tile: str) -> bool:
        return any(tile in s.tiles for s in self.sources)

    def _grid(self, tile: str):
        for s in self.sources:
            if tile in s.tiles:
                raw = s.read(tile)
                side = int(round((len(raw) / 2) ** 0.5))
                if side * side * 2 != len(raw) or side < 2:
                    raise ValueError(f"{tile}: not a square int16 grid ({len(raw)} bytes)")
                a = array.array("h")
                a.frombytes(raw)
                if sys.byteorder == "little":
                    a.byteswap()
                self.tiles_read += 1
                return a, side
        return None

    def sample_many(self, points):
        """``points`` is an iterable of ``(key, lat, lon)``. Yields
        ``(key, metres or None)``, reading each tile once."""
        by_tile: dict[str, list] = {}
        for key, lat, lon in points:
            by_tile.setdefault(tile_name(lat, lon), []).append((key, lat, lon))
        held = [t for t in sorted(by_tile) if self.holds(t)]
        for tile in sorted(set(by_tile) - set(held)):
            for key, _lat, _lon in by_tile[tile]:
                yield key, None

        def one(tile):
            grid = self._grid(tile)
            return [(key, _bilinear(grid, lat, lon)) for key, lat, lon in by_tile[tile]]

        # Tiles are read concurrently (a remote tile is a ~4 MB fetch), a
        # few at a time so at most ``workers`` decoded grids are in memory.
        with ThreadPoolExecutor(max_workers=max(1, self.workers)) as pool:
            for i in range(0, len(held), self.workers):
                for out in pool.map(one, held[i:i + self.workers]):
                    yield from out


def _bilinear(grid, lat: float, lon: float):
    a, side = grid
    n = side - 1
    y = (math.floor(lat) + 1 - lat) * n            # rows run south from the north edge
    x = (lon - math.floor(lon)) * n
    r0, c0 = min(int(y), n - 1), min(int(x), n - 1)
    fy, fx = y - r0, x - c0
    posts = (a[r0 * side + c0], a[r0 * side + c0 + 1],
             a[(r0 + 1) * side + c0], a[(r0 + 1) * side + c0 + 1])
    if any(p in VOID for p in posts):
        return None
    top = posts[0] * (1 - fx) + posts[1] * fx
    bot = posts[2] * (1 - fx) + posts[3] * fx
    return top * (1 - fy) + bot * fy
