# SPDX-License-Identifier: Apache-2.0
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


def nasr_navaid_csv_urls(c: Cycle) -> tuple[str, str, str]:
    """The three 28DaySub 'extra' zips a navaids pack is built from
    (navaids, fixes, airways). Same URL family as APT, verified live
    2026-07-04 for the 11_Jun_2026 cycle."""
    base = "https://nfdc.faa.gov/webContent/28DaySub/extra/"
    day = _faa_day(c.effective)
    return (f"{base}{day}_NAV_CSV.zip",
            f"{base}{day}_FIX_CSV.zip",
            f"{base}{day}_AWY_CSV.zip")


def cifp_url(c: Cycle) -> str:
    return (f"https://aeronav.faa.gov/Upload_313-d/cifp/"
            f"CIFP_{c.effective:%y%m%d}.zip")


def dof_url(c: Cycle) -> str:
    # The DOF "DAILY" product is always the latest; we snapshot it on the
    # 56-day cycle boundary and tag it with that cycle for expiry purposes.
    return "https://aeronav.faa.gov/Obst_Data/DAILY_DOF_CSV.ZIP"


@dataclass(frozen=True)
class Source:
    """A cyclical dataset: how to name its pack, fetch it, and build it.

    ``url_for`` may return one URL or a tuple of URLs; multi-URL sources
    (navaids = NAV + FIX + AWY) have every archive extracted into the same
    input directory before the builder runs."""
    pack_id: str
    kind: str                       # packmeta kind
    cadence: str                    # 'airac' | 'dof' | 'static' ('static':
                                     # non-cyclical, re-issued by edition tag
                                     # like terrain/water -- never fed to
                                     # cycles.current_and_next(), see
                                     # AIRSPACE_SOURCES below)
    url_for: Callable[[Cycle], "str | tuple[str, ...]"]
    builder: str                    # key into packtools.build.BUILDERS
    attribution: str
    # license/license_url: additive, machine-checkable counterpart to
    # attribution (free text) -- see packmeta.PackMeta. "" means unspecified.
    license: str = ""
    license_url: str = ""
    regions: tuple[str, ...] = ("conus",)
    # zip member to feed the builder, or "" if the archive is the input dir
    archive_member: str = ""
    implemented: bool = True        # CIFP is registered but deferred (see below)


# The cyclical sources the daily pipeline builds. Pack ids are stable and
# user-facing (they appear in the manifest and on the Pi).
# FAA sources are US Government works: no copyright attaches, so "public
# domain" isn't actually a license grant with text to cite -- license_url is
# left empty on purpose rather than pointing at something that isn't one.
_FAA_PUBLIC_DOMAIN = "LicenseRef-us-public-domain"

SOURCES: dict[str, Source] = {
    "airports-conus": Source(
        pack_id="airports-conus", kind="navdata", cadence="airac",
        url_for=nasr_apt_csv_url, builder="airports",
        attribution="FAA NASR (public domain)",
        license=_FAA_PUBLIC_DOMAIN,
    ),
    "obstacles-conus": Source(
        pack_id="obstacles-conus", kind="obstacles", cadence="dof",
        url_for=dof_url, builder="obstacles",
        attribution="FAA DOF (public domain)",
        license=_FAA_PUBLIC_DOMAIN,
    ),
    "navaids-conus": Source(
        pack_id="navaids-conus", kind="navaids", cadence="airac",
        url_for=nasr_navaid_csv_urls, builder="navaids",
        attribution="FAA NASR (public domain)",
        license=_FAA_PUBLIC_DOMAIN,
    ),
    # CIFP is a real source but its indexer lives in pyAvTools (GPL-2.0); we
    # do not vendor GPL into this Apache-2.0 component. Building CIFP packs is deferred
    # to a focused step (call faa-cifp-data's tooling at build time, or
    # reimplement the index). Registered so the orchestrator can see it.
    "cifp-conus": Source(
        pack_id="cifp-conus", kind="cifp", cadence="airac",
        url_for=cifp_url, builder="cifp",
        attribution="FAA CIFP (public domain)",
        license=_FAA_PUBLIC_DOMAIN,
        implemented=False,
    ),
}


def cyclical_sources(include_deferred: bool = False) -> list[Source]:
    return [s for s in SOURCES.values() if s.implemented or include_deferred]


# --- openAIP airspace (AER-547) -------------------------------------------
#
# Kept out of SOURCES/cyclical_sources() on purpose: airspace is static-first
# and non-cyclical (docs/openaip_evaluation.md), and Source.cadence here
# ('static') is not a value cycles.current_and_next() understands -- feeding
# these through run_cyclical.py's CyclicalRunner would raise on the first
# cycle lookup and take the nightly cron down, the exact class of incident
# CLAUDE.md's "a new pack kind must merge before its first R2 publish" rule
# exists to prevent. Build with packtools.build.airspace.build_airspace(),
# then packtool build-pack --kind airspace, entirely by hand, until there is
# a verified automated fetch path.

# openAIP's own listing/FAQ pages sit behind a JS shell that 403s scripted
# fetches (re-confirmed 2026-08-23, see docs/openaip_evaluation.md), so the
# static per-country export URL pattern is NOT independently verified. Do not
# invent one here; download exports by hand until it is.
def openaip_static_export_url(_cycle=None) -> str:
    raise NotImplementedError(
        "openAIP's static per-country export URL pattern is unverified live "
        "-- see docs/openaip_evaluation.md 'Refresh cadence'. Download the "
        "country's GeoJSON export by hand from https://www.openaip.net/data "
        "and pass the directory straight to "
        "packtools.build.airspace.build_airspace(); do not wire this into an "
        "automated fetch until the URL is confirmed.")


OPENAIP_ATTRIBUTION = "openAIP (CC BY-NC 4.0)"
OPENAIP_LICENSE = "CC-BY-NC-4.0"
OPENAIP_LICENSE_URL = "https://creativecommons.org/licenses/by-nc/4.0/"

# Small, defensible starting set -- not all ~243 ICAO countries in one pass
# (AER-547 scope). Each entry closes a concrete gap rather than padding the
# list: Canada shares the CONUS border, the most immediate "just past the
# edge" gap every FAA-only pack in this repo leaves today; the UK/Germany/
# France are the largest GA communities europe_coverage.md already flagged
# as the demand signal for going past CONUS at all.
OPENAIP_COUNTRIES: dict[str, str] = {
    "ca": "Canada",
    "gb": "United Kingdom",
    "de": "Germany",
    "fr": "France",
}


def openaip_airspace_sources(countries: dict[str, str] | None = None) -> dict[str, Source]:
    """One Source per country. The repetition across countries is data (a
    code), not structure, so a generator replaces what would otherwise be a
    hand-written Source literal per country -- see AER-547's own steer to
    "prefer deleting the repetition"."""
    countries = OPENAIP_COUNTRIES if countries is None else countries
    return {
        f"airspace-{cc}": Source(
            pack_id=f"airspace-{cc}", kind="airspace", cadence="static",
            url_for=openaip_static_export_url, builder="airspace",
            attribution=OPENAIP_ATTRIBUTION,
            license=OPENAIP_LICENSE, license_url=OPENAIP_LICENSE_URL,
            regions=(cc,),
            # Not deferred for a licensing/tooling reason (cf. CIFP) -- the
            # fetch URL simply isn't verified yet. Never reached through
            # cyclical_sources() regardless; see module note above.
            implemented=False,
        )
        for cc in countries
    }


#: Registered per-country airspace sources. Deliberately separate from
#: SOURCES -- see the module note above.
AIRSPACE_SOURCES: dict[str, Source] = openaip_airspace_sources()
