# SPDX-License-Identifier: Apache-2.0
"""Download + extract upstream archives. Resumable, no surprises.

Kept deliberately small and side-effect-explicit so the orchestrator can
inject a fake in tests (it never has to hit the network to be tested).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

#: Sent on every request this package makes. The CDN in front of
#: navdata.aerocommons.org answers 403 to Python's default ``Python-urllib/*``
#: user agent, so no download path may fall back to it.
USER_AGENT = "makerplane-data/0.1 (+https://github.com/billmallard/makerplane-data)"
_CHUNK = 1 << 20  # 1 MiB


def http_get(url: str, *, timeout: int = 60) -> bytes:
    """GET ``url`` into memory with :data:`USER_AGENT`. For small objects
    (a manifest, its signature, a pack report); packs go through
    :func:`download`."""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def download(url: str, dest: str | Path, *, resume: bool = True,
             timeout: int = 60, progress=None) -> Path:
    """Download ``url`` to ``dest`` with resume support.

    Downloads to ``dest.part`` then renames on completion, so a partial or
    interrupted transfer never looks like a finished file.

    ``progress``, if given, is called as ``progress(done_bytes, total_bytes)``
    on each chunk (``total_bytes`` is None if the server sent no Content-Length).
    """
    import requests  # lazy: only the real fetch path needs it (not tests)

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")

    headers = {"User-Agent": USER_AGENT}
    mode = "wb"
    have = 0
    if resume and part.exists():
        have = part.stat().st_size
        headers["Range"] = f"bytes={have}-"
        mode = "ab"

    with requests.get(url, headers=headers, stream=True, timeout=timeout) as r:
        if have and r.status_code == 200:
            # Server ignored Range (sent the whole file) — restart cleanly.
            mode, have = "wb", 0
        elif have and r.status_code == 416:
            # Already have the whole thing.
            part.replace(dest)
            if progress:
                progress(have, have)
            return dest
        r.raise_for_status()
        total = None
        cl = r.headers.get("Content-Length")
        if cl is not None:
            try:
                total = int(cl) + (have if mode == "ab" else 0)
            except ValueError:
                total = None
        done = have
        if progress:
            progress(done, total)
        with open(part, mode) as f:
            for block in r.iter_content(_CHUNK):
                f.write(block)
                done += len(block)
                if progress:
                    progress(done, total)
    part.replace(dest)
    return dest


def extract_zip(archive: str | Path, dest_dir: str | Path,
                member: str | None = None) -> Path:
    """Extract a zip (or a single member) into ``dest_dir``. Returns the dir."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        if member:
            z.extract(member, dest_dir)
        else:
            z.extractall(dest_dir)
    return dest_dir


def fetch_and_extract(url: str, work_dir: str | Path, *,
                      member: str | None = None) -> Path:
    """Download ``url`` and extract it under ``work_dir``. Returns the
    directory containing the extracted files."""
    work_dir = Path(work_dir)
    archive = download(url, work_dir / Path(url.split("?")[0]).name)
    return extract_zip(archive, work_dir / "extracted", member=member)
