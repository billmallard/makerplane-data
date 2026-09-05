# SPDX-License-Identifier: Apache-2.0
"""Source URL builders — pinned against URLs verified live on the FAA
servers 2026-06-14 (see the Phase B research)."""

import datetime as dt

import pytest

from packtools import sources
from packtools.cycles import Cycle, airac_cycle, dof_cycle


def C(eff, exp, cyc="x"):
    return Cycle(cyc, dt.date.fromisoformat(eff), dt.date.fromisoformat(exp))


def test_nasr_apt_url_zero_padded_day_abbrev_month():
    # 11_Jun_2026 and 04_Sep_2025 both returned 206 on the live server.
    assert sources.nasr_apt_csv_url(C("2026-06-11", "2026-07-09")) == \
        "https://nfdc.faa.gov/webContent/28DaySub/extra/11_Jun_2026_APT_CSV.zip"
    assert sources.nasr_apt_csv_url(C("2025-09-04", "2025-10-02")) == \
        "https://nfdc.faa.gov/webContent/28DaySub/extra/04_Sep_2025_APT_CSV.zip"


def test_nasr_url_is_locale_independent():
    # Even if the runner's locale were non-English, month must be 'Jun'.
    url = sources.nasr_apt_csv_url(C("2026-06-11", "2026-07-09"))
    assert "11_Jun_2026" in url and "June" not in url


def test_nasr_navaid_urls_are_the_three_extra_zips():
    urls = sources.nasr_navaid_csv_urls(C("2026-06-11", "2026-07-09"))
    assert urls == (
        "https://nfdc.faa.gov/webContent/28DaySub/extra/11_Jun_2026_NAV_CSV.zip",
        "https://nfdc.faa.gov/webContent/28DaySub/extra/11_Jun_2026_FIX_CSV.zip",
        "https://nfdc.faa.gov/webContent/28DaySub/extra/11_Jun_2026_AWY_CSV.zip",
    )


def test_cifp_url():
    assert sources.cifp_url(C("2026-06-11", "2026-07-09")) == \
        "https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_260611.zip"


def test_dof_url_is_the_daily_product():
    assert sources.dof_url(C("2026-06-11", "2026-08-06")) == \
        "https://aeronav.faa.gov/Obst_Data/DAILY_DOF_CSV.ZIP"


def test_current_cycle_url_exists_pattern():
    # The cycle math feeds the URL: today's AIRAC effective -> FAA filename.
    cur = airac_cycle(dt.date(2026, 6, 14))
    assert "11_Jun_2026_APT_CSV.zip" in sources.nasr_apt_csv_url(cur)
    dof = dof_cycle(dt.date(2026, 6, 14))
    assert sources.dof_url(dof).endswith("DAILY_DOF_CSV.ZIP")


def test_implemented_sources_exclude_cifp():
    impl = {s.pack_id for s in sources.cyclical_sources()}
    assert "airports-conus" in impl and "obstacles-conus" in impl
    assert "navaids-conus" in impl
    assert "cifp-conus" not in impl                       # deferred (GPL indexer)
    assert "cifp-conus" in {s.pack_id for s in sources.cyclical_sources(include_deferred=True)}


def test_source_license_fields_default_empty():
    # Additive: a Source built without license kwargs (as every call site
    # predating this field does) still constructs and defaults to "".
    src = sources.Source(pack_id="x", kind="navdata", cadence="airac",
                         url_for=sources.nasr_apt_csv_url, builder="airports",
                         attribution="test")
    assert src.license == "" and src.license_url == ""


def test_faa_sources_carry_explicit_public_domain_license():
    for pack_id in ("airports-conus", "obstacles-conus", "navaids-conus", "cifp-conus"):
        src = sources.SOURCES[pack_id]
        assert src.license == "LicenseRef-us-public-domain"
        # No copyright attaches to a US Government work, so there is no
        # license text to cite -- an empty license_url is the honest value.
        assert src.license_url == ""


# --- openAIP airspace (AER-547) -------------------------------------------

def test_airspace_sources_carry_openaip_license():
    assert sources.AIRSPACE_SOURCES  # a small, non-empty starting set
    for src in sources.AIRSPACE_SOURCES.values():
        assert src.kind == "airspace"
        assert src.builder == "airspace"
        assert src.attribution == "openAIP (CC BY-NC 4.0)"
        assert src.license == "CC-BY-NC-4.0"
        assert src.license_url == "https://creativecommons.org/licenses/by-nc/4.0/"


def test_airspace_sources_keyed_and_regioned_by_country():
    for pack_id, src in sources.AIRSPACE_SOURCES.items():
        assert pack_id == f"airspace-{src.regions[0]}"
        assert src.regions[0] in sources.OPENAIP_COUNTRIES


def test_airspace_sources_excluded_from_cyclical_pipeline():
    # 'static' isn't a cadence cycles.current_and_next() understands -- these
    # must never reach run_cyclical.py's CyclicalRunner, or the nightly cron
    # crashes on the first cycle lookup (the class of incident CLAUDE.md's
    # "new kind merges before publish" rule exists to prevent).
    cyclical_ids = {s.pack_id for s in sources.cyclical_sources(include_deferred=True)}
    assert cyclical_ids.isdisjoint(sources.AIRSPACE_SOURCES)
    for src in sources.AIRSPACE_SOURCES.values():
        assert src.pack_id not in sources.SOURCES


def test_airspace_static_export_url_not_fabricated():
    # The listing page 403s scripted fetches (docs/openaip_evaluation.md);
    # rather than guess a URL, this must fail loudly until it's verified.
    with pytest.raises(NotImplementedError):
        sources.openaip_static_export_url()


def test_openaip_airspace_sources_generator_takes_a_custom_country_table():
    custom = sources.openaip_airspace_sources({"nz": "New Zealand"})
    assert set(custom) == {"airspace-nz"}
    assert custom["airspace-nz"].regions == ("nz",)
