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

# What a typical US deployment tracks out of the box.
DEFAULT_PACKS = ["airports-conus", "obstacles-conus"]
DEFAULT_REGIONS = ["conus"]


def _default_root() -> Path:
    return Path(os.path.expanduser("~/makerplane-data"))


def _default_config_path() -> Path:
    return Path(os.path.expanduser("~/.makerplane/pyefis/data.yaml"))


@dataclass(frozen=True)
class Config:
    base_url: str = DEFAULT_BASE_URL
    root: Path = field(default_factory=_default_root)
    packs: tuple[str, ...] = tuple(DEFAULT_PACKS)
    regions: tuple[str, ...] = tuple(DEFAULT_REGIONS)
    auto_update: bool = True
    stage_next: bool = True          # pre-download the next cycle before it's effective
    storage_budget_gb: float | None = None

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
        return replace(cfg, **kw)
