# SPDX-License-Identifier: Apache-2.0
"""Software-notice acknowledgment — the blocking accept at install/update time.

The notice is shown, and an explicit accept required, only on the ground: when
someone installs or updates through ``pyefis-data``. Each accept appends one
record to an append-only JSON-lines log so the acknowledgment is provable
later (what text, which software version, which packs, who, where, when).

The log lives under the XDG *state* directory, deliberately outside the config
trees (``~/.makerplane/`` for this updater, ``~/makerplane/pyefis/config`` for
pyEfis), so resetting either config does not erase it::

    $XDG_STATE_HOME/makerplane/acknowledgments.jsonl
    (default ~/.local/state/makerplane/acknowledgments.jsonl)

``MAKERPLANE_ACK_LOG`` overrides the path. The file is only ever opened with
``O_APPEND``; nothing here truncates or rewrites it. pyEfis (startup banner,
About screen) reads the same file; the record schema is documented in
docs/install_acknowledgment.md and asserted by ``validate_record``.

Reading never raises (construct-never-raises): an unreadable or corrupt log
reads as "no acknowledgment on record", which only ever means the notice is
shown again on the ground — never that anything is blocked in flight.
"""

from __future__ import annotations

import datetime as _dt
import getpass
import hashlib
import json
import os
import socket
from pathlib import Path

from . import __version__

SCHEMA = 1

# Bill approved this text 2026-10-30 (BACKLOG L2). Use it verbatim; changing a
# single character changes NOTICE_SHA256 and re-requires an accept everywhere.
NOTICE_ID = "pyefis-software-2026-10-30"
NOTICE_TEXT = (
    "pyEfis is experimental software for Experimental / Amateur-Built aircraft. "
    "It is not certified. It is provided as-is with no warranty, and any use of "
    "it is at your own risk."
)
NOTICE_SHA256 = hashlib.sha256(NOTICE_TEXT.encode("utf-8")).hexdigest()

# The component whose version an updater-side accept is keyed on.
COMPONENT = "pyefis-data"

# How the accept was given: typed at a terminal, asserted by a caller (the
# pyEfis installer / on-device picker) that displayed NOTICE_TEXT itself, or
# given automatically on a bench unit (`bench_auto_accept: true` in data.yaml).
# A bench accept is not a pilot's: nobody read the notice.
VIA = ("tty", "caller", "bench")
BENCH_VIA = "bench"

# The word a terminal user must type. An explicit word, not a default-yes key.
ACCEPT_WORD = "accept"

_REQUIRED = {
    "schema": int, "event": str, "timestamp": str, "notice_id": str,
    "notice_sha256": str, "notice_text": str, "software": dict, "packs": dict,
    "user": str, "host": str, "via": str, "trigger": str,
}


def default_log_path() -> Path:
    env = os.environ.get("MAKERPLANE_ACK_LOG")
    if env:
        return Path(os.path.expanduser(env))
    state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(state) / "makerplane" / "acknowledgments.jsonl"


def software_versions(extra: dict | None = None) -> dict:
    """``{component: version}``. Always carries this updater; adds pyEfis when it
    is pip-installed, and anything the caller names (``--software name=ver``)."""
    sw = {COMPONENT: __version__}
    try:
        from importlib.metadata import version
        sw["pyefis"] = version("pyefis")
    except Exception:
        pass
    sw.update(extra or {})
    return sw


def pack_versions(inventory) -> dict:
    """``{pack_id: current_cycle}`` of what is installed at the moment of accept."""
    out = {}
    try:
        for pid, e in inventory.data.get("packs", {}).items():
            if isinstance(e, dict) and e.get("current"):
                out[pid] = str(e["current"])
    except Exception:
        pass
    return out


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def make_record(*, via: str, trigger: str, packs: dict | None = None,
                software: dict | None = None, now: _dt.datetime | None = None,
                user: str | None = None, host: str | None = None) -> dict:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return {
        "schema": SCHEMA,
        "event": "accept",
        "timestamp": now.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "notice_id": NOTICE_ID,
        "notice_sha256": NOTICE_SHA256,
        "notice_text": NOTICE_TEXT,
        "software": software if software is not None else software_versions(),
        "packs": dict(packs or {}),
        "user": user or _user(),
        "host": host or socket.gethostname() or "unknown",
        "via": via,
        "trigger": trigger,
    }


def validate_record(rec) -> list[str]:
    """Schema problems with one record; empty list = valid. Shared by the
    reader (invalid lines are skipped) and the tests."""
    if not isinstance(rec, dict):
        return ["record is not an object"]
    errs = []
    for k, t in _REQUIRED.items():
        if k not in rec:
            errs.append(f"missing {k}")
        elif not isinstance(rec[k], t) or isinstance(rec[k], bool):
            errs.append(f"{k} is not {t.__name__}")
    if errs:
        return errs
    if rec["schema"] != SCHEMA:
        errs.append(f"unknown schema {rec['schema']}")
    if rec["event"] != "accept":
        errs.append(f"unknown event {rec['event']!r}")
    try:
        _dt.datetime.strptime(rec["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        errs.append("timestamp is not ISO 8601 UTC (YYYY-MM-DDTHH:MM:SSZ)")
    if hashlib.sha256(rec["notice_text"].encode("utf-8")).hexdigest() != rec["notice_sha256"]:
        errs.append("notice_sha256 does not match notice_text")
    if not isinstance(rec["software"].get(COMPONENT), str):
        errs.append(f"software.{COMPONENT} missing")
    if not all(isinstance(k, str) and isinstance(v, str)
               for d in (rec["software"], rec["packs"]) for k, v in d.items()):
        errs.append("software/packs must map strings to strings")
    if rec["via"] not in VIA:
        errs.append(f"unknown via {rec['via']!r}")
    return errs


def append_record(rec: dict, path: Path | None = None) -> Path:
    """Append one record as a single line. ``O_APPEND`` only — never truncates —
    and fsynced (file, then directory on first create) so a power cut right
    after an accept does not lose it. Raises on failure: an accept that cannot
    be recorded is not an accept."""
    errs = validate_record(rec)
    if errs:
        raise ValueError("refusing to write an invalid record: " + "; ".join(errs))
    p = Path(path) if path else default_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    created = not p.exists()
    line = (json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)
    if created:
        try:
            dfd = os.open(p.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    return p


def read_records(path: Path | None = None) -> list[dict]:
    """Every valid record, oldest first. Never raises; skips corrupt lines."""
    p = Path(path) if path else default_log_path()
    out = []
    try:
        with open(p, "rb") as f:
            for raw in f:
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    continue
                if not validate_record(rec):
                    out.append(rec)
    except Exception:
        pass
    return out


def find_acceptance(version: str | None = None, path: Path | None = None, *,
                    include_bench: bool = False) -> dict | None:
    """The latest record accepting the current notice for this updater version,
    or None. A new software version or a changed notice text needs a new accept.
    Bench accepts count only with ``include_bench`` (a bench unit asking), so a
    box that leaves bench mode is asked again like any real install."""
    version = version or __version__
    for rec in reversed(read_records(path)):
        if (rec["notice_sha256"] == NOTICE_SHA256
                and rec["software"].get(COMPONENT) == version
                and (include_bench or rec["via"] != BENCH_VIA)):
            return rec
    return None


def prompt(stdin, stdout) -> bool:
    """Show the full notice and require the user to type ACCEPT_WORD."""
    stdout.write("\n" + "-" * 72 + "\n" + NOTICE_TEXT + "\n" + "-" * 72 + "\n")
    stdout.write(f'Type "{ACCEPT_WORD}" to accept and continue, anything else to stop: ')
    stdout.flush()
    try:
        answer = stdin.readline()
    except Exception:
        return False
    return answer.strip().lower() == ACCEPT_WORD
