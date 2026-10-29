# SPDX-License-Identifier: Apache-2.0
"""The publish gate: every pack gets its test record before the manifest is
signed (testing-visibility plan sections 3 and 4, TV3).

Every path that adds a pack to the live manifest runs :func:`gate_one` on
the exact file it is about to upload: the cyclical orchestrator
(``run_cyclical``), and every caller of :func:`packtools.publish.publish`
(``build-pack --upload`` for roads and water, ``make-terrain --upload`` from
the QNAP entrypoint, ``make-plates --upload``). The gate:

1. refuses an (id, cycle) the signed live manifest already lists: a
   published record is immutable, so a rebuild needs a new cycle tag;
2. checks the entry's sha256 and size against the file;
3. finds the previous edition **from the signed live manifest** and nowhere
   else. No edition of the id listed means first edition; an edition listed
   but unreadable means no previous record, which blocks every blocking
   delta check. Nothing in config can declare a first edition;
4. builds the ``pack-report/1`` record in this job, from these bytes, with
   build notes only from the person who started the run (``notes``). Nothing
   fills a note from config, a default or a template, so an unattended run
   that warns is refused rather than noted;
5. confirms the record's ``pack.sha256`` equals the sha256 of the file being
   signed, and then gates on :func:`report.publish_blockers`, which takes the
   check set and the ``blocking`` flags from the catalogue, not the record.

A refused build gets its record uploaded under ``reports/rejected/`` with
``record: rejected`` (D4) and is not published. An accepted one gets its
record uploaded to ``reports/<id>/<cycle>/report.json``. Its manifest entry
carries ``report_url`` and ``report_sha256``, so the manifest signature
covers the record.

``packtools/report.py`` is the catalogue QA accepted (AER-4102); this module
only calls it.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import report as _report
from . import signing
from .build.errors import BuildError
from .manifest import Manifest, PackEntry, _rank_key

MANIFEST_KEY = "manifest.json"
SIG_KEY = "manifest.json.minisig"
REPORTS = "reports"


class GateError(BuildError):
    """The gate could not judge this pack at all (an already-published
    (id, cycle), an entry that does not match its file, an unknown check id in
    a note, an unverifiable live manifest). Nothing is published. A
    ``BuildError``, so the cyclical run counts it as a hard failure."""


class GateRefused(BuildError):
    """The gate judged the pack and refused it. Its ``rejected`` record has
    been uploaded; nothing else was. A ``BuildError``, so the cyclical run
    counts it as a hard failure rather than a WARN."""

    def __init__(self, refused: list["Gated"]):
        self.refused = refused
        super().__init__("; ".join(
            f"{g.entry.id} {g.entry.cycle} refused by the publish gate "
            f"({len(g.blockers)} blocker(s): {'; '.join(g.blockers)})" for g in refused))


# --- where records live ------------------------------------------------------

def report_key(pack_id: str, cycle: str) -> str:
    return f"{REPORTS}/{pack_id}/{cycle}/report.json"


def rejected_key(rec: dict) -> str:
    """``reports/rejected/<id>/<cycle>/<built.at>-<sha12>/report.json``: one
    key per refused attempt, so no rejected record is ever overwritten."""
    p = rec["pack"]
    at = re.sub(r"[^0-9TZ]", "", str((rec.get("built") or {}).get("at") or ""))
    return f"{REPORTS}/rejected/{p['id']}/{p['cycle']}/{at}-{p['sha256'][:12]}/report.json"


def report_url_for(entry: PackEntry) -> str:
    """The record's public URL, on the same origin as the pack: an entry URL
    ``<origin>/packs/<file>`` gives ``<origin>/reports/<id>/<cycle>/report.json``."""
    base = entry.url.rsplit("/", 1)[0]
    if base.endswith("/packs"):
        base = base[: -len("/packs")]
    return f"{base}/{report_key(entry.id, entry.cycle)}"


def _pack_key(entry: PackEntry) -> str:
    return f"packs/{entry.url.rsplit('/', 1)[1]}"


# --- the signed live manifest -----------------------------------------------

def load_signed(store, secret: signing.SecretKey) -> tuple[Manifest | None, dict]:
    """The live manifest, verified against the signing key's own public key
    before anything is decided from it. Returns ``(manifest, raw object)``,
    or ``(None, {"packs": []})`` when nothing has been published yet. A
    manifest with a missing or bad signature raises :class:`GateError`."""
    raw = store.get_bytes(MANIFEST_KEY)
    if raw is None:
        return None, {"packs": []}
    sig = store.get_bytes(SIG_KEY)
    if sig is None:
        raise GateError(f"{MANIFEST_KEY} has no {SIG_KEY}: refusing to decide anything from it")
    try:
        signing.verify(raw, sig.decode("ascii"), secret.public_key_text())
    except Exception as e:
        raise GateError(f"{MANIFEST_KEY} does not verify against the signing key: {e}") from None
    return Manifest.from_bytes(raw), json.loads(raw)


# --- the previous edition ------------------------------------------------------

def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def previous_record(store, live: Manifest | None, entry: PackEntry, *,
                    local: dict | None = None, scratch: Path | None = None,
                    log=print) -> tuple[dict | None, bool, str]:
    """``(previous record, first_edition, how)`` for ``entry``.

    The candidates are the editions of ``entry.id`` that the signed live
    manifest lists, plus ``local``: editions accepted earlier in this same
    job (``{(id, cycle): (PackEntry, record)}``). None at all is the only
    first edition. Otherwise the newest edition ranked below this one (else
    the newest listed) is the previous one, read from its bound record (hash
    checked against the signed ``report_sha256``) or, for an edition
    published before records existed, reported on now from its pack (hash
    checked against the signed ``sha256``). If neither can be read the
    previous record is ``None`` with ``first_edition`` False, so every
    blocking delta check blocks."""
    local = local or {}
    cands: dict[str, tuple[PackEntry, dict | None]] = {}
    for p in (live.packs if live else []):
        if p.id == entry.id and p.cycle != entry.cycle:
            cands[p.cycle] = (p, None)
    for (pid, cyc), (p, rec) in local.items():
        if pid == entry.id and cyc != entry.cycle:
            cands[cyc] = (p, rec)
    if not cands:
        return None, True, f"first edition: the signed manifest lists no edition of {entry.id}"
    below = [c for c in cands.values() if _rank_key(c[0]) < _rank_key(entry)]
    prev, rec = max(below or cands.values(), key=lambda c: _rank_key(c[0]))
    if rec is not None:
        return rec, False, f"previous edition {prev.cycle}: its record from this run"
    if prev.report_url:
        raw = store.get_bytes(report_key(prev.id, prev.cycle))
        if raw is not None and _sha256_bytes(raw) == prev.report_sha256:
            return (json.loads(raw), False,
                    f"previous edition {prev.cycle}: its published record (report_sha256 matches)")
        log(f"  previous {prev.id} {prev.cycle}: bound record "
            f"{'missing' if raw is None else 'does not match report_sha256'}; using its pack")
    own = scratch is None
    tmp = Path(tempfile.mkdtemp(prefix="gate-prev-")) if own else Path(scratch)
    dest = tmp / f"previous-{prev.id}-{prev.cycle}.pack"
    try:
        if not store.get_file(_pack_key(prev), dest):
            return None, False, (f"previous edition {prev.cycle} is listed but its pack "
                                 f"{_pack_key(prev)} is not in the store")
        if signing.sha256_file(dest) != prev.sha256:
            return None, False, (f"previous edition {prev.cycle}: the stored pack does not "
                                 "match the sha256 in the signed manifest")
        try:
            rec = _report.previous_from(dest)
        except Exception as e:
            return None, False, f"previous edition {prev.cycle}: cannot report on its pack ({e})"
        return rec, False, (f"previous edition {prev.cycle}: reported on now from its published "
                            "pack (sha256 matches; it has no bound record)")
    finally:
        dest.unlink(missing_ok=True)
        if own:
            shutil.rmtree(tmp, ignore_errors=True)


# --- build notes ---------------------------------------------------------------

def notes_for(pack_id: str, kind: str, notes: dict | None, *, is_zip: bool) -> dict:
    """The build notes that apply to one pack. ``notes`` keys are ``CHECK``
    (every pack of this run) or ``<pack id>:CHECK`` (one pack); values are
    what the person who started the run wrote for this edition. A key naming
    no check in the kind's catalogue would leave the warn it meant to explain
    unexplained, so it raises :class:`GateError`, as does an empty note."""
    known = {c.id for c in _report.catalogue(kind, is_zip=is_zip)}
    out = {}
    for k, text in (notes or {}).items():
        pid, sep, cid = k.rpartition(":")
        if sep and pid != pack_id:
            continue
        if cid not in known:
            raise GateError(f"note {k!r}: {cid} is not a check in the {kind} catalogue")
        if not str(text).strip():
            raise GateError(f"note {k!r} is empty")
        out[cid] = str(text).strip()
    return out


def read_notes_file(path: str | Path | None) -> dict:
    """Build notes from a file a person wrote for this edition: a JSON
    object, or ``KEY=TEXT`` lines (``#`` comments and blank lines skipped)."""
    if not path:
        return {}
    text = Path(path).read_text(encoding="utf-8")
    if text.lstrip().startswith("{"):
        return {str(k): str(v) for k, v in json.loads(text).items()}
    out = {}
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        k, sep, v = ln.partition("=")
        if not sep or not k.strip():
            raise GateError(f"{path}: not KEY=TEXT: {ln!r}")
        out[k.strip()] = v.strip()
    return out


# --- the gate --------------------------------------------------------------------

@dataclass
class Options:
    """What a publish path hands the gate. ``notes`` come only from the
    person who started the run; an unattended path passes none."""
    notes: dict = field(default_factory=dict)
    origin: str | None = None
    sources: list = field(default_factory=list)
    inputs: Path | None = None
    pyefis_src: Path | None = None
    #: Where local copies of the records go, for the job summary.
    record_dir: Path | None = None
    #: Builds the record. Tests inject a stand-in; the binding and
    #: publish_blockers checks below are not injectable.
    reporter: Callable = _report.build_report


@dataclass
class Gated:
    entry: PackEntry
    path: Path
    record: dict
    raw: bytes
    blockers: list[str]
    previous_how: str
    key: str = ""

    @property
    def accepted(self) -> bool:
        return not self.blockers


def _binding_problems(rec: dict, entry: PackEntry, sha_now: str, size_now: int) -> list[str]:
    p = rec.get("pack") or {}
    want = {"id": entry.id, "kind": entry.kind, "cycle": entry.cycle,
            "sha256": sha_now, "bytes": size_now}
    return [f"record does not describe the bytes being signed: pack.{k} is {p.get(k)!r}, "
            f"the file being signed has {v!r}" for k, v in want.items() if p.get(k) != v]


def gate_one(store, live: Manifest | None, entry: PackEntry, path: str | Path, *,
             opts: Options | None = None, local: dict | None = None, log=print) -> Gated:
    """Report on one pack and judge it. A refused pack's ``rejected`` record
    is uploaded here; an accepted one's record is left for :func:`put_record`,
    so a multi-pack publish uploads nothing unless every pack is accepted."""
    opts = opts or Options()
    path = Path(path)
    if live is not None and any(p.id == entry.id and p.cycle == entry.cycle for p in live.packs):
        raise GateError(f"{entry.id} {entry.cycle} is already in the signed manifest; its "
                        "record is immutable, so publish the rebuild under a new cycle tag")
    sha = signing.sha256_file(path)
    if (sha, path.stat().st_size) != (entry.sha256, entry.bytes):
        raise GateError(f"{entry.id} {entry.cycle}: the manifest entry does not match "
                        f"{path.name} (sha256 {sha[:12]}, {path.stat().st_size:,} B)")
    notes = notes_for(entry.id, entry.kind, opts.notes, is_zip=zipfile.is_zipfile(path))
    previous, first, how = previous_record(store, live, entry, local=local, log=log)
    log(f"  gate {entry.id} {entry.cycle}: {how}")
    rec = opts.reporter(path, record="build", previous=previous, first_edition=first,
                        origin=opts.origin, sources=list(opts.sources), notes=notes,
                        inputs=opts.inputs, pyefis_src=opts.pyefis_src, probe=True)
    # The file being signed, hashed again after the checks ran on it.
    blockers = _binding_problems(rec, entry, signing.sha256_file(path), path.stat().st_size)
    blockers += [f"malformed record: {e}" for e in _report.validate_report(rec)]
    blockers += _report.publish_blockers(rec)
    if blockers:
        rec = dict(rec, record="rejected")
    raw = _report.to_bytes(rec)
    g = Gated(entry=entry, path=path, record=rec, raw=raw, blockers=blockers, previous_how=how)
    if opts.record_dir is not None:
        d = Path(opts.record_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{entry.id}-{entry.cycle}.{'report' if g.accepted else 'rejected'}.json"
         ).write_bytes(raw)
        (d / f"{entry.id}-{entry.cycle}.gate.json").write_text(json.dumps(
            {"accepted": g.accepted, "blockers": blockers, "previous": how}, indent=2) + "\n")
    if g.accepted:
        s = rec["summary"]
        log(f"  gate {entry.id} {entry.cycle}: accepted ({s['pass']} pass, {s['warn']} warn "
            f"with a note, {s['not_run']} not run, {s['fail']} non-blocking fail)")
    else:
        g.key = rejected_key(rec)
        store.put_bytes(g.key, raw, content_type="application/json")
        log(f"  gate {entry.id} {entry.cycle}: REFUSED; record at {g.key}")
        for b in blockers:
            log(f"    BLOCKS PUBLISH  {b}")
    return g


def put_record(store, g: Gated) -> PackEntry:
    """Upload an accepted record and bind it to its entry (``report_url``,
    ``report_sha256``, ``origin``, ``build_commit``). Call only after every
    pack in the publish was accepted, and before the manifest is signed."""
    if not g.accepted:
        raise GateError(f"{g.entry.id} {g.entry.cycle} was refused; it has no record to bind")
    g.key = report_key(g.entry.id, g.entry.cycle)
    store.put_bytes(g.key, g.raw, content_type="application/json")
    e = g.entry
    e.report_url = report_url_for(e)
    e.report_sha256 = _sha256_bytes(g.raw)
    e.origin = g.record.get("origin") or ""
    e.build_commit = (g.record.get("built") or {}).get("commit") or ""
    return e
