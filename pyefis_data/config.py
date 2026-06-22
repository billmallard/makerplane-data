"""On-Pi updater configuration — ``~/.makerplane/pyefis/data.yaml``.

Construct-never-raises (the pyEfis house rule): a missing or unreadable
config yields sensible defaults rather than an exception, so a fresh Pi
works with zero configuration and a typo can never brick the updater.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

# Production data origin (custom domain on Cloudflare R2). Overridable in
# data.yaml so the same build runs against the r2.dev URL or a staging bucket.
DEFAULT_BASE_URL = "https://navdata.aerocommons.org"

# The configuration manager (panel designs) lives on a different origin than the
# navdata base_url. A paired device pulls its screen config from here (#65).
DEFAULT_CONFIGURATOR_URL = "https://pyefis.aerocommons.org"

# Selection policy. A fresh Pi tracks all CORE NAVDATA kinds automatically
# (small, CONUS-wide, everyone wants them) and no bulk packs — terrain/charts
# are opt-in by region. `packs` adds explicit ids on top; nothing here ever
# forces a bulk download or restricts what the EFIS may use.
DEFAULT_TRACK_KINDS = ("navdata", "obstacles", "cifp")
DEFAULT_PACKS: tuple[str, ...] = ()      # explicit extra ids (none by default)
DEFAULT_REGIONS: tuple[str, ...] = ()    # opted-in bulk regions (none by default)


def _default_root() -> Path:
    return Path(os.path.expanduser("~/makerplane-data"))


def _default_config_path() -> Path:
    return Path(os.path.expanduser("~/.makerplane/pyefis/data.yaml"))


# Fixed, well-known path for the status JSON the EFIS reads. Deliberately NOT
# under the data root (which may live on a separate disk, e.g. /data on the
# M.2) so pyEfis can find it without knowing where the data lives.
def default_status_path() -> Path:
    return Path(os.path.expanduser("~/.makerplane/pyefis/status.json"))


@dataclass(frozen=True)
class Config:
    base_url: str = DEFAULT_BASE_URL
    root: Path = field(default_factory=_default_root)
    track_kinds: tuple[str, ...] = DEFAULT_TRACK_KINDS  # core kinds tracked automatically
    packs: tuple[str, ...] = DEFAULT_PACKS              # explicit extra pack ids
    regions: tuple[str, ...] = DEFAULT_REGIONS          # opted-in bulk regions
    auto_update: bool = True
    stage_next: bool = True          # pre-download the next cycle before it's effective
    storage_budget_gb: float | None = None
    # Device config (panel design) pull — set by `pyefis-data pair` (#65).
    configurator_url: str = DEFAULT_CONFIGURATOR_URL
    device_token: str | None = None
    device_id: int | None = None

    @property
    def manifest_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/manifest.json"

    @property
    def sig_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/manifest.json.minisig"

    def pack_url(self, filename: str) -> str:
        return f"{self.base_url.rstrip('/')}/packs/{filename}"

    @staticmethod
    def load(path: str | Path | None = None) -> "Config":
        """Load config, falling back to defaults on any problem. Never raises."""
        p = Path(path) if path else _default_config_path()
        cfg = Config()
        try:
            import yaml
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            return cfg
        except Exception as e:  # malformed yaml, perms, etc. — degrade, don't die
            import logging
            logging.getLogger(__name__).warning("data.yaml unreadable (%s); using defaults", e)
            return cfg

        kw = {}
        if isinstance(data.get("base_url"), str):
            kw["base_url"] = data["base_url"]
        if isinstance(data.get("root"), str):
            kw["root"] = Path(os.path.expanduser(data["root"]))
        if isinstance(data.get("track_kinds"), list):
            kw["track_kinds"] = tuple(str(x) for x in data["track_kinds"])
        if isinstance(data.get("packs"), list):
            kw["packs"] = tuple(str(x) for x in data["packs"])
        if isinstance(data.get("regions"), list):
            kw["regions"] = tuple(str(x) for x in data["regions"])
        if isinstance(data.get("auto_update"), bool):
            kw["auto_update"] = data["auto_update"]
        if isinstance(data.get("stage_next"), bool):
            kw["stage_next"] = data["stage_next"]
        if isinstance(data.get("storage_budget_gb"), (int, float)):
            kw["storage_budget_gb"] = float(data["storage_budget_gb"])
        if isinstance(data.get("configurator_url"), str):
            kw["configurator_url"] = data["configurator_url"]
        if isinstance(data.get("device_token"), str):
            kw["device_token"] = data["device_token"]
        if isinstance(data.get("device_id"), int):
            kw["device_id"] = data["device_id"]
        return replace(cfg, **kw)


def write_config(path: str | Path | None, updates: dict) -> Path:
    """Merge ``updates`` into the data.yaml at ``path`` and write it back
    atomically. The on-device pack picker calls this to persist the user's
    selection (``packs``) and storage ``root`` so the next auto-update tracks
    exactly what they chose -- the picker replaces hand-editing the file.

    Existing keys are preserved; inline comments are not (best-effort, by
    design -- once the picker owns the file a clean canonical form is fine).
    The write is atomic (temp + os.replace) so the updater never reads a
    half-written config."""
    import yaml
    p = Path(os.path.expanduser(str(path))) if path else _default_config_path()
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:                       # missing/unreadable/malformed: start fresh
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.update(updates)
    if isinstance(data.get("root"), Path):
        data["root"] = str(data["root"])
    p.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# MakerPlane navigation-data updater config.\n"
        "# Managed by `pyefis-data` -- the on-device pack picker writes here.\n"
        "# You can still hand-edit it; see the sample at\n"
        "# https://navdata.aerocommons.org/data.yaml.sample\n\n"
    )
    body = yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(header + body, encoding="utf-8")
    os.replace(tmp, p)
    return p
