"""Cycle arithmetic — pinned against known FAA dates.

The anchor 2024-04-18 == AIRAC 2404 is confirmed against FAA CIFP filenames
and the published AIRAC schedule. These pins guard the whole 28-day grid.
"""

import datetime as dt

import pytest

from packtools import cycles


def D(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


# (effective date, expected AIRAC id) pairs from the published schedule.
AIRAC_PINS = [
    ("2024-01-25", "2401"),
    ("2024-04-18", "2404"),   # the anchor
    ("2026-01-22", "2601"),
    ("2026-05-14", "2605"),   # the local NASR data on this machine
    ("2026-06-11", "2606"),
    ("2026-07-09", "2607"),
]


@pytest.mark.parametrize("eff,expected", AIRAC_PINS)
def test_airac_id(eff, expected):
    assert cycles.airac_id(D(eff)) == expected


def test_airac_grid_is_28_day_spaced():
    a = cycles.airac_effective_on_or_before(D("2026-06-11"))
    b = cycles.airac_effective_on_or_before(D("2026-06-10"))  # day before rollover
    assert a == D("2026-06-11")
    assert (a - b).days == 28


def test_airac_effective_on_boundary_is_inclusive():
    # On an effective date, that cycle is current (not the previous one).
    assert cycles.airac_effective_on_or_before(D("2026-06-11")) == D("2026-06-11")


def test_airac_cycle_current_and_window():
    c = cycles.airac_cycle(D("2026-06-14"))   # "today" per session context
    assert c.cycle == "2606"
    assert c.effective == D("2026-06-11")
    assert c.expires == D("2026-07-09")
    assert c.covers(D("2026-06-14"))
    assert c.covers(D("2026-06-11"))          # inclusive lower bound
    assert not c.covers(D("2026-07-09"))      # exclusive upper bound


def test_current_and_next_airac_are_contiguous():
    cur, nxt = cycles.current_and_next("airac", today=D("2026-06-14"))
    assert cur.cycle == "2606"
    assert nxt.cycle == "2607"
    assert cur.expires == nxt.effective       # seamless rollover


def test_dof_is_56_day_and_aligned_to_airac_grid():
    cur, nxt = cycles.current_and_next("dof", today=D("2026-06-14"))
    assert (cur.expires - cur.effective).days == 56
    assert (nxt.effective - cur.effective).days == 56
    # DOF effective dates land on the AIRAC grid.
    assert cur.effective == cycles.airac_effective_on_or_before(cur.effective)


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        cycles.current_and_next("bogus", today=D("2026-06-14"))


def test_matches_legacy_faa_cifp_logic():
    """Reproduce faa-cifp-data/download.py's result for a sample date and
    confirm our clean reimplementation agrees."""
    today = D("2026-06-14")
    start = dt.date(2024, 4, 18)
    interval = 28
    days_diff = (start - today).days
    remainder = days_diff % interval
    legacy_current = today - dt.timedelta(days=interval - remainder)
    legacy_next = today + dt.timedelta(days=remainder)
    cur, nxt = cycles.current_and_next("airac", today=today)
    assert cur.effective == legacy_current
    assert nxt.effective == legacy_next
