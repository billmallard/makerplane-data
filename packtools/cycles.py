"""FAA data-cycle arithmetic.

Three upstream cadences matter:

  * AIRAC 28-day cycle  — NASR and CIFP share it. Effective dates fall on
    a fixed worldwide 28-day grid (Thursdays). Each cycle has a 4-digit
    AIRAC id, ``YYNN`` (year + sequence-within-year), e.g. ``2606``.
  * DOF 56-day cycle    — FAA Digital Obstacle File. Two AIRAC cycles long;
    not AIRAC-numbered, so we identify it by its effective date.
  * Quarterly / yearly  — OSM water, Copernicus terrain. Edition-tagged,
    not date-critical; handled outside this module.

This is a clean reimplementation of the (correct but cryptic) logic in
``makerplane/faa-cifp-data/download.py``. We keep its empirically-verified
anchor and pin the behaviour with unit tests, rather than copying the
roundabout modulo expression.

    Anchor: 2024-04-18 is AIRAC 2404 (confirmed against FAA CIFP filenames
    and the published AIRAC schedule). Every 28 days from any AIRAC date
    is another AIRAC date; this anchor pins the whole grid.

All functions are pure and timezone-naive (dates only). ``today`` is
always injectable so tests are deterministic and the cloud build is
reproducible.
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass

# --- anchors --------------------------------------------------------------

#: An AIRAC effective date known to be cycle 2404. Any 28-day multiple of
#: this is also an AIRAC effective date.
AIRAC_ANCHOR = _dt.date(2024, 4, 18)

AIRAC_DAYS = 28
DOF_DAYS = 56


def _today(today: _dt.date | None) -> _dt.date:
    return today if today is not None else _dt.date.today()


# --- AIRAC (28-day) -------------------------------------------------------

def _first_airac_of_year(year: int) -> _dt.date:
    """Earliest AIRAC effective date that falls in ``year``."""
    jan1 = _dt.date(year, 1, 1)
    # k = number of whole 28-day steps from the anchor needed to reach or
    # pass Jan 1 of the target year.
    k = math.ceil((jan1 - AIRAC_ANCHOR).days / AIRAC_DAYS)
    return AIRAC_ANCHOR + _dt.timedelta(days=k * AIRAC_DAYS)


def airac_id(effective: _dt.date) -> str:
    """The 4-digit AIRAC id (``YYNN``) for an AIRAC effective date.

    NN is the 1-based sequence of this cycle within its effective year.
    """
    seq = (effective - _first_airac_of_year(effective.year)).days // AIRAC_DAYS + 1
    return f"{effective.year % 100:02d}{seq:02d}"


def airac_effective_on_or_before(day: _dt.date) -> _dt.date:
    """The most recent AIRAC effective date on or before ``day``."""
    steps = (day - AIRAC_ANCHOR).days // AIRAC_DAYS
    return AIRAC_ANCHOR + _dt.timedelta(days=steps * AIRAC_DAYS)


# --- cycle value object ---------------------------------------------------

@dataclass(frozen=True)
class Cycle:
    """One edition of a cyclical dataset.

    ``cycle`` is the canonical identifier used in pack filenames and the
    manifest: the AIRAC id for AIRAC products, or the effective date in
    ``YYMMDD`` form for DOF.
    """
    cycle: str
    effective: _dt.date
    expires: _dt.date  # exclusive: data is current for [effective, expires)

    def covers(self, day: _dt.date) -> bool:
        return self.effective <= day < self.expires

    @property
    def days_until_expiry_from(self):
        def _from(day: _dt.date) -> int:
            return (self.expires - day).days
        return _from

    def to_meta(self) -> dict:
        return {
            "cycle": self.cycle,
            "effective": self.effective.isoformat(),
            "expires": self.expires.isoformat(),
        }


def airac_cycle(day: _dt.date) -> Cycle:
    """The AIRAC cycle current on ``day`` (NASR / CIFP)."""
    eff = airac_effective_on_or_before(day)
    return Cycle(airac_id(eff), eff, eff + _dt.timedelta(days=AIRAC_DAYS))


def dof_cycle(day: _dt.date) -> Cycle:
    """The DOF (56-day) cycle current on ``day``.

    DOF effective dates align to every other AIRAC date, so we snap to the
    AIRAC grid and step back to a 56-day boundary measured from the anchor.
    """
    steps = (day - AIRAC_ANCHOR).days // DOF_DAYS
    eff = AIRAC_ANCHOR + _dt.timedelta(days=steps * DOF_DAYS)
    return Cycle(eff.strftime("%y%m%d"), eff, eff + _dt.timedelta(days=DOF_DAYS))


def current_and_next(kind: str, today: _dt.date | None = None) -> tuple[Cycle, Cycle]:
    """Return ``(current, next)`` cycles for a dataset kind.

    The pipeline always tries to build both: the FAA publishes the next
    cycle ahead of its effective date so devices can stage it early and it
    activates automatically on rollover (the Garmin experience).

    ``kind`` is one of ``"airac"`` (NASR/CIFP) or ``"dof"``.
    """
    day = _today(today)
    if kind == "airac":
        cur = airac_cycle(day)
        nxt = airac_cycle(cur.expires)
    elif kind == "dof":
        cur = dof_cycle(day)
        nxt = dof_cycle(cur.expires)
    else:
        raise ValueError(f"unknown cycle kind: {kind!r} (expected 'airac' or 'dof')")
    return cur, nxt
