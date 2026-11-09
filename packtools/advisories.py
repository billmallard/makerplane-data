# SPDX-License-Identifier: Apache-2.0
"""The safety advisory feed: schema, validation, version matching (BACKLOG L5).

One small JSON document, ``advisories.json``, published at the bucket root
beside ``manifest.json`` and signed with the same key by the same
``packtools.signing`` code (``advisories.json.minisig``). A device that opts
in fetches it on its normal update schedule and matches it against what it
has installed. The match happens on the device. The request is a plain GET of
a fixed URL, identical for every device, so the feed never learns who is
running what. There is no registry and nothing is sent back
(docs/advisories.md).

Schema ``advisory-feed/1``::

    {
      "schema": "advisory-feed/1",
      "generated": "2026-11-08T00:00:00Z",
      "sequence": 1,
      "advisories": [
        {
          "id": "MPA-2026-001",
          "issued": "2026-11-08",
          "severity": "warning",
          "title": "...",
          "description": "...",
          "action": "...",
          "affects": [
            {"software": "pyefis", "introduced": "2.1.0", "fixed": "2.1.3"},
            {"pack": "obstacles-conus", "cycles": ["260611"]}
          ]
        }
      ]
    }

``sequence`` increases by one on every publish. A device refuses a feed
whose sequence is lower than the one it already trusts: a signature proves
who signed, not when, and a replayed old feed would otherwise hide a newer
advisory. An empty ``advisories`` list is the normal state.

A software target covers ``introduced <= v < fixed``; either bound may be
left out (no ``introduced``: every version before ``fixed``; no ``fixed``: no
fixed release yet). ``versions`` lists exact versions instead. A pack target
lists the affected cycles of one pack id.

Shared by the build side (strict validation before signing) and the device
(lenient reading after verifying), like ``packtools.manifest``.
"""

from __future__ import annotations

import datetime as _dt
import json
import re

SCHEMA = "advisory-feed/1"
FEED_KEY = "advisories.json"
FEED_SIG_KEY = "advisories.json.minisig"

# Most severe first. The words pilots already read on an annunciator panel
# (warning / caution / advisory); the meanings are in docs/advisories.md.
SEVERITIES = ("warning", "caution", "advisory")

ID_RE = re.compile(r"^MPA-\d{4}-\d{3,}$")
_VERSION_RE = re.compile(r"^(\d+(?:\.\d+)*)(.*)$")
_TIME_FMT = "%Y-%m-%dT%H:%M:%SZ"

# Match results, per advisory and per target.
YES, NO, UNKNOWN = "yes", "no", "unknown"


class FeedError(ValueError):
    """The feed is malformed. The build side refuses to sign it."""


# --------------------------------------------------------------------------
# versions
# --------------------------------------------------------------------------

def parse_version(v) -> tuple | None:
    """``(release, final)`` for a version string, or None when it has no
    leading numeric release. ``release`` is the int tuple with trailing zeros
    dropped (``2.1`` == ``2.1.0``); ``final`` is False for anything with a
    suffix other than a ``+local`` label (``2.1.3rc1``, ``2.1.3.dev0``,
    ``2.1.3-test``), which sorts below the release it precedes."""
    if not isinstance(v, str):
        return None
    m = _VERSION_RE.match(v.strip())
    if not m:
        return None
    release = [int(x) for x in m.group(1).split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    suffix = m.group(2)
    return tuple(release), suffix == "" or suffix.startswith("+")


def _bound_ok(v) -> bool:
    p = parse_version(v)
    return p is not None and p[1] and v == v.strip()


def software_match(target: dict, version: str | None) -> str:
    """YES / NO / UNKNOWN for one software target against an installed
    version (None = could not tell what is installed)."""
    if version is None:
        return UNKNOWN
    if "versions" in target:
        mine = parse_version(version)
        return YES if any(version == v or (mine and parse_version(v) == mine)
                          for v in target["versions"]) else NO
    mine = parse_version(version)
    if mine is None:
        return UNKNOWN
    lo, hi = target.get("introduced"), target.get("fixed")
    # A pre-release counts as inside the range at both ends: one of the
    # introducing release may already carry the defect, one of the fixing
    # release may not carry the fix yet.
    if lo is not None and mine[0] < parse_version(lo)[0]:
        return NO
    if hi is not None and mine >= (parse_version(hi)[0], True):
        return NO
    return YES


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def _target_errors(t, where: str) -> list[str]:
    if not isinstance(t, dict):
        return [f"{where}: target is not an object"]
    if ("software" in t) == ("pack" in t):
        return [f"{where}: target needs exactly one of 'software' or 'pack'"]
    errs = []
    if "software" in t:
        if not isinstance(t["software"], str) or not t["software"]:
            errs.append(f"{where}: software must be a component name")
        if "versions" in t:
            if "introduced" in t or "fixed" in t:
                errs.append(f"{where}: give versions, or introduced/fixed, not both")
            vs = t["versions"]
            if not (isinstance(vs, list) and vs and all(isinstance(v, str) and v for v in vs)):
                errs.append(f"{where}: versions must be a non-empty list of strings")
        for k in ("introduced", "fixed"):
            if k in t and not _bound_ok(t[k]):
                errs.append(f"{where}: {k} {t[k]!r} is not a release version (e.g. 2.1.3)")
        if (not errs and "introduced" in t and "fixed" in t
                and parse_version(t["introduced"]) >= parse_version(t["fixed"])):
            errs.append(f"{where}: introduced must be lower than fixed")
    else:
        if not isinstance(t["pack"], str) or not t["pack"]:
            errs.append(f"{where}: pack must be a pack id")
        cs = t.get("cycles")
        if not (isinstance(cs, list) and cs and all(isinstance(c, str) and c for c in cs)):
            errs.append(f"{where}: cycles must be a non-empty list of strings")
    return errs


def advisory_errors(a) -> list[str]:
    """Problems with one advisory entry; empty = valid."""
    if not isinstance(a, dict):
        return ["advisory is not an object"]
    where = a.get("id") if isinstance(a.get("id"), str) else "advisory"
    errs = []
    if not isinstance(a.get("id"), str) or not ID_RE.match(a["id"]):
        errs.append(f"{where}: id must look like MPA-2026-001")
    try:
        _dt.date.fromisoformat(a.get("issued"))
    except (TypeError, ValueError):
        errs.append(f"{where}: issued must be an ISO date")
    if a.get("severity") not in SEVERITIES:
        errs.append(f"{where}: severity must be one of {', '.join(SEVERITIES)}")
    for k in ("title", "description", "action"):
        if not isinstance(a.get(k), str) or not a[k].strip():
            errs.append(f"{where}: {k} must be non-empty text")
    affects = a.get("affects")
    if not isinstance(affects, list) or not affects:
        errs.append(f"{where}: affects must be a non-empty list")
    else:
        for i, t in enumerate(affects):
            errs += _target_errors(t, f"{where} affects[{i}]")
    return errs


def feed_errors(obj) -> list[str]:
    """Strict check of a whole feed (the build side runs this before it
    signs). Empty = valid."""
    if not isinstance(obj, dict):
        return ["feed is not an object"]
    errs = []
    if obj.get("schema") != SCHEMA:
        errs.append(f"schema must be {SCHEMA!r}")
    try:
        _dt.datetime.strptime(obj.get("generated"), _TIME_FMT)
    except (TypeError, ValueError):
        errs.append("generated must be YYYY-MM-DDTHH:MM:SSZ")
    seq = obj.get("sequence")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
        errs.append("sequence must be a positive integer")
    advs = obj.get("advisories")
    if not isinstance(advs, list):
        errs.append("advisories must be a list (empty when there are none)")
        return errs
    seen = set()
    for a in advs:
        errs += advisory_errors(a)
        aid = a.get("id") if isinstance(a, dict) else None
        if aid in seen:
            errs.append(f"{aid}: duplicate id")
        seen.add(aid)
    return errs


def to_bytes(obj: dict) -> bytes:
    """Canonical bytes, the same form as ``Manifest.to_bytes``. Sign and
    publish THESE bytes; the device verifies what it downloaded and never
    re-serializes."""
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)
            + "\n").encode("utf-8")


def empty_feed(sequence: int, generated: str) -> dict:
    return {"schema": SCHEMA, "generated": generated, "sequence": sequence,
            "advisories": []}


# --------------------------------------------------------------------------
# publish (build side)
# --------------------------------------------------------------------------

def load_file(path) -> dict:
    """Read and strictly validate a feed file (the reviewed source of truth,
    ``advisories/feed.json``). Raises FeedError listing every problem."""
    from pathlib import Path
    try:
        obj = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise FeedError(f"{path}: {e}") from None
    errs = feed_errors(obj)
    if errs:
        raise FeedError(f"{path}: " + "; ".join(errs))
    return obj


def publish(store, secret, obj: dict, *, sign, dry_run: bool = False,
            log=print) -> str:
    """Sign and upload a validated feed beside the manifest. Returns
    "published", "unchanged" or "dry-run".

    The live feed, if any, must verify against the signing key before its
    sequence is trusted, and the new feed's sequence must be higher: a
    device refuses a lower one, so publishing it would only strand every
    device on the old feed. Re-publishing identical bytes is a no-op."""
    from . import signing
    errs = feed_errors(obj)
    if errs:
        raise FeedError("; ".join(errs))
    raw = to_bytes(obj)
    live = store.get_bytes(FEED_KEY)
    if live is not None:
        live_sig = store.get_bytes(FEED_SIG_KEY)
        if live_sig is None:
            raise FeedError(f"live {FEED_KEY} has no {FEED_SIG_KEY}; refusing to publish over it")
        try:
            signing.verify(live, live_sig.decode("ascii"), secret.public_key_text())
        except Exception as e:
            raise FeedError(f"live {FEED_KEY} does not verify against the signing key: {e}") from None
        if live == raw:
            log(f"advisories: live feed is already sequence {obj['sequence']}; nothing to do")
            return "unchanged"
        live_seq = json.loads(live).get("sequence")
        if isinstance(live_seq, int) and obj["sequence"] <= live_seq:
            raise FeedError(f"sequence {obj['sequence']} is not above the live {live_seq}; "
                            "bump it (devices refuse a lower one)")
    n = len(obj["advisories"])
    if dry_run:
        log(f"advisories: would publish sequence {obj['sequence']} ({n} advisory(ies)); dry run")
        return "dry-run"
    sig = sign(raw, secret, trusted_comment=f"advisories sequence {obj['sequence']} "
                                            f"generated {obj['generated']}")
    store.put_bytes(FEED_KEY, raw, content_type="application/json")
    store.put_bytes(FEED_SIG_KEY, sig.encode("ascii"), content_type="text/plain")
    log(f"advisories: published sequence {obj['sequence']} ({n} advisory(ies)), signed")
    return "published"
