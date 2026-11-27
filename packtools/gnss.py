# SPDX-License-Identifier: Apache-2.0
"""gnss pack: the GPS almanac and the active NANUs from USCG NAVCEN (RAIM1).

RAIM prediction (fix-gateway ``fixgw/raim/``, RAIM2/RAIM3) needs two things
the receiver does not hand it: a current almanac, to place the satellites at a
future time, and the satellite outages the operators have announced, to take
the affected satellites out of that geometry. Both are free US-government
data from the Coast Guard Navigation Center:

  * the YUMA almanac   navcen.uscg.gov/sites/default/files/gps/almanac/current_yuma.alm
  * the NANUs          navcen.uscg.gov/sites/default/files/gps/nanu/<YYYY>/<YYYYNNN>.nnu
                       (``current_nanu.nnu`` is the newest one only; the
                       ``gps-constellation`` page links each PRN's latest)

This module fetches them (or reads a recorded copy), parses them strictly and
writes one small JSON document, schema ``mp-gnss/1`` (docs/gnss.md). The
publishing pack is that document with ``pack_meta`` embedded as a top-level
key (packtools.packmeta.embed_json).

Parsing is fail-closed, because a permissive parser over an upstream feed
ships wrong data quietly: every almanac record must carry every field with a
value in the physical range of a GPS orbit, the constellation must have a
floor of satellites, the almanac must be recent, and a NANU of a type outside
NAVCEN's published list (``NANU_TYPES``) refuses the build rather than being
skipped. A refused build publishes nothing; the last good pack keeps serving
until it expires.

What the pack does NOT do: it does not apply GENERAL NANUs. Those are free
text (NAVCEN uses them, for example, to correct the dates of an earlier NANU),
so they are carried verbatim in ``general`` for a person to read, and their
corrections are not applied to ``outages``.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

SCHEMA = "mp-gnss/1"

NAVCEN = "https://navcen.uscg.gov"
YUMA_URL = f"{NAVCEN}/sites/default/files/gps/almanac/current_yuma.alm"
CURRENT_NANU_URL = f"{NAVCEN}/sites/default/files/gps/nanu/current_nanu.nnu"
CONSTELLATION_URL = f"{NAVCEN}/gps-constellation"
USER_AGENT = "makerplane-data-packtools (+https://navdata.aerocommons.org)"

ATTRIBUTION = "GPS almanac and NANUs: U.S. Coast Guard Navigation Center (NAVCEN)"
# A work of the US federal government (17 U.S.C. 105): no copyright in the US.
LICENSE = "LicenseRef-US-Government-Work"
LICENSE_URL = "https://www.usa.gov/government-copyright"


def nanu_url(number: int) -> str:
    return f"{NAVCEN}/sites/default/files/gps/nanu/{number // 1000}/{number}.nnu"


# NAVCEN "NANU Abbreviations and Descriptions" (read 2026-11-27). How each type
# acts on the outage list:
#   window  -- announces an outage window of its own
#   replace -- replaces the window of the NANU it references with its own
#   cancel  -- removes the window of the NANU it references
#   none    -- no outage (USABINIT closes a DECOM of the PRN's previous SVN)
NANU_TYPES = {
    "FCSTDV": "window",      # forecast Delta-V manoeuvre
    "FCSTMX": "window",      # forecast maintenance
    "FCSTUUFN": "window",    # forecast unusable until further notice
    "FCSTEXTD": "replace",   # forecast extended until further notice
    "FCSTSUMM": "replace",   # actual times of a completed forecast outage
    "FCSTRESCD": "replace",  # forecast rescheduled
    "FCSTCANC": "cancel",    # forecast cancelled
    "UNUSUFN": "window",     # unusable until further notice
    "UNUSABLE": "replace",   # closes an UNUSUFN with the actual times
    "UNUNOREF": "window",    # unusable, resolved before an UNUSUFN went out
    "DECOM": "window",       # removed from the constellation
    "USABINIT": "none",      # set healthy for the first time
    "LEAPSEC": "none",
    "GENERAL": "none",
    "LAUNCH": "none",
}
# Free-text types, carried verbatim in the pack's ``general`` list.
TEXT_TYPES = ("GENERAL", "LAUNCH", "LEAPSEC")

# Build-side gates (the producer's, not the consumer's: RAIM3 has its own
# almanac age limit). A healthy constellation has 31 satellites; fewer than
# 24 almanac records means a truncated or broken file, not a real constellation.
MIN_SATELLITES = 24
MAX_ALMANAC_AGE_DAYS = 14
# How long a pack stays current (its manifest ``expires``). The pack is rebuilt
# daily; the updater shows any pack within 7 days of its expiry as EXPIRES
# (white) and past it as EXPIRED (amber), so 14 days means: white once the
# device has gone a week without a fresh pack, amber after two. NAVCEN
# announces a scheduled outage 72 hours ahead, so a week-old pack can already
# miss one. The device never stops using an old pack; RAIM3 applies its own
# almanac age limit to the prediction.
VALID_DAYS = 14
# NANUs are walked back this far by their number's DTG; older long-lived states
# come from the constellation page's per-PRN links.
LOOKBACK_DAYS = 45

GPS_EPOCH = _dt.datetime(1980, 1, 6, tzinfo=_dt.timezone.utc)
_MON = {m: i + 1 for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"))}


class GnssError(ValueError):
    """The upstream data failed a check; nothing may be built from it."""


def _iso(t: _dt.datetime | None) -> str | None:
    return None if t is None else t.strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# YUMA almanac
# --------------------------------------------------------------------------

# YUMA label -> (json key, type, (low, high) physical range or None)
_YUMA_FIELDS = {
    "ID": ("prn", int, (1, 32)),
    "Health": ("health", int, (0, 63)),
    "Eccentricity": ("e", float, (0.0, 0.03)),
    "Time of Applicability(s)": ("toa_s", float, (0.0, 604800.0)),
    "Orbital Inclination(rad)": ("i0", float, (0.85, 1.05)),
    "Rate of Right Ascen(r/s)": ("omega_dot", float, (-1e-7, 1e-7)),
    "SQRT(A)  (m 1/2)": ("sqrt_a", float, (5100.0, 5200.0)),
    "Right Ascen at Week(rad)": ("omega0", float, (-3.1416, 3.1416)),
    "Argument of Perigee(rad)": ("w", float, (-3.1416, 3.1416)),
    "Mean Anom(rad)": ("m0", float, (-3.1416, 3.1416)),
    "Af0(s)": ("af0", float, (-1e-2, 1e-2)),
    "Af1(s/s)": ("af1", float, (-1e-8, 1e-8)),
    "week": ("week", int, (0, 1023)),
}
_YUMA_HEADER = re.compile(r"^\*+ Week (\d+) almanac for PRN-(\d+) \*+$")


def parse_yuma(text: str) -> list[dict]:
    """Every satellite record in a YUMA almanac, strictly. Raises GnssError on
    a missing or unknown field, a value outside a GPS orbit's range, a
    duplicate PRN, or records that disagree on the week or TOA."""
    records: list[dict] = []
    cur: dict | None = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        m = _YUMA_HEADER.match(line)
        if m:
            cur = {"_header": (int(m.group(1)), int(m.group(2))), "_line": n}
            records.append(cur)
            continue
        if cur is None or ":" not in line:
            raise GnssError(f"YUMA line {n}: unexpected {line[:60]!r}")
        # The label's internal spacing ("SQRT(A)  (m 1/2)") is part of it.
        label, value = (s.strip() for s in raw.split(":", 1))
        spec = _YUMA_FIELDS.get(label)
        if spec is None:
            raise GnssError(f"YUMA line {n}: unknown field {label!r}")
        key, typ, rng = spec
        try:
            v = typ(float(value)) if typ is int else typ(value)
        except ValueError:
            raise GnssError(f"YUMA line {n}: {label} is not a number: {value!r}") from None
        if rng and not rng[0] <= v <= rng[1]:
            raise GnssError(f"YUMA line {n}: {label} = {v} outside {rng}")
        if key in cur:
            raise GnssError(f"YUMA line {n}: {label} repeated")
        cur[key] = v

    out, seen = [], set()
    for r in records:
        week_h, prn_h = r.pop("_header")
        line = r.pop("_line")
        missing = sorted({k for k, *_ in _YUMA_FIELDS.values()} - set(r))
        if missing:
            raise GnssError(f"YUMA record at line {line}: missing {missing}")
        if (r["week"], r["prn"]) != (week_h, prn_h):
            raise GnssError(f"YUMA record at line {line}: header says week {week_h} "
                            f"PRN {prn_h}, body says week {r['week']} PRN {r['prn']}")
        if r["prn"] in seen:
            raise GnssError(f"YUMA: PRN {r['prn']} appears twice")
        seen.add(r["prn"])
        out.append(r)
    if len(out) < MIN_SATELLITES:
        raise GnssError(f"YUMA: {len(out)} satellites, below the floor of {MIN_SATELLITES}")
    if len({(r["week"], r["toa_s"]) for r in out}) != 1:
        raise GnssError("YUMA: records disagree on the almanac week/TOA")
    return sorted(out, key=lambda r: r["prn"])


def full_week(week10: int, near: _dt.datetime) -> int:
    """Resolve a 10-bit (mod 1024) GPS week to the full week nearest ``near``."""
    now = int((near - GPS_EPOCH).total_seconds() // 604800)
    k = round((now - week10) / 1024)
    return week10 + 1024 * k


# --------------------------------------------------------------------------
# NANUs
# --------------------------------------------------------------------------

@dataclass
class Nanu:
    number: int
    type: str
    dtg: str | None = None            # ISO UTC of the NANU's date-time group
    ref: int | None = None
    svn: int | None = None
    prn: int | None = None
    start: _dt.datetime | None = None
    stop: _dt.datetime | None = None  # None: until further notice
    subject: str = ""
    text: str = ""                    # the full message (free-text types)
    fields: dict = field(default_factory=dict)


_NANU_HEAD = re.compile(r"NOTICE ADVISORY TO NAVSTAR USERS \(NANU\)\s+(\d{7})")
_NANU_FIELD = re.compile(r"^\s+([A-Z][A-Z ]+?):\s*(.*?)\s*$")
_DTG = re.compile(r"^(\d{2})(\d{2})(\d{2})Z ([A-Z]{3}) (\d{4})$")
_DATE = re.compile(r"^(\d{2}) ([A-Z]{3}) (\d{4})$")


def _dtg(s: str, where: str) -> str:
    m = _DTG.match(s)
    if not m or m.group(4) not in _MON:
        raise GnssError(f"{where}: bad DTG {s!r}")
    d, hh, mm, mon, yyyy = m.groups()
    try:
        t = _dt.datetime(int(yyyy), _MON[mon], int(d), int(hh), int(mm),
                         tzinfo=_dt.timezone.utc)
    except ValueError:
        raise GnssError(f"{where}: bad DTG {s!r}") from None
    return _iso(t)


def _when(f: dict, end: str, where: str) -> _dt.datetime | None:
    """START/STOP calendar date + Zulu time, cross-checked against the JDAY.
    ``None`` for an until-further-notice stop."""
    jday = f.get(f"{end} JDAY", "")
    if end == "STOP" and jday == "UFN":
        return None
    date, hhmm = f.get(f"{end} CALENDAR DATE", ""), f.get(f"{end} TIME ZULU", "")
    m = _DATE.match(date)
    if not m or m.group(2) not in _MON or not re.fullmatch(r"\d{4}", hhmm) \
            or not jday.isdigit():
        raise GnssError(f"{where}: bad {end} date/time/JDAY "
                        f"({date!r}, {hhmm!r}, {jday!r})")
    try:
        t = _dt.datetime(int(m.group(3)), _MON[m.group(2)], int(m.group(1)),
                         int(hhmm[:2]), int(hhmm[2:]), tzinfo=_dt.timezone.utc)
    except ValueError:
        raise GnssError(f"{where}: bad {end} date/time ({date!r}, {hhmm!r})") from None
    if t.timetuple().tm_yday != int(jday):
        raise GnssError(f"{where}: {end} JDAY {jday} is not {date} "
                        f"(day {t.timetuple().tm_yday})")
    return t


def parse_nanu(text: str) -> Nanu:
    """One NANU message, strictly. Raises GnssError on an unknown type or a
    malformed field the type needs."""
    head = _NANU_HEAD.search(text)
    if not head:
        raise GnssError("NANU: no 'NOTICE ADVISORY TO NAVSTAR USERS (NANU) <number>' header")
    number = int(head.group(1))
    where = f"NANU {number}"
    tm = re.search(r"NANU TYPE:\s*([A-Z]+)", text)
    if not tm:
        raise GnssError(f"{where}: no NANU TYPE")
    typ = tm.group(1)
    if typ not in NANU_TYPES:
        raise GnssError(f"{where}: unknown NANU TYPE {typ!r} (not in NAVCEN's list; "
                        "add it to packtools.gnss.NANU_TYPES deliberately)")
    subj = re.search(r"^SUBJ:\s*(.*?)\s*$", text, re.M)
    n = Nanu(number=number, type=typ, subject=subj.group(1) if subj else "")
    if typ in TEXT_TYPES:
        n.text = text.strip()
        dtg = re.search(r"NANU DTG:\s*(\d{6}Z [A-Z]{3} \d{4})", text)
        n.dtg = _dtg(dtg.group(1), where) if dtg else None
        return n

    f = {}
    for line in text.splitlines():
        m = _NANU_FIELD.match(line)
        if m and m.group(1) not in f:
            f[m.group(1)] = m.group(2)
    n.fields = f
    if f.get("NANU NUMBER") != str(number):
        raise GnssError(f"{where}: NANU NUMBER field {f.get('NANU NUMBER')!r} "
                        "does not match the header")
    n.dtg = _dtg(f.get("NANU DTG", ""), where)
    ref = f.get("REFERENCE NANU", "N/A")
    if ref != "N/A":
        if not re.fullmatch(r"\d{7}", ref):
            raise GnssError(f"{where}: bad REFERENCE NANU {ref!r}")
        n.ref = int(ref)
    if NANU_TYPES[typ] in ("replace", "cancel") and n.ref is None:
        raise GnssError(f"{where}: {typ} must reference the NANU it acts on")
    for key, attr, hi in (("SVN", "svn", 999), ("PRN", "prn", 32)):
        v = f.get(key, "")
        if not v.isdigit() or not 1 <= int(v) <= hi:
            raise GnssError(f"{where}: bad {key} {v!r}")
        setattr(n, attr, int(v))
    if typ != "FCSTCANC":
        n.start = _when(f, "START", where)
        if typ in ("DECOM", "USABINIT"):
            n.stop = None
        else:
            n.stop = _when(f, "STOP", where)
        if n.stop is not None and n.stop < n.start:
            raise GnssError(f"{where}: STOP {n.stop} is before START {n.start}")
    return n


def outages(nanus: list[Nanu]) -> list[dict]:
    """Reduce NANUs to the outage windows still standing, in NANU-number order
    (not DTG order: NAVCEN has published a DTG earlier than the NANU it
    references). A replacing NANU takes its reference's place; a cancel
    removes it; USABINIT closes an open DECOM window of the same PRN on a
    different SVN (the PRN was reassigned)."""
    live: dict[int, dict] = {}
    for n in sorted(nanus, key=lambda n: n.number):
        act = NANU_TYPES[n.type]
        if act in ("replace", "cancel") and n.ref is not None:
            live.pop(n.ref, None)
        if act in ("window", "replace"):
            live[n.number] = {"prn": n.prn, "svn": n.svn, "type": n.type,
                              "start": n.start, "stop": n.stop, "nanu": n.number,
                              "ref": n.ref}
        if n.type == "USABINIT":
            for w in live.values():
                if w["prn"] == n.prn and w["svn"] != n.svn and w["stop"] is None:
                    w["stop"] = n.start
    return sorted(live.values(), key=lambda w: (w["start"], w["prn"], w["nanu"]))


# --------------------------------------------------------------------------
# fetching: live, or from a recorded copy
# --------------------------------------------------------------------------

Getter = Callable[[str], "bytes | None"]   # None: not found (404)


def http_getter(timeout: int = 30) -> Getter:
    import requests  # lazy

    def get(url: str) -> bytes | None:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content
    return get


def recording_getter(inner: Getter, record_dir: Path) -> Getter:
    """Wrap ``inner`` so every response (and every 404) is saved under
    ``record_dir`` in the layout :func:`fixture_getter` reads back."""
    record_dir = Path(record_dir)

    def get(url: str) -> bytes | None:
        body = inner(url)
        rel = _fixture_rel(url)
        path = record_dir / (rel + ".404" if body is None else rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"" if body is None else body)
        return body
    return get


def fixture_getter(root: Path) -> Getter:
    """Read a recorded NAVCEN copy (made with ``recording_getter``). A URL that
    was never recorded is an error, so a test cannot pass by fetching less."""
    root = Path(root)

    def get(url: str) -> bytes | None:
        rel = _fixture_rel(url)
        if (root / (rel + ".404")).is_file():
            return None
        p = root / rel
        if not p.is_file():
            raise GnssError(f"fixture has no recording of {url}")
        return p.read_bytes()
    return get


def _fixture_rel(url: str) -> str:
    if not url.startswith(NAVCEN + "/"):
        raise GnssError(f"not a NAVCEN URL: {url}")
    rel = url[len(NAVCEN) + 1:]
    # An extensionless page is saved as .page, not .html: it is a third-party
    # recording, not a source file of ours (scripts/spdx_check.py gates .html).
    return rel if "." in rel.rsplit("/", 1)[-1] else rel + ".page"


_SEED = re.compile(r"/sites/default/files/gps/nanu/(\d{4})/(\d{7})\.nnu")


def _seeds(html: bytes) -> set[int]:
    """Each PRN's latest NANU, as linked from the constellation status page."""
    out = {int(m.group(2)) for m in _SEED.finditer(html.decode("utf-8", "replace"))
           if m.group(1) == m.group(2)[:4]}
    if not out:
        raise GnssError("constellation page links no NANUs (layout changed?)")
    return out


def _last_of_year(get: Getter, year: int) -> int | None:
    """Highest NANU number published in ``year`` (they are dense from 001):
    a binary search, so a January build costs ~10 probes, not hundreds."""
    if get(nanu_url(year * 1000 + 1)) is None:
        return None
    lo, hi = 1, 999                          # lo exists; nothing above hi does
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if get(nanu_url(year * 1000 + mid)) is None:
            hi = mid - 1
        else:
            lo = mid
    return year * 1000 + lo


def collect(get: Getter, now: _dt.datetime, *,
            lookback_days: int = LOOKBACK_DAYS) -> dict:
    """Fetch the almanac and the NANUs. NANUs are walked back from the newest
    one by number until their DTG is older than ``lookback_days`` (crossing
    into last year when needed), plus each PRN's latest NANU from the
    constellation page, which carries a long-lived state (an UNUSUFN or a
    DECOM) older than the walk. Returns raw bytes keyed by URL plus the
    parsed NANUs."""
    raw: dict[str, bytes] = {}

    def must(url: str) -> bytes:
        body = get(url)
        if body is None:
            raise GnssError(f"NAVCEN returned 404 for {url}")
        raw[url] = body
        return body

    must(YUMA_URL)
    newest = parse_nanu(must(CURRENT_NANU_URL).decode("utf-8", "replace"))
    seeds = _seeds(must(CONSTELLATION_URL))
    cutoff = now - _dt.timedelta(days=lookback_days)
    nanus = {newest.number: newest}

    number = newest.number
    while True:
        number -= 1
        if number % 1000 == 0:                    # walked past YYYY001
            prev = _last_of_year(get, number // 1000 - 1)
            if prev is None:
                break
            number = prev
        url = nanu_url(number)
        body = get(url)
        if body is None:
            raise GnssError(f"NANU {number} is missing from the sequence ({url})")
        raw[url] = body
        n = parse_nanu(body.decode("utf-8", "replace"))
        nanus[n.number] = n
        if n.dtg and n.dtg < _iso(cutoff):
            break
    for s in sorted(seeds - set(nanus)):
        url = nanu_url(s)
        nanus[s] = parse_nanu(must(url).decode("utf-8", "replace"))
    return {"raw": raw, "nanus": sorted(nanus.values(), key=lambda n: n.number)}


# --------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------

def build(get: Getter, now: _dt.datetime, *, retrieved: str | None = None,
          lookback_days: int = LOOKBACK_DAYS) -> dict:
    """The ``mp-gnss/1`` document for ``now`` (UTC). ``retrieved`` stamps the
    sources (defaults to ``now``; a fixture build passes the recording time)."""
    got = collect(get, now, lookback_days=lookback_days)
    sats = parse_yuma(got["raw"][YUMA_URL].decode("ascii", "replace"))
    week = full_week(sats[0]["week"], now)
    toa = GPS_EPOCH + _dt.timedelta(weeks=week, seconds=sats[0]["toa_s"])
    age = (now - toa).total_seconds() / 86400
    if age > MAX_ALMANAC_AGE_DAYS:
        raise GnssError(f"almanac TOA {_iso(toa)} is {age:.1f} days before the build "
                        f"(limit {MAX_ALMANAC_AGE_DAYS}): NAVCEN's file is stale")
    if age < -7:
        raise GnssError(f"almanac TOA {_iso(toa)} is {-age:.1f} days after the build")

    cutoff = now - _dt.timedelta(days=lookback_days)
    windows = [w for w in outages(got["nanus"]) if w["stop"] is None or w["stop"] >= cutoff]
    newest = got["nanus"][-1]
    general = [{"nanu": n.number, "type": n.type, "dtg": n.dtg, "text": n.text}
               for n in got["nanus"] if n.type in TEXT_TYPES
               and (n.dtg is None or n.dtg >= _iso(cutoff))]
    stamp = retrieved or _iso(now)
    return {
        "schema": SCHEMA,
        "generated": _iso(now),
        "almanac": {
            "format": "yuma",
            "week": week,
            "week_mod1024": sats[0]["week"],
            "toa_s": sats[0]["toa_s"],
            # GPS time, not UTC: GPS runs ahead of UTC by the leap seconds
            # since 1980 (18 s since 2017).
            "toa_gps": _iso(toa),
        },
        "satellites": [{k: s[k] for k in ("prn", "health", "e", "toa_s", "i0",
                                          "omega_dot", "sqrt_a", "omega0", "w",
                                          "m0", "af0", "af1")} for s in sats],
        "nanu": {
            "latest": newest.number,
            "latest_dtg": newest.dtg,
            "considered": [n.number for n in got["nanus"]],
            "lookback_days": lookback_days,
        },
        "outages": [{**w, "start": _iso(w["start"]), "stop": _iso(w["stop"])}
                    for w in windows],
        "general": general,
        "sources": [{"url": u, "sha256": hashlib.sha256(b).hexdigest(),
                     "retrieved": stamp} for u, b in sorted(got["raw"].items())],
        "license": LICENSE,
        "license_url": LICENSE_URL,
        "attribution": ATTRIBUTION,
    }


def dumps(doc: dict) -> bytes:
    """Canonical bytes: sorted keys, fixed separators, trailing newline, so the
    same inputs give the same pack."""
    return (json.dumps(doc, sort_keys=True, indent=1, separators=(",", ": "))
            + "\n").encode("utf-8")


def write(doc: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(dumps(doc))
    return path
