"""On-Pi updater core — fetch, verify, install (verify-then-atomic-swap).

The safety contract (this is avionics-adjacent, so it matters):

  * The manifest signature is checked against the embedded public key
    BEFORE the manifest is trusted or cached. Any failure => use the last
    good cached manifest, or refuse — never trust unverified bytes.
  * A pack is downloaded to ``staging/``, sha256-verified against the
    signed manifest, and only THEN moved into place and the ``current``
    symlink atomically flipped. A bad download/signature can never disturb
    the live data a running pyEfis is serving.
  * POSIX rename semantics: flipping ``current`` does not affect a process
    that already has the old file open; the new data applies on restart /
    next cache miss.

Shares ``packtools.signing`` and ``packtools.manifest`` with the build
side so the verify logic can never drift from the sign logic.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from packtools import signing
from packtools.manifest import Manifest, PackEntry

from .config import Config

# --- status vocabulary (also what the DATA annunciation keys off, Phase F) --
CURRENT = "current"
UPDATE = "UPDATE AVAILABLE"
STAGED = "update-staged"
EXPIRES = "EXPIRES"
EXPIRED = "EXPIRED"
MISSING = "MISSING"
UNKNOWN = "unknown"

# kind -> (subdirectory under root, canonical filename pyEfis opens)
SQLITE_KINDS: dict[str, tuple[str, str]] = {
    "navdata": ("navdata", "airports.sqlite"),
    "obstacles": ("obstacles", "obstacles.sqlite"),
    "water": ("water", "water.sqlite"),
}

# Selection policy (what a Pi tracks):
#   core navdata kinds  -> tracked by default (small, CONUS-wide)
#   bulk kinds          -> opt-in by region (large, region-grouped)
BULK_KINDS = ("water", "terrain", "charts")

# Zip "tile" packs that are unzipped and merged into a shared tile tree
# (vs. sqlite packs that are swapped via the current symlink).
TILE_KINDS = ("terrain",)

# Human labels for the on-device status screen.
KIND_LABELS = {
    "navdata": "Airports & Runways",
    "obstacles": "Obstacles",
    "cifp": "Procedures & Waypoints",
    "water": "Water",
    "terrain": "Terrain",
    "charts": "Charts",
}

# status -> display severity. Subtle by design: expired/out-of-window is amber,
# soon-to-expire/missing is white, healthy is none. The EFIS informs; it never
# restricts (old data with awareness beats no data).
SEVERITY = {
    CURRENT: "none", STAGED: "none",
    EXPIRES: "white", MISSING: "white", UNKNOWN: "white",
    UPDATE: "amber", EXPIRED: "amber",
}


class VerificationError(Exception):
    """Signature or sha256 mismatch — caller must not install."""


# --------------------------------------------------------------------------
# transports
# --------------------------------------------------------------------------

class HttpRemote:
    """Production transport: HTTPS GET against the public R2 origin."""

    def get_bytes(self, url: str, timeout: int = 30) -> bytes:
        import requests  # lazy
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.content

    def download(self, url: str, dest: Path) -> Path:
        from packtools import fetch  # resumable download
        return fetch.download(url, dest)


class LocalDirRemote:
    """Filesystem transport: maps a URL's *path* under a local root.

    Serves both the USB-import flow (root = the stick's ``makerplane-data/``
    dir) and the test suite (root = an orchestrator LocalStore output), since
    both lay out ``manifest.json`` + ``packs/<file>`` identically.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _path(self, url: str) -> Path:
        return self.root / urlparse(url).path.lstrip("/")

    def get_bytes(self, url: str, timeout: int = 30) -> bytes:
        return self._path(url).read_bytes()

    def download(self, url: str, dest: Path) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._path(url).read_bytes())
        return dest


# --------------------------------------------------------------------------
# local inventory
# --------------------------------------------------------------------------

class Inventory:
    """``installed.json`` — what is on disk, by pack id."""

    def __init__(self, path: Path, data: dict | None = None):
        self.path = Path(path)
        self.data = data if data is not None else {"packs": {}}

    @classmethod
    def load(cls, path: str | Path) -> "Inventory":
        path = Path(path)
        try:
            return cls(path, json.loads(path.read_text()))
        except (FileNotFoundError, ValueError):
            return cls(path)

    def get(self, pack_id: str) -> dict | None:
        return self.data["packs"].get(pack_id)

    def set_current(self, pack_id: str, cycle: str, sha256: str, kind: str = "") -> None:
        e = self.data["packs"].setdefault(pack_id, {})
        e["current"] = cycle
        e["sha256"] = sha256
        if kind:
            e["kind"] = kind

    def set_staged(self, pack_id: str, cycle: str, sha256: str) -> None:
        e = self.data["packs"].setdefault(pack_id, {})
        e["staged"] = cycle
        e["staged_sha256"] = sha256

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
        os.replace(tmp, self.path)


# --------------------------------------------------------------------------
# the updater
# --------------------------------------------------------------------------

@dataclass
class PackStatus:
    pack_id: str
    status: str
    detail: str
    kind: str = ""
    name: str = ""
    cycle: str = ""                 # installed cycle, or "" if none
    expires: str | None = None      # installed cycle's expiry (ISO)
    days: int | None = None         # days until that expiry (negative if past)

    @property
    def severity(self) -> str:
        return SEVERITY.get(self.status, "white")

    def as_dict(self) -> dict:
        return {
            "id": self.pack_id, "name": self.name, "kind": self.kind,
            "status": self.status, "severity": self.severity,
            "cycle": self.cycle, "expires": self.expires, "days": self.days,
            "detail": self.detail,
        }


class Updater:
    def __init__(self, config: Config, public_key: str, *,
                 remote=None, today: _dt.date | None = None, log=print):
        self.config = config
        self.public_key = public_key
        self.remote = remote if remote is not None else HttpRemote()
        self.today = today or _dt.date.today()
        self.log = log
        self.inventory = Inventory.load(config.root / "installed.json")
        self.errors: list[str] = []   # verification failures during the last update
        self.manifest_generated: str | None = None   # set on each fetch

    # --- manifest fetch + verify ---
    def _verify(self, raw: bytes, sig: str) -> str:
        return signing.verify(raw, sig, self.public_key)  # raises on any problem

    def fetch_manifest(self, remote=None) -> Manifest:
        """Fetch + verify the manifest. Falls back to the last good cache when
        offline; the cache is itself re-verified, so stale-but-signed is OK and
        unsigned is never trusted."""
        remote = remote or self.remote
        cache = self.config.root / "manifest.json"
        cache_sig = self.config.root / "manifest.json.minisig"
        try:
            raw = remote.get_bytes(self.config.manifest_url)
            sig = remote.get_bytes(self.config.sig_url).decode("ascii")
            self._verify(raw, sig)                       # before trust/cache
            self.config.root.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(raw)
            cache_sig.write_text(sig, encoding="ascii")
            return self._loaded(raw)
        except VerificationError:
            raise
        except Exception as e:
            if cache.exists() and cache_sig.exists():
                raw = cache.read_bytes()
                self._verify(raw, cache_sig.read_text("ascii"))
                self.log(f"offline: using cached manifest ({type(e).__name__})")
                return self._loaded(raw)
            raise

    def _loaded(self, raw: bytes) -> Manifest:
        m = Manifest.from_bytes(raw)
        self.manifest_generated = m.generated
        return m

    def _tracked_ids(self, m: Manifest) -> list[str]:
        """Which pack ids this Pi tracks: all core-navdata kinds by default,
        plus bulk packs whose region is opted-in, plus any explicit ids."""
        by_id: dict[str, PackEntry] = {}
        for e in m.packs:
            by_id.setdefault(e.id, e)
        ids = set(self.config.packs)
        for pid, e in by_id.items():
            if e.kind in self.config.track_kinds:
                ids.add(pid)
            elif e.kind in BULK_KINDS and set(e.regions) & set(self.config.regions):
                ids.add(pid)
        return sorted(ids)

    # --- status ---
    def status(self) -> list[PackStatus]:
        m = self.fetch_manifest()
        return [self._status_for(m, pid) for pid in self._tracked_ids(m)]

    def _status_for(self, m: Manifest, pid: str) -> PackStatus:
        entries = m.for_id(pid)
        kind = entries[0].kind if entries else ""
        name = KIND_LABELS.get(kind, pid)
        inv = self.inventory.get(pid) or {}
        installed = inv.get("current", "")
        inst_entry = next((e for e in entries if e.cycle == installed), None) if installed else None
        days = inst_entry.days_until_expiry(self.today) if inst_entry else None
        expires = inst_entry.expires if inst_entry else None
        status, detail = self._classify(m, pid, entries, inv, installed, inst_entry, days)
        return PackStatus(pid, status, detail, kind=kind, name=name,
                          cycle=installed, expires=expires, days=days)

    def _classify(self, m, pid, entries, inv, installed, inst_entry, days):
        if not entries:
            return UNKNOWN, "not in catalog"
        cur = m.select(pid, self.today)             # entry whose window covers today
        if not installed:
            return MISSING, (f"available: {cur.cycle}" if cur else "no current cycle")
        if days is not None and days < 0:
            return EXPIRED, f"{installed} expired {inst_entry.expires}"
        if cur and cur.cycle != installed:
            if inv.get("staged") == cur.cycle:
                return STAGED, f"{cur.cycle} staged (effective {cur.effective})"
            return UPDATE, f"{installed} -> {cur.cycle}"
        if days is not None and days <= 7:
            return EXPIRES, f"{installed} expires {inst_entry.expires} ({days}d)"
        return CURRENT, installed

    # --- install (verify-then-atomic-swap) ---
    def install_pack(self, entry: PackEntry, *, make_current: bool, remote=None) -> Path:
        if entry.kind in TILE_KINDS:
            return self._install_tile_pack(entry, remote=remote)
        if entry.kind not in SQLITE_KINDS:
            raise NotImplementedError(
                f"installing kind {entry.kind!r} is not supported yet")
        remote = remote or self.remote
        subdir, filename = SQLITE_KINDS[entry.kind]
        root = self.config.root
        staging = root / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        part = staging / f"{entry.id}-{entry.cycle}.pack"

        self.log(f"  download {entry.id} {entry.cycle} ({entry.bytes:,} B)")
        remote.download(entry.url, part)

        got = signing.sha256_file(part)
        if got != entry.sha256:
            part.unlink(missing_ok=True)
            raise VerificationError(
                f"sha256 mismatch for {entry.id} {entry.cycle}: "
                f"expected {entry.sha256[:12]}…, got {got[:12]}…")

        version_dir = root / subdir / entry.cycle
        version_dir.mkdir(parents=True, exist_ok=True)
        final = version_dir / filename
        os.replace(part, final)                      # move verified file into place
        if make_current:
            self._flip_current(root / subdir, entry.cycle)
        return final

    def _install_tile_pack(self, entry: PackEntry, remote=None) -> Path:
        """Install a zip tile pack (terrain): verify, then unzip-merge the HGT
        tree into terrain/tiles/. Tiles union across region packs into one
        tree the SVS reads; there is no current-symlink (tiles aren't
        versioned). Verify-then-extract keeps a bad download out of the tree."""
        remote = remote or self.remote
        root = self.config.root
        staging = root / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        part = staging / f"{entry.id}-{entry.cycle}.pack"

        self.log(f"  download {entry.id} {entry.cycle} ({entry.bytes:,} B)")
        remote.download(entry.url, part)
        got = signing.sha256_file(part)
        if got != entry.sha256:
            part.unlink(missing_ok=True)
            raise VerificationError(
                f"sha256 mismatch for {entry.id} {entry.cycle}: "
                f"expected {entry.sha256[:12]}…, got {got[:12]}…")

        tiles_dir = root / "terrain" / "tiles"
        tiles_dir.mkdir(parents=True, exist_ok=True)
        import zipfile
        with zipfile.ZipFile(part) as z:
            for member in z.namelist():
                if member.endswith("/") or member == "pack_meta.json":
                    continue
                z.extract(member, tiles_dir)   # zipfile sanitizes path traversal
        part.unlink(missing_ok=True)
        self._record_terrain_region(entry)
        return tiles_dir

    def _record_terrain_region(self, entry: PackEntry) -> None:
        """Track which terrain regions/edition are merged into the tile tree."""
        f = self.config.root / "terrain" / "tiles" / ".regions.json"
        try:
            data = json.loads(f.read_text())
        except Exception:
            data = {}
        for region in (entry.regions or [entry.id]):
            data[region] = entry.cycle
        f.write_text(json.dumps(data, indent=2, sort_keys=True))

    def _flip_current(self, kind_dir: Path, cycle: str) -> None:
        """Atomically point ``<kind_dir>/current`` at the ``cycle`` subdir.

        On POSIX this is a relative symlink (what pyEfis opens). Where symlinks
        are unavailable (a Windows dev box without the privilege) it degrades
        to a text pointer file so the tooling still works for testing; the Pi
        always gets the symlink.
        """
        link = kind_dir / "current"
        try:
            tmp = kind_dir / ".current.new"
            if tmp.exists() or tmp.is_symlink():
                tmp.unlink()
            os.symlink(cycle, tmp, target_is_directory=True)
            os.replace(tmp, link)                          # POSIX-atomic swap
        except OSError:
            tmp = kind_dir / ".current.txt.new"
            tmp.write_text(cycle, encoding="ascii")
            os.replace(tmp, link)

    def _current_target(self, kind_dir: Path) -> str | None:
        """The cycle name ``current`` points at (symlink or pointer-file form)."""
        link = kind_dir / "current"
        if link.is_symlink():
            return os.readlink(link)
        if link.is_file():
            return link.read_text(encoding="ascii").strip()
        return None

    # --- update ---
    def _next_entry(self, m: Manifest, pid: str) -> PackEntry | None:
        future = [e for e in m.for_id(pid)
                  if e.effective and _dt.date.fromisoformat(e.effective) > self.today]
        return min(future, key=lambda e: e.effective) if future else None

    def update(self, *, dry_run: bool = False, remote=None) -> list[PackStatus]:
        remote = remote or self.remote
        m = self.fetch_manifest(remote)
        self.errors = []
        results: list[PackStatus] = []
        for pid in self._tracked_ids(m):
            entries = m.for_id(pid)
            if not entries:
                results.append(PackStatus(pid, UNKNOWN, "not in catalog"))
                continue
            inv = self.inventory.get(pid) or {}
            cur = m.select(pid, self.today)

            if cur and inv.get("current") != cur.cycle:
                if dry_run:
                    results.append(PackStatus(pid, UPDATE, f"would install {cur.cycle}"))
                else:
                    try:
                        self.install_pack(cur, make_current=True, remote=remote)
                    except VerificationError as e:
                        # leave 'current' untouched; record as a hard error
                        self.errors.append(f"{pid}: {e}")
                        results.append(PackStatus(pid, EXPIRED, str(e)))
                        continue
                    except NotImplementedError as e:
                        results.append(PackStatus(pid, UNKNOWN, str(e)))
                        continue
                    self.inventory.set_current(pid, cur.cycle, cur.sha256, kind=cur.kind)
                    results.append(PackStatus(pid, CURRENT, f"installed {cur.cycle}"))
            else:
                results.append(PackStatus(pid, CURRENT, cur.cycle if cur else "(no current cycle)"))

            # pre-stage the next cycle so rollover is seamless on its effective date
            if self.config.stage_next and not dry_run:
                nxt = self._next_entry(m, pid)
                inv_now = self.inventory.get(pid) or {}
                if nxt and inv_now.get("staged") != nxt.cycle:
                    try:
                        self.install_pack(nxt, make_current=False, remote=remote)
                        self.inventory.set_staged(pid, nxt.cycle, nxt.sha256)
                        self.log(f"  staged next: {pid} {nxt.cycle} (effective {nxt.effective})")
                    except (VerificationError, NotImplementedError) as e:
                        self.log(f"  stage-next skipped for {pid}: {e}")

            self._prune(pid, entries[0].kind)

        self.inventory.save()
        return results

    def _prune(self, pid: str, kind: str) -> None:
        """Remove version dirs for this pack other than current + staged, to
        bound SD-card use. Assumes one pack per kind/subdir (our mapping)."""
        if kind not in SQLITE_KINDS:
            return
        inv = self.inventory.get(pid) or {}
        keep = {inv.get("current"), inv.get("staged")} - {None}
        base = self.config.root / SQLITE_KINDS[kind][0]
        if not base.is_dir():
            return
        cur_target = self._current_target(base)
        import shutil
        for child in base.iterdir():
            if child.is_dir() and child.name not in keep and child.name != cur_target:
                shutil.rmtree(child, ignore_errors=True)

    # --- USB import ---
    def import_dir(self, path: str | Path, *, dry_run: bool = False) -> list[PackStatus]:
        """Install from a local directory (the USB sneakernet path). The
        directory's manifest copy is signature-verified exactly like the
        network one."""
        local = LocalDirRemote(path)
        return self.update(dry_run=dry_run, remote=local)
