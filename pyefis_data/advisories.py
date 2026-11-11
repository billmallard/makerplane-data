# SPDX-License-Identifier: Apache-2.0
"""Safety advisories on the device: fetch, verify, match, report (BACKLOG L5).

Opt-in (``advisories: true`` in data.yaml, off by default). When on, every
``pyefis-data update`` / ``import`` also fetches ``advisories.json`` and its
signature from the same origin as the manifest, verifies it with the same
embedded key, and caches it. The match against what is installed runs here,
on the device, and its result goes into ``status.json`` for the About/System
screen and into the updater's output.

What leaves the device: one GET of a fixed URL, with no query string, no body
and no added headers, identical for every device. No version, pack, install
id, user or host is ever sent. ``tests/test_advisories.py`` asserts this.

Trust: the same chain as the manifest. Unverified bytes are never cached or
shown. A feed whose ``sequence`` is lower than the cached one is refused and
the cached one kept (a replay must not hide a newer advisory). Offline, or on
any failure, the last verified feed stays in use and the failure is recorded.

Nothing here blocks anything. Every entry point is construct-never-raises:
a broken feed or a full disk ends as an ``error`` string in the report, never
as an exception that would stop an update or the status file.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

from packtools import advisories as feed
from packtools import signing

# Shown when the channel is switched on and offered by the installer or
# picker (`pyefis-data notice --json`, field `advisory_disclosure`). Wording
# is Bill's (BACKLOG L5); this is the proposed text.
DISCLOSURE = (
    "Safety advisories (optional, off unless you turn them on). When on, "
    "pyefis-data downloads a public list of safety advisories, signed by "
    "MakerPlane, each time it checks for data updates, and compares it with "
    "the software and data installed here. The comparison is done on this "
    "device. The download is the same for every aircraft and sends nothing "
    "about this aircraft, this device or what is installed on it. "
    "Turn on: pyefis-data advisories --enable. "
    "Turn off: pyefis-data advisories --disable."
)

CACHE = "advisories.json"
CACHE_SIG = "advisories.json.minisig"
STATE = "advisories-state.json"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_state(root: Path) -> dict:
    try:
        s = json.loads((root / STATE).read_text(encoding="utf-8"))
        return s if isinstance(s, dict) else {}
    except Exception:
        return {}


def _write_state(root: Path, **kw) -> None:
    try:
        s = _read_state(root)
        s.update(kw)
        root.mkdir(parents=True, exist_ok=True)
        _write_atomic(root / STATE, (json.dumps(s, indent=2, sort_keys=True) + "\n").encode())
    except Exception:
        pass


def load_cached(root: Path, public_key: str) -> dict | None:
    """The cached feed, re-verified, or None. Never raises."""
    try:
        raw = (root / CACHE).read_bytes()
        signing.verify(raw, (root / CACHE_SIG).read_text("ascii"), public_key)
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _fetch_verified(config, public_key: str, remote) -> tuple[bytes, str]:
    """Fetch the feed and its signature and verify the pair. A failed verify
    is retried once: the two objects are separate GETs, so a publish landing
    between them can hand back a new body with the old signature."""
    for attempt in (1, 2):
        raw = remote.get_bytes(config.advisories_url)
        sig = remote.get_bytes(config.advisories_sig_url).decode("ascii")
        try:
            signing.verify(raw, sig, public_key)
            return raw, sig
        except Exception:
            if attempt == 2:
                raise


def check(config, public_key: str, remote, *, log=print) -> dict:
    """Fetch, verify and cache the feed. Returns ``{"ok", "error",
    "refused"}``; never raises. Does nothing at all, and touches no network,
    when the channel is off."""
    root = Path(config.root)
    if not config.advisories:
        return {"ok": False, "error": None, "refused": None, "disabled": True}
    attempted = _now()
    try:
        raw, sig = _fetch_verified(config, public_key, remote)
    except FileNotFoundError:
        # A USB stick without the feed: normal, keep what we have.
        _write_state(root, attempted=attempted, error=None)
        return {"ok": False, "error": None, "refused": None, "absent": True}
    except Exception as e:
        msg = f"could not fetch a verified advisory feed ({type(e).__name__})"
        _write_state(root, attempted=attempted, error=msg)
        have = load_cached(root, public_key) is not None
        log(f"advisories: {msg}; "
            + ("using the last verified feed" if have else "no verified feed on this device yet"))
        return {"ok": False, "error": msg, "refused": None}
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("feed is not an object")
        if obj.get("schema") != feed.SCHEMA:
            raise ValueError(f"feed format {obj.get('schema')!r} is newer than this "
                             "pyefis-data; update pyefis-data to read it")
        seq = obj.get("sequence")
        if not isinstance(seq, int) or isinstance(seq, bool):
            raise ValueError("feed has no sequence")
    except Exception as e:
        msg = f"advisory feed unreadable: {e}"
        _write_state(root, attempted=attempted, error=msg)
        log(f"advisories: {msg}")
        return {"ok": False, "error": msg, "refused": None}
    cached = load_cached(root, public_key)
    cseq = cached.get("sequence") if cached else None
    if isinstance(cseq, int) and (seq < cseq or (seq == cseq and raw != (root / CACHE).read_bytes())):
        refused = {"offered": seq, "cached": cseq}
        msg = (f"refused advisory feed sequence {seq}: this device already "
               f"trusts sequence {cseq}")
        _write_state(root, attempted=attempted, error=msg, refused=refused)
        log(f"advisories: {msg}")
        return {"ok": False, "error": msg, "refused": refused}
    try:
        root.mkdir(parents=True, exist_ok=True)
        _write_atomic(root / CACHE_SIG, sig.encode("ascii"))
        _write_atomic(root / CACHE, raw)
    except Exception as e:
        msg = f"could not cache the advisory feed ({type(e).__name__})"
        _write_state(root, attempted=attempted, error=msg)
        return {"ok": False, "error": msg, "refused": None}
    _write_state(root, attempted=attempted, checked=attempted, error=None, refused=None)
    return {"ok": True, "error": None, "refused": None}


def local_versions(inventory, software: dict | None = None) -> dict:
    """What is installed here, for matching. Never leaves the device.
    ``software`` adds components the caller knows (``--software
    pyefis=2.1.0``); pyEfis is often not importable from the updater's own
    environment, and a component with no known version matches as
    ``unknown``, never as ``no``."""
    from .acknowledge import software_versions
    packs = {}
    try:
        for pid, e in inventory.data.get("packs", {}).items():
            if isinstance(e, dict):
                packs[pid] = [str(e[k]) for k in ("current", "staged") if e.get(k)]
    except Exception:
        pass
    return {"software": software_versions(software), "packs": packs}


def _target_result(t, versions: dict) -> str:
    if isinstance(t, dict) and isinstance(t.get("software"), str) and "pack" not in t:
        if feed._target_errors(t, ""):
            return feed.UNKNOWN
        return feed.software_match(t, versions["software"].get(t["software"]))
    if isinstance(t, dict) and isinstance(t.get("pack"), str) and "software" not in t:
        cycles = t.get("cycles") if isinstance(t.get("cycles"), list) else []
        have = versions["packs"].get(t["pack"], [])
        return feed.YES if any(c in cycles for c in have) else feed.NO
    return feed.UNKNOWN          # a target type this updater cannot read


def match(obj: dict | None, versions: dict) -> list[dict]:
    """The advisories that apply here (``applies`` "yes") or might
    (``"unknown"``), most severe first. An advisory this updater cannot fully
    read is reported as ``unknown`` rather than dropped."""
    out = []
    advs = obj.get("advisories") if isinstance(obj, dict) else None
    for a in advs if isinstance(advs, list) else []:
        if not isinstance(a, dict):
            continue
        affects = a.get("affects") if isinstance(a.get("affects"), list) else []
        targets = [dict(t, result=_target_result(t, versions)) if isinstance(t, dict)
                   else {"result": feed.UNKNOWN} for t in affects]
        results = {t["result"] for t in targets}
        if feed.YES in results:
            applies = feed.YES
        elif feed.UNKNOWN in results or not targets or feed.advisory_errors(a):
            applies = feed.UNKNOWN
        else:
            continue
        sev = a.get("severity") if a.get("severity") in feed.SEVERITIES else "warning"
        out.append({
            "id": str(a.get("id", "")), "issued": str(a.get("issued", "")),
            "severity": sev,
            "title": a.get("title") if isinstance(a.get("title"), str) else "",
            "description": a.get("description") if isinstance(a.get("description"), str) else "",
            "action": a.get("action") if isinstance(a.get("action"), str) else "",
            "applies": applies, "affects": targets,
        })
    rank = {s: i for i, s in enumerate(feed.SEVERITIES)}
    out.sort(key=lambda r: (r["applies"] != feed.YES, rank[r["severity"]], r["id"]))
    return out


def report(config, public_key: str, inventory, software: dict | None = None) -> dict:
    """The ``advisories`` block of status.json. Built from the cached,
    re-verified feed; fetches nothing. Never raises."""
    if not getattr(config, "advisories", False):
        return {"enabled": False}
    root = Path(config.root)
    try:
        state = _read_state(root)
        obj = load_cached(root, public_key)
        return {
            "enabled": True,
            "sequence": obj.get("sequence") if obj else None,
            "generated": obj.get("generated") if obj else None,
            "checked": state.get("checked"),
            "error": state.get("error") or (None if obj else "no verified advisory feed yet"),
            "active": match(obj, local_versions(inventory, software)),
        }
    except Exception as e:
        return {"enabled": True, "sequence": None, "generated": None, "checked": None,
                "error": f"advisory check failed ({type(e).__name__})", "active": []}


def lines(block: dict) -> list[str]:
    """Human lines for the updater's output. An empty feed prints nothing."""
    out = []
    for a in block.get("active", []) if block.get("enabled") else []:
        head = "ADVISORY" if a["applies"] == feed.YES else "ADVISORY (may apply)"
        out.append(f"{head} {a['id']} [{a['severity']}] {a['title']}")
        if a["applies"] != feed.YES:
            unknown = [t.get("software") or t.get("pack") or "?" for t in a["affects"]
                       if t.get("result") == feed.UNKNOWN]
            if unknown:
                out.append(f"  could not tell the installed version of: {', '.join(map(str, unknown))}")
        out.append(f"  action: {a['action']}")
    return out
