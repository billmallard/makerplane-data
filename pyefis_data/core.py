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
    "highways": ("highways", "highways.sqlite"),
}

# Selection policy (what a Pi tracks):
#   core navdata kinds  -> tracked by default (small, CONUS-wide)
#   bulk kinds          -> opt-in by region (large, region-grouped)
BULK_KINDS = ("water", "terrain", "charts", "highways")

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
    "highways": "Roads & Highways",
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


def disk_info(root) -> dict:
    """Free/total bytes of the filesystem that holds ``root`` (walking up to the
    nearest existing parent, since the data root may not exist yet). Used by the
    on-device picker to show 'installing to <root> - N GB free'."""
    import shutil
    p = Path(root)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        du = shutil.disk_usage(p)
        return {"root": str(root), "free_bytes": du.free, "total_bytes": du.total}
    except Exception:
        return {"root": str(root), "free_bytes": None, "total_bytes": None}


# Filesystems that can actually hold a data root (skip tmpfs/overlay/proc/...).
_REAL_FS = {"ext4", "ext3", "ext2", "vfat", "exfat", "ntfs", "ntfs3",
            "btrfs", "xfs", "f2fs"}


def _read_proc_mounts():
    out = []
    try:
        with open("/proc/mounts", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3:
                    out.append((parts[0], parts[1].replace("\\040", " "), parts[2]))
    except Exception:
        pass
    return out


def _sys_removable(device):
    """True if ``device`` (e.g. /dev/sda1, /dev/mmcblk0p1) is on removable media,
    per /sys/block/<base>/removable."""
    import re
    base = None
    m = re.match(r"/dev/(sd[a-z]+|hd[a-z]+)\d*$", device)
    if m:
        base = re.sub(r"\d+$", "", m.group(1))
    else:
        m = re.match(r"/dev/(nvme\d+n\d+|mmcblk\d+)p?\d*$", device)
        if m:
            base = m.group(1)
    if not base:
        return False
    try:
        with open(f"/sys/block/{base}/removable", encoding="ascii") as f:
            return f.read().strip() == "1"
    except Exception:
        return False


def list_drives(*, mounts=None, usage=None, removable=None, min_free=0):
    """Candidate storage drives for the data root: writable, real-filesystem
    mounts with their free/total space and a removable flag. Fixed drives sort
    first, then by most-free. ``mounts``/``usage``/``removable`` are injectable
    for tests; by default reads /proc/mounts + shutil.disk_usage + /sys."""
    import shutil
    mounts = mounts if mounts is not None else _read_proc_mounts()
    usage = usage or (lambda mp: (lambda d: (d.total, d.free))(shutil.disk_usage(mp)))
    removable = removable or _sys_removable
    seen, drives = set(), []
    for dev, mp, fs in mounts:
        if fs not in _REAL_FS or mp in seen:
            continue
        if mp.startswith(("/boot", "/proc", "/sys", "/dev")):
            continue
        if not os.path.isdir(mp) or not os.access(mp, os.W_OK):
            continue
        try:
            total, free = usage(mp)
        except Exception:
            continue
        if total <= 0 or free < min_free:
            continue
        seen.add(mp)
        drives.append({"mount": mp, "device": dev, "fstype": fs,
                       "free_bytes": free, "total_bytes": total,
                       "removable": bool(removable(dev))})
    drives.sort(key=lambda d: (d["removable"], -d["free_bytes"]))
    return drives


def network_available(config, timeout: int = 5) -> bool:
    """Cheap reachability probe for the data origin (HEAD the manifest)."""
    try:
        import requests  # lazy
        r = requests.head(config.manifest_url, timeout=timeout, allow_redirects=True)
        return r.status_code == 200
    except Exception:
        return False


def detect_sources(config, *, mount_globs=None, network_check=None) -> dict:
    """What update sources are available right now, for the Update flow:
    ``{"network": bool, "usb": [dir, ...]}``. A USB dir is any mounted location
    holding a ``manifest.json`` (either at its root or under a ``makerplane-data``
    subdir, matching the web-GUI sneakernet layout). ``mount_globs`` and
    ``network_check`` are injectable for tests."""
    import glob as _glob
    net = bool(network_check(config) if network_check else network_available(config))
    globs = mount_globs or ["/media/*/*", "/media/*", "/run/media/*/*", "/mnt/*"]
    usb: list[str] = []
    seen: set[str] = set()
    for pat in globs:
        for d in _glob.glob(pat):
            for cand in (Path(d) / "makerplane-data", Path(d)):
                key = str(cand)
                if key not in seen and (cand / "manifest.json").is_file():
                    seen.add(key)
                    usb.append(key)
    return {"network": net, "usb": usb}


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

    def ids(self) -> list[str]:
        return list(self.data["packs"].keys())

    def remove(self, pack_id: str) -> None:
        self.data["packs"].pop(pack_id, None)

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

    # --- catalog (full picker listing) ---
    def _all_ids(self, m: Manifest) -> list[str]:
        """Every unique pack id in the manifest, ordered by (kind, id) so the
        on-device picker groups consistently."""
        kind_of: dict[str, str] = {}
        for e in m.packs:
            kind_of.setdefault(e.id, e.kind)
        return sorted(kind_of, key=lambda pid: (kind_of[pid], pid))

    def catalog(self, *, remote=None) -> list[dict]:
        """Full catalog for the on-device picker: EVERY pack in the verified
        manifest (not just the tracked subset that ``status`` reports), each
        with its label, kind, download size, currency, regions, and
        ``installed`` / ``tracked`` flags.

        Reuses ``_status_for`` so the picker and the boot status screen can
        never disagree about a pack's currency. ``tracked`` reflects the
        current data.yaml selection so the picker can pre-check it."""
        remote = remote or self.remote
        m = self.fetch_manifest(remote)
        tracked = set(self._tracked_ids(m))
        rows: list[dict] = []
        for pid in self._all_ids(m):
            st = self._status_for(m, pid)
            entries = m.for_id(pid)
            # The entry the user would install now: the one whose window covers
            # today, else the sole non-cyclical edition.
            sel = m.select(pid, self.today) or (entries[0] if entries else None)
            row = st.as_dict()
            row.update({
                "bytes": sel.bytes if sel else 0,
                "regions": list(sel.regions) if sel and sel.regions else [],
                "attribution": sel.attribution if sel else "",
                "available_cycle": sel.cycle if sel else None,
                "tracked": pid in tracked,
                "installed": bool(st.cycle),
            })
            rows.append(row)
        return rows

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
        tiles = []
        with zipfile.ZipFile(part) as z:
            for member in z.namelist():
                if member.endswith("/") or member == "pack_meta.json":
                    continue
                z.extract(member, tiles_dir)   # zipfile sanitizes path traversal
                tiles.append(member)
        part.unlink(missing_ok=True)
        self._record_terrain_region(entry, tiles)
        return tiles_dir

    def _record_terrain_region(self, entry: PackEntry, tiles=None) -> None:
        """Track which terrain regions/edition are merged into the tile tree
        (``.regions.json``: region -> edition) and, separately, which tile files
        each region contributed (``.region_tiles.json``: region -> [paths]) so a
        deselected region's exclusive tiles can be removed later. The two files
        are kept separate so ``.regions.json`` stays the simple region->edition
        contract other code reads."""
        base = self.config.root / "terrain" / "tiles"
        regf = base / ".regions.json"
        tilef = base / ".region_tiles.json"
        try:
            regs = json.loads(regf.read_text())
        except Exception:
            regs = {}
        try:
            tmap = json.loads(tilef.read_text())
        except Exception:
            tmap = {}
        for region in (entry.regions or [entry.id]):
            regs[region] = entry.cycle
            tmap[region] = sorted(tiles or [])
        regf.write_text(json.dumps(regs, indent=2, sort_keys=True))
        tilef.write_text(json.dumps(tmap, indent=2, sort_keys=True))

    # --- removal (reconcile installed -> tracked selection) ---
    def remove_pack(self, pid: str, kind: str, m: Manifest | None = None) -> None:
        """Delete an installed pack's data from disk. SQLite kinds drop their
        whole kind subdir; terrain removes the region's exclusive tiles from the
        shared tree. Re-downloadable, so it's safe to be thorough."""
        import shutil
        root = self.config.root
        if kind in SQLITE_KINDS:
            subdir = SQLITE_KINDS[kind][0]
            shutil.rmtree(root / subdir, ignore_errors=True)
        elif kind in TILE_KINDS:
            entries = m.for_id(pid) if m else []
            regions = entries[0].regions if entries else [pid]
            self._remove_terrain_regions(regions)

    def _remove_terrain_regions(self, regions) -> None:
        """Remove ``regions`` from the merged terrain tree: delete the tiles they
        contributed that no *remaining* region also provides. If no terrain
        regions remain, drop the whole tile tree. Tiles whose provenance is
        unknown (legacy install with no .region_tiles.json) are left in place
        unless the tree is being fully removed."""
        import shutil
        base = self.config.root / "terrain" / "tiles"
        if not base.is_dir():
            return
        regf, tilef = base / ".regions.json", base / ".region_tiles.json"
        try:
            regs = json.loads(regf.read_text())
        except Exception:
            regs = {}
        try:
            tmap = json.loads(tilef.read_text())
        except Exception:
            tmap = {}
        removing = set(regions)
        remaining = [r for r in regs if r not in removing]
        if not remaining:                       # nothing left -> reclaim everything
            shutil.rmtree(base, ignore_errors=True)
            return
        keep = set()
        for r in remaining:
            keep.update(tmap.get(r, []))
        drop = set()
        for r in removing:
            drop.update(tmap.get(r, []))
        for rel in drop - keep:
            (base / rel).unlink(missing_ok=True)
        for r in removing:
            regs.pop(r, None)
            tmap.pop(r, None)
        regf.write_text(json.dumps(regs, indent=2, sort_keys=True))
        tilef.write_text(json.dumps(tmap, indent=2, sort_keys=True))
        self._prune_empty_dirs(base)

    @staticmethod
    def _prune_empty_dirs(base: Path) -> None:
        for d in sorted([p for p in base.iterdir() if p.is_dir()], reverse=True):
            try:
                next(d.iterdir())
            except StopIteration:
                d.rmdir()
            except Exception:
                pass

    def _reconcile_removals(self, tracked: set, m: Manifest) -> list:
        """Remove installed packs that are no longer in the tracked selection so
        the drive matches what the user chose (the picker is the desired state).
        Returns the ids removed."""
        removed = []
        for pid in self.inventory.ids():
            if pid in tracked:
                continue
            kind = (self.inventory.get(pid) or {}).get("kind", "")
            if not kind:
                ents = m.for_id(pid)
                kind = ents[0].kind if ents else ""
            try:
                self.remove_pack(pid, kind, m)
            except Exception as e:
                self.log(f"  could not remove {pid}: {e}")
                continue
            self.inventory.remove(pid)
            removed.append(pid)
            self.log(f"  removed {pid} ({kind})")
        return removed

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
        tracked = self._tracked_ids(m)
        # Reconcile: drop installed packs no longer selected, so the drive
        # matches the desired set (the picker is the source of truth). Never
        # during a dry run.
        if not dry_run:
            for pid in self._reconcile_removals(set(tracked), m):
                results.append(PackStatus(pid, MISSING, "removed (deselected)"))
        for pid in tracked:
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
