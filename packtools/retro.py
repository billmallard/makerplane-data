# SPDX-License-Identifier: Apache-2.0
"""Retro records (TV4): a ``pack-report/1`` record for every edition the
signed live manifest lists without one.

The publish gate (``packtools.gate``, TV3) reports on new builds only, so the
editions published before it went live have no record. This module writes
theirs, one pack at a time, from the published bytes:

1. download the pack from the store into ``scratch`` and check its sha256 and
   size against the **signed** live manifest; a mismatch is an error and
   nothing is written for that pack;
2. run the kind's catalogue on it as a ``retro`` record. ``built`` fields that
   cannot be recovered are ``null``; ``sources`` is empty and PACK-004 says
   why; a delta check compares against the edition ranked just below, if the
   manifest lists one and it has a record, and is otherwise ``not-run``;
3. upload the record to ``reports/<id>/<cycle>/report.json`` and bind it:
   reload the signed manifest, fill the entry's ``report_url`` and
   ``report_sha256`` (and a blank ``origin``/``notice``), and re-sign through
   :func:`packtools.publish.sign_and_put` with the record passed as
   ``retro``, so ``manifest.validate`` checks the binding narrowly (a blank
   entry, a ``retro`` record, the same bytes; never replacing a binding);
4. optionally write ``schema/<kind>.json`` from the open pack (SCH1);
5. delete the pack before the next one.

What a retro record finds is published as found, failures included. Nothing
here retracts, re-publishes or edits a pack: a failing check is listed in the
run's summary for a person to decide on (Bill's TV4 condition 1).
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import gate as _gate
from . import packschema
from . import report as _report
from . import signing
from .build.errors import BuildError
from .manifest import NOTICE, Manifest, PackEntry, _rank_key, validate

#: PACK-004's note in a retro record. The editions TV4 covers were built
#: before records existed, and their upstream inputs were not kept.
SOURCES_NOTE = ("Retro record: this edition was published before pack-report/1 "
                "existed, and the upstream inputs it was built from were not recorded "
                "then and cannot be recovered from the pack.")


class RetroError(BuildError):
    """One pack could not be given a retro record (it is missing from the
    store, or its bytes do not match the signed manifest). Nothing was
    written for it."""


def pending(live: Manifest | None, only: list[str] | None = None) -> list[PackEntry]:
    """Entries with no record binding, in the order to process them: by
    kind, then id, then oldest edition first, so an edition's delta checks
    can compare against the retro record just written for the one below."""
    out = [p for p in (live.packs if live else [])
           if not (p.report_url or p.report_sha256) and (not only or p.id in only)]
    return sorted(out, key=lambda p: (p.kind, p.id, _rank_key(p)))


def previous_for(store, live: Manifest, entry: PackEntry,
                 local: dict | None = None) -> tuple[dict | None, str]:
    """The record of the edition of ``entry.id`` ranked just below it in the
    signed manifest, and how it was found. ``None`` if the manifest lists no
    edition below, or that edition has no readable record: unlike the gate,
    a retro record does not report on an older pack to get one, and never
    claims a first edition, since editions before the listed ones were
    pruned and are not known here."""
    below = [p for p in live.packs
             if p.id == entry.id and p.cycle != entry.cycle and _rank_key(p) < _rank_key(entry)]
    if not below:
        return None, "no earlier edition is listed"
    prev = max(below, key=_rank_key)
    rec = (local or {}).get((prev.id, prev.cycle))
    if rec is not None:
        return rec, f"previous edition {prev.cycle}: its retro record from this run"
    if prev.report_sha256:
        raw = store.get_bytes(_gate.report_key(prev.id, prev.cycle))
        if raw is not None and _gate._sha256_bytes(raw) == prev.report_sha256:
            return json.loads(raw), f"previous edition {prev.cycle}: its bound record"
    return None, f"previous edition {prev.cycle} has no readable record"


def retro_built(rec: dict, entry: PackEntry) -> dict:
    """``built`` for a retro record: only what the manifest still says. The
    commit is the entry's ``build_commit`` if it has one; everything else
    about the original build is ``null`` (docs/pack_report.md)."""
    return {"at": None, "repo": _report.REPO, "commit": entry.build_commit or None,
            "builder_refs": {}, "run_url": None, "runner": None}


_NO_PREV = ("No previous edition was supplied to compare against (--previous), and "
            "--first-edition was not declared.")


def report_one(path: Path, entry: PackEntry, *, previous: dict | None,
               previous_how: str = "", pyefis_src=None, probe: bool = True,
               reporter=None) -> dict:
    """The retro record for the pack at ``path`` (already checked against
    ``entry``). ``reporter`` defaults to :func:`report.build_report`.
    ``previous_how`` (from :func:`previous_for`) replaces the build-side
    wording of a delta check that had nothing to compare against."""
    reporter = reporter or _report.build_report
    origin = entry.origin or None
    if origin is None and entry.kind == "terrain":
        from .make_terrain import TERRAIN_ORIGIN
        origin = TERRAIN_ORIGIN
    rec = reporter(path, record="retro", previous=previous, first_edition=False,
                   origin=origin, sources=[], notes={"PACK-004": SOURCES_NOTE},
                   pyefis_src=pyefis_src, probe=probe)
    # Who wrote this record, and with what code, as distinct from who built
    # the pack (unknown): an additive top-level field (pack-report/1 readers
    # ignore unknown fields).
    run_url = (rec.get("built") or {}).get("run_url")
    rec["retro"] = {
        "written_at": (rec.get("built") or {}).get("at")
        or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": (rec.get("built") or {}).get("commit"),
        "probe_refs": dict((rec.get("built") or {}).get("builder_refs") or {}),
        "run_url": run_url,
        "runner": (rec.get("built") or {}).get("runner"),
    }
    rec["built"] = retro_built(rec, entry)
    if previous is None:
        why = (f"Retro record: {previous_how or 'no earlier edition with a record'}, so there "
               "is nothing to compare against. Editions published before the ones the "
               "manifest lists were pruned and are not known here.")
        for c in rec["checks"]:
            if c.get("not_run_reason") == "no-previous" and c.get("note", "").startswith(_NO_PREV):
                c["note"] = why + c["note"][len(_NO_PREV):]
    if rec.get("record") != "retro":
        raise RetroError(f"{entry.id} {entry.cycle}: reporter returned a "
                         f"{rec.get('record')!r} record")
    errs = _report.validate_report(rec)
    if errs:
        raise RetroError(f"{entry.id} {entry.cycle}: malformed record: " + "; ".join(errs))
    p = rec["pack"]
    if (p["id"], p["cycle"], p["sha256"], p["bytes"]) != (entry.id, entry.cycle,
                                                          entry.sha256, entry.bytes):
        raise RetroError(f"{entry.id} {entry.cycle}: the record describes other bytes")
    return rec


def failing(rec: dict) -> list[dict]:
    """The checks a person must hear about: every ``fail``, blocking or not
    (a check that raised is recorded as ``fail``)."""
    return [c for c in rec["checks"] if c["status"] == "fail"]


def bind(store, secret, entry: PackEntry, raw: bytes, *, generated: str, sign,
         log=print) -> str:
    """Upload ``raw`` (a retro record for ``entry``) and bind it in the signed
    live manifest. Returns ``"bound"``, or ``"already bound"`` when a
    re-run finds the same record already bound. Any other existing binding
    raises: a retro record never replaces one."""
    from .publish import sign_and_put
    rec = json.loads(raw)
    live, previous = _gate.load_signed(store, secret)
    if live is None:
        raise RetroError("no live manifest to bind to")
    cur = next((p for p in live.packs if (p.id, p.cycle) == (entry.id, entry.cycle)), None)
    if cur is None:
        raise RetroError(f"{entry.id} {entry.cycle} is no longer in the live manifest; "
                         "not binding (its record is not uploaded)")
    sha = _gate._sha256_bytes(raw)
    if cur.report_url or cur.report_sha256:
        if cur.report_sha256 == sha:
            return "already bound"
        raise RetroError(f"{entry.id} {entry.cycle} already has a record "
                         f"({cur.report_url}); a retro record never replaces it")
    if (cur.sha256, cur.bytes) != (rec["pack"]["sha256"], rec["pack"]["bytes"]):
        raise RetroError(f"{entry.id} {entry.cycle}: the live entry's bytes changed "
                         "since the record was written")
    key = _gate.report_key(entry.id, entry.cycle)
    cur.report_url = _gate.report_url_for(cur)
    cur.report_sha256 = sha
    cur.origin = cur.origin or rec.get("origin") or ""
    cur.notice = cur.notice or NOTICE
    live.generated = generated
    retro = {(entry.id, entry.cycle): raw}
    # Judge the binding before the record is uploaded, so a refused one
    # leaves no unbound record under reports/ for the site to list.
    validate(live.to_obj(), previous=previous, retro=retro)
    store.put_bytes(key, raw, content_type="application/json")
    sign_and_put(store, secret, live, previous=previous, sign=sign,
                 comment=f"retro record {entry.id}/{entry.cycle} at {generated}",
                 retro=retro)
    log(f"  bound {key} (sha256 {sha[:12]}) to {entry.id} {entry.cycle}; manifest re-signed")
    return "bound"


@dataclass
class Result:
    id: str
    cycle: str
    kind: str
    outcome: str                       # bound | already bound | written (dry run) | error
    summary: dict = field(default_factory=dict)
    failing: list = field(default_factory=list)
    previous: str = ""
    error: str = ""
    schema_key: str = ""


def _utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(store, secret, *, scratch: Path, sign=signing.sign, only: list[str] | None = None,
        out_dir: Path | None = None, pyefis_src=None, probe: bool = True,
        schema: bool = True, dry_run: bool = False, reporter=None,
        log=print) -> list[Result]:
    """Write and bind a retro record for every unbound live entry, one pack
    at a time. ``dry_run`` writes the records (and schemas) to ``out_dir``
    only: nothing is uploaded and nothing is signed, and ``secret`` may be
    ``None``, in which case the live manifest is read unverified. A failure
    on one pack is recorded and the run moves on to the next."""
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    if out_dir is not None:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
    if secret is not None:
        live, _ = _gate.load_signed(store, secret)
    else:
        if not dry_run:
            raise RetroError("binding needs the signing key; pass dry_run for a preview")
        raw = store.get_bytes(_gate.MANIFEST_KEY)
        live = Manifest.from_bytes(raw) if raw else None
    todo = pending(live, only)
    log(f"retro: {len(todo)} unbound edition(s){' (dry run)' if dry_run else ''}")
    local: dict = {}
    results = []
    schemas: dict[str, tuple[str, bytes]] = {}
    for i, entry in enumerate(todo, 1):
        log(f">> [{i}/{len(todo)}] {entry.id} {entry.cycle} ({entry.bytes:,} B)")
        res = Result(entry.id, entry.cycle, entry.kind, "error")
        dest = scratch / entry.url.rsplit("/", 1)[1]
        try:
            if not store.get_file(_gate._pack_key(entry), dest):
                raise RetroError(f"{_gate._pack_key(entry)} is not in the store")
            if (signing.sha256_file(dest), dest.stat().st_size) != (entry.sha256, entry.bytes):
                raise RetroError(f"{entry.id} {entry.cycle}: the stored pack does not match "
                                 "the sha256 and size in the signed manifest")
            prev, res.previous = previous_for(store, live, entry, local)
            log(f"   {res.previous}")
            rec = report_one(dest, entry, previous=prev, previous_how=res.previous,
                             pyefis_src=pyefis_src,
                             probe=probe, reporter=reporter)
            raw = _report.to_bytes(rec)
            res.summary, res.failing = rec["summary"], [
                {"id": c["id"], "blocking": c["blocking"], "measured": c["measured"]}
                for c in failing(rec)]
            if out_dir is not None:
                (Path(out_dir) / f"{entry.id}-{entry.cycle}.retro.json").write_bytes(raw)
            if schema:
                sch = packschema.to_bytes(packschema.capture(dest))
                schemas[entry.kind] = (packschema.key_for(entry.kind), sch)
                res.schema_key = packschema.key_for(entry.kind)
                if out_dir is not None:
                    (Path(out_dir) / f"schema-{entry.kind}.json").write_bytes(sch)
            if dry_run:
                res.outcome = "written (dry run)"
            else:
                res.outcome = bind(store, secret, entry, raw, generated=_utc(), sign=sign,
                                   log=log)
                if schema:
                    k, sch = schemas[entry.kind]
                    if store.get_bytes(k) != sch:
                        store.put_bytes(k, sch, content_type="application/json")
            local[(entry.id, entry.cycle)] = rec
            s = rec["summary"]
            log(f"   {res.outcome}: {s['pass']} pass, {s['warn']} warn, {s['fail']} fail, "
                f"{s['not_run']} not run")
            for c in res.failing:
                log(f"   FAIL {c['id']}{' (blocking)' if c['blocking'] else ''}: {c['measured']}")
        except Exception as e:
            res.error = f"{type(e).__name__}: {e}"
            log(f"!! {entry.id} {entry.cycle}: {res.error}")
        finally:
            dest.unlink(missing_ok=True)
        results.append(res)
    if not dry_run and any(r.outcome == "bound" for r in results):
        # Another publish (the nightly cyclical run) that loaded the manifest
        # before a bind and signed after it drops that binding. Say so; a
        # re-run binds it again.
        now, _ = _gate.load_signed(store, secret)
        have = {(p.id, p.cycle): p.report_sha256 for p in (now.packs if now else [])}
        for r in results:
            if r.outcome == "bound" and not have.get((r.id, r.cycle)):
                r.outcome, r.error = "error", "binding lost after it was signed; re-run"
                log(f"!! {r.id} {r.cycle}: binding no longer in the live manifest; re-run")
    if out_dir is not None:
        (Path(out_dir) / "retro-summary.json").write_text(
            json.dumps([r.__dict__ for r in results], indent=2) + "\n", encoding="utf-8")
    return results


def summary_markdown(results: list[Result]) -> str:
    """The run as a table, failures listed below it."""
    from . import summary as _summary
    rows = [[r.id, r.cycle, r.outcome,
             (f"{r.summary['pass']}/{r.summary['warn']}/{r.summary['fail']}/"
              f"{r.summary['not_run']}" if r.summary else ""),
             r.error or r.previous] for r in results]
    md = ("## retro records (TV4)\n\n"
          + _summary.table(["id", "cycle", "outcome", "pass/warn/fail/not run", "note"], rows))
    fails = [(r, c) for r in results for c in r.failing]
    md += "\n### Failing checks\n\n"
    md += ("".join(f"- {r.id} {r.cycle} {c['id']}{' (blocking)' if c['blocking'] else ''}: "
                   f"{c['measured']}\n" for r, c in fails) or "None.\n")
    return md

