# SPDX-License-Identifier: Apache-2.0
"""Panel-change cue: a config pull that changes the panel is shown until a
pilot acknowledges it on the ground (BACKLOG CERT17).

``pyefis-config-pull.service`` pulls the panel at boot, unattended, and
installs it if it changed. Rollback catches a config that crashes pyEfis, not
one that boots fine with an instrument bound to the wrong key. So every pull
that changes the installed config is recorded, and the ``panel_change`` block
of ``status.json`` carries it to the Data Status screen until someone
acknowledges that exact config there (or with ``pyefis-data panel-ack``).

The record is an append-only JSON-lines log beside the software-notice log,
written with the same fsynced ``O_APPEND`` writer (``acknowledge.append_line``),
so it survives a reset of either config tree and a power cut::

    $XDG_STATE_HOME/makerplane/panel_changes.jsonl
    (default ~/.local/state/makerplane/panel_changes.jsonl)

``MAKERPLANE_PANEL_LOG`` overrides the path. Three events:

* ``changed`` -- a pull installed a config whose sha256 differs from the one
  installed before (``trigger`` is ``boot`` for the boot unit, else ``manual``);
* ``rolled_back`` -- that config crashed pyEfis and the previous panel was
  restored, so the change is withdrawn and whatever was pending before is again;
* ``acknowledged`` -- a person acknowledged that config hash on the ground.

A pull that brings the same config hash is not a change and records nothing.
Reading never raises: an unreadable log reads as "nothing pending".
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import socket
from pathlib import Path

from . import acknowledge as _ack
from . import config as _config

SCHEMA = 1
EVENTS = ("changed", "rolled_back", "acknowledged")
TRIGGERS = ("boot", "manual")
VIA = ("tty", "caller")

# The word a terminal user types to acknowledge; an explicit word, as for the
# software notice.
ACK_WORD = "acknowledge"


def default_log_path() -> Path:
    env = os.environ.get("MAKERPLANE_PANEL_LOG")
    if env:
        return Path(os.path.expanduser(env))
    return _ack.default_log_path().parent / "panel_changes.jsonl"


def config_hash(text: str) -> str:
    """sha256 of the served config exactly as pulled (panel, preferences and
    fix-gateway blocks together)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now(now: _dt.datetime | None = None) -> str:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return now.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _valid(rec) -> bool:
    return (isinstance(rec, dict) and rec.get("schema") == SCHEMA
            and rec.get("event") in EVENTS
            and isinstance(rec.get("config_hash"), str)
            and isinstance(rec.get("timestamp"), str))


def read_events(path: Path | None = None) -> list[dict]:
    return [r for r in _ack.read_lines(path or default_log_path()) if _valid(r)]


def state(path: Path | None = None) -> dict | None:
    """The installed config's latest ``changed`` record, with ``acknowledged``
    filled in (a timestamp, or None while pending). None when no pull has ever
    been recorded. A ``rolled_back`` withdraws the change it names, exposing
    the one before it -- install snapshots go one level deep, and so does this."""
    stack: list[dict] = []
    for r in read_events(path):
        h = r["config_hash"]
        if r["event"] == "changed":
            stack.append(dict(r, acknowledged=None))
        elif r["event"] == "rolled_back":
            if stack and stack[-1]["config_hash"] == h:
                stack.pop()
        elif stack and stack[-1]["config_hash"] == h and not stack[-1]["acknowledged"]:
            stack[-1]["acknowledged"] = r["timestamp"]
    return stack[-1] if stack else None


def is_change(text: str, path: Path | None = None) -> bool:
    """True when the pulled text is not the config already installed."""
    cur = state(path)
    return cur is None or cur["config_hash"] != config_hash(text)


def record_change(text: str, version, trigger: str, *, path: Path | None = None,
                  now: _dt.datetime | None = None) -> dict | None:
    """Record an installed pull. Returns the record, or None when the config
    hash is the one already installed (an unchanged pull raises no cue).
    Raises if the log cannot be written."""
    if not is_change(text, path):
        return None
    rec = {"schema": SCHEMA, "event": "changed", "timestamp": _now(now),
           "config_hash": config_hash(text),
           "config_version": version if isinstance(version, int) else None,
           "trigger": trigger if trigger in TRIGGERS else "manual",
           "host": socket.gethostname() or "unknown"}
    _ack.append_line(rec, path or default_log_path())
    return rec


def record_rollback(text: str, *, path: Path | None = None,
                    now: _dt.datetime | None = None) -> None:
    rec = {"schema": SCHEMA, "event": "rolled_back", "timestamp": _now(now),
           "config_hash": config_hash(text)}
    _ack.append_line(rec, path or default_log_path())


def acknowledge(via: str, *, expect_hash: str | None = None,
                path: Path | None = None, now: _dt.datetime | None = None,
                user: str | None = None) -> dict:
    """Acknowledge the pending change. ``expect_hash`` is the hash the caller
    displayed: if the installed config is no longer that one, nothing is
    recorded, so a screen can never acknowledge a change it did not show.
    Raises LookupError when nothing is pending or the hash does not match."""
    cur = state(path)
    if cur is None or cur["acknowledged"]:
        raise LookupError("no panel change is waiting for acknowledgment")
    if expect_hash and expect_hash != cur["config_hash"]:
        raise LookupError("the installed panel config is not the one shown "
                          f"(installed {cur['config_hash'][:12]})")
    rec = {"schema": SCHEMA, "event": "acknowledged", "timestamp": _now(now),
           "config_hash": cur["config_hash"], "user": user or _ack._user(),
           "host": socket.gethostname() or "unknown",
           "via": via if via in VIA else "caller"}
    _ack.append_line(rec, path or default_log_path())
    return rec


def report(path: Path | None = None) -> dict:
    """The ``panel_change`` block of status.json. Never raises."""
    try:
        cur = state(path)
    except Exception:
        cur = None
    if cur is None:
        return {"pending": False, "config_hash": None, "config_version": None,
                "changed": None, "trigger": None, "acknowledged": None}
    return {"pending": not cur["acknowledged"], "config_hash": cur["config_hash"],
            "config_version": cur.get("config_version"),
            "changed": cur["timestamp"], "trigger": cur.get("trigger"),
            "acknowledged": cur["acknowledged"]}


def refresh_status(out: Path | None = None, path: Path | None = None) -> Path:
    """Rewrite only the ``panel_change`` block of status.json, keeping every
    other key, so the Data Status screen sees the change on pyEfis's next start
    without a catalog fetch. With no status.json yet, write one that says the
    pack status is not known. Atomic, like every status.json write."""
    out = Path(out) if out else _config.default_status_path()
    try:
        doc = json.loads(out.read_text("utf-8"))
        if not isinstance(doc, dict):
            raise ValueError
    except Exception:
        doc = {"ok": False, "error": "pack status not yet checked", "packs": []}
    doc["panel_change"] = report(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    os.replace(tmp, out)
    return out


def lines(block: dict) -> list[str]:
    """Plain-text lines for ``pyefis-data status`` / ``panel-ack``."""
    if not block.get("config_hash"):
        return []
    when = "at boot" if block.get("trigger") == "boot" else "by a config pull"
    ver = f" v{block['config_version']}" if block.get("config_version") else ""
    head = (f"panel changed {when}: config{ver} installed {block['changed']}, "
            f"hash {block['config_hash'][:12]}")
    if block["pending"]:
        return [head + " -- not yet acknowledged (pyefis-data panel-ack)"]
    return [head + f" -- acknowledged {block['acknowledged']}"]
