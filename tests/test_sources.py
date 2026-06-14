"""Source URL builders — pinned against URLs verified live on the FAA
servers 2026-06-14 (see the Phase B research)."""

import datetime as dt

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
    assert "cifp-conus" not in impl                       # deferred (GPL indexer)
    assert "cifp-conus" in {s.pack_id for s in sources.cyclical_sources(include_deferred=True)}
