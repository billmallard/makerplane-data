# SPDX-License-Identifier: Apache-2.0
"""Fetch one published pack, trusted through the signed manifest.

A build step that consumes another pack (the terrain build rasterizes the
published ``water-na`` water.sqlite into ``.wmask`` masks, AER-2287) must not
trust a bare URL: it verifies the manifest signature, picks the current
edition with the same ``Manifest.select`` the Pi updater uses, and checks the
download's sha256 against that signed entry. A cached copy whose hash still
matches is reused, so a re-run does not pull the pack again.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from . import fetch, signing
from .manifest import Manifest, PackEntry


def resolve_entry(manifest_bytes: bytes, sig_text: str, pub_text: str,
                  pack_id: str, day: _dt.date) -> PackEntry:
    """Verify the manifest signature, then select ``pack_id``'s current
    edition for ``day``. Raises on a bad signature or an unlisted id."""
    signing.verify(manifest_bytes, sig_text, pub_text)
    entry = Manifest.from_bytes(manifest_bytes).select(pack_id, day)
    if entry is None:
        raise LookupError(f"{pack_id!r} has no edition covering {day} "
                          "in the signed manifest")
    return entry


def fetch_verified_pack(entry: PackEntry, dest_dir: str | Path, *,
                        download=fetch.download, log=print) -> Path:
    """Return a local copy of ``entry`` whose sha256 matches the signed
    manifest, downloading only when no matching cached copy exists. A
    download that does not match is deleted and raises ``ValueError``."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{entry.id}-{entry.cycle}.pack"
    if dest.is_file() and signing.sha256_file(dest) == entry.sha256:
        log(f"{dest.name}: cached copy matches manifest sha256, reusing")
        return dest
    if dest.exists():
        dest.unlink()                         # stale/corrupt cache entry
    log(f"{dest.name}: downloading {entry.bytes:,} B from {entry.url}")
    download(entry.url, dest)
    got = signing.sha256_file(dest)
    if got != entry.sha256:
        dest.unlink()
        raise ValueError(f"{dest.name}: sha256 {got} does not match the "
                         f"signed manifest ({entry.sha256})")
    log(f"{dest.name}: sha256 OK")
    return dest
