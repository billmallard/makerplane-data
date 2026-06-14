"""Upstream data sources — the FAA URLs, cycle-aware.

One place for every upstream URL so that when the FAA moves a path (it
does), there is a single edit. URLs are built from a :class:`cycles.Cycle`
so current-and-next are derived, never hard-coded.

All URLs verified live 2026-06-14:
  * NASR APT CSV   nfdc.faa.gov/webContent/28DaySub/extra/<DD_Mon_YYYY>_APT_CSV.zip
  * CIFP           aeronav.faa.gov/Upload_313-d/cifp/CIFP_<YYMMDD>.zip
  * DOF (daily)    aeronav.faa.gov/Obst_Data/DAILY_DOF_CSV.ZIP

Date formatting is locale-independent on purpose (we format month names
ourselves) so a CI runner in any locale builds the same URL.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Callable

from .cycles import Cycle

# Explicit English month abbreviations — do NOT use strftime('%b'); that is
# locale-dependent and would silently break URLs on a non-English runner.
_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _faa_day(d: _dt.date) -> str:
    """FAA NASR 'extra' filename date stamp, e.g. 04_Sep_2025 / 11_Jun_2026."""
    return f"{d.day:02d}_{_MON[d.month - 1]}_{d.year}"


def nasr_apt_csv_url(c: Cycle) -> str:
    return (f"https://nfdc.faa.gov/webContent/28DaySub/extra/"
            f"{_faa_day(c.effective)}_APT_CSV.zip")


def cifp_url(c: Cycle) -> str:
    return (f"https://aeronav.faa.gov/Upload_313-d/cifp/"
            f"CIFP_{c.effective:%y%m%d}.zip")


def dof_url(c: Cycle) -> str:
    # The DOF "DAILY" product is always the latest; we snapshot it on the
    # 56-day cycle boundary and tag it with that cycle for expiry purposes.
    return "https://aeronav.faa.gov/Obst_Data/DAILY_DOF_CSV.ZIP"


@dataclass(frozen=True)
class Source:
    """A cyclical dataset: how to name its pack, fetch it, and build it."""
    pack_id: str
    kind: str                       # packmeta kind
    cadence: str                    # 'airac' | 'dof'
    url_for: Callable[[Cycle], str]
    builder: str                    # key into packtools.build.BUILDERS
    attribution: str
    regions: tuple[str, ...] = ("conus",)
    # zip member to feed the builder, or "" if the archive is the input dir
    archive_member: str = ""
    implemented: bool = True        # CIFP is registered but deferred (see below)


# The cyclical sources the daily pipeline builds. Pack ids are stable and
# user-facing (they appear in the manifest and on the Pi).
SOURCES: dict[str, Source] = {
    "airports-conus": Source(
        pack_id="airports-conus", kind="navdata", cadence="airac",
        url_for=nasr_apt_csv_url, builder="airports",
        attribution="FAA NASR (public domain)",
    ),
    "obstacles-conus": Source(
        pack_id="obstacles-conus", kind="obstacles", cadence="dof",
        url_for=dof_url, builder="obstacles",
        attribution="FAA DOF (public domain)",
    ),
    # CIFP is a real source but its indexer lives in pyAvTools (GPL-2.0); we
    # do not vendor GPL into this MIT repo. Building CIFP packs is deferred
    # to a focused step (call faa-cifp-data's tooling at build time, or
    # reimplement the index). Registered so the orchestrator can see it.
    "cifp-conus": Source(
        pack_id="cifp-conus", kind="cifp", cadence="airac",
        url_for=cifp_url, builder="cifp",
        attribution="FAA CIFP (public domain)",
        implemented=False,
    ),
}


def cyclical_sources(include_deferred: bool = False) -> list[Source]:
    return [s for s in SOURCES.values() if s.implemented or include_deferred]
