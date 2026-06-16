"""pack_meta — the self-describing header carried *inside* every pack.

A pack must identify itself even when it is separated from the manifest
(e.g. a lone file copied onto a USB stick). Two physical pack containers,
one logical metadata schema:

  * **sqlite packs** (navdata, water): a ``pack_meta(key, value)`` table.
    pyEfis's DB loaders already tolerate-and-ignore unknown tables, so this
    is invisible to the renderer.
  * **zip packs** (terrain tiles, later charts, CIFP file-sets): a
    ``pack_meta.json`` member alongside the payload.

The manifest entry for a pack is a superset of pack_meta (it adds url,
bytes, sha256, regions). pack_meta is the subset that is intrinsic to the
file itself.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from dataclasses import dataclass, asdict, field
from pathlib import Path

#: Bump when the *meaning* of pack_meta fields changes in a breaking way.
SCHEMA_VERSION = 1

# Recognised pack kinds. "kind" drives which build tool produced the pack and
# how the Pi installs it; it is open for extension (charts, etc.).
KINDS = ("navdata", "obstacles", "cifp", "water", "terrain", "highways",
         "airports")


@dataclass
class PackMeta:
    id: str                       # stable pack id, e.g. "obstacles-conus"
    kind: str                     # one of KINDS
    cycle: str                    # AIRAC id ("2606") or YYMMDD or edition tag
    effective: str | None = None  # ISO date; None => non-cyclical (terrain)
    expires: str | None = None    # ISO date (exclusive); None => never expires
    attribution: str = ""
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown pack kind {self.kind!r}; expected one of {KINDS}")

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, d: dict) -> "PackMeta":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# --- sqlite packs ---------------------------------------------------------

def embed_sqlite(path: str | Path, meta: PackMeta) -> None:
    """(Re)write the pack_meta table inside an sqlite pack in place."""
    con = sqlite3.connect(str(path))
    try:
        con.execute("CREATE TABLE IF NOT EXISTS pack_meta (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("DELETE FROM pack_meta")
        con.executemany(
            "INSERT INTO pack_meta (key, value) VALUES (?, ?)",
            [(k, str(v)) for k, v in meta.as_dict().items()],
        )
        con.commit()
    finally:
        con.close()


def read_sqlite(path: str | Path) -> PackMeta:
    con = sqlite3.connect(str(path))
    try:
        rows = con.execute("SELECT key, value FROM pack_meta").fetchall()
    finally:
        con.close()
    d: dict = {k: v for k, v in rows}
    if "schema_version" in d:
        d["schema_version"] = int(d["schema_version"])
    return PackMeta.from_dict(d)


# --- zip packs ------------------------------------------------------------

_ZIP_META_NAME = "pack_meta.json"


def embed_zip(path: str | Path, meta: PackMeta) -> None:
    """Add/replace pack_meta.json inside an existing zip pack."""
    path = Path(path)
    # zipfile cannot rewrite a member in place; copy through a temp file.
    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(path, "r") as zin, \
         zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if item.filename == _ZIP_META_NAME:
                continue
            zout.writestr(item, zin.read(item.filename))
        zout.writestr(_ZIP_META_NAME, json.dumps(meta.as_dict(), indent=2))
    tmp.replace(path)


def read_zip(path: str | Path) -> PackMeta:
    with zipfile.ZipFile(path, "r") as z:
        return PackMeta.from_dict(json.loads(z.read(_ZIP_META_NAME)))


# --- dispatch -------------------------------------------------------------

def read(path: str | Path) -> PackMeta:
    """Read pack_meta from either container type, detected by content."""
    path = Path(path)
    if zipfile.is_zipfile(path):
        return read_zip(path)
    return read_sqlite(path)
