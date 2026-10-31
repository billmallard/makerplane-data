# SPDX-License-Identifier: Apache-2.0
"""The daily pipeline orchestrator (Leg 1).

For each cyclical source, for the current cycle (and the next, for AIRAC
products the FAA publishes ahead):

    compute cycle -> is the pack already in the store? -> if yes, skip
    -> else fetch upstream -> build sqlite -> embed pack_meta -> compute
    sha256/size -> publish gate (packtools.gate: build the pack-report/1
    record from these bytes and judge it) -> upload record + pack -> upsert
    into the manifest with the record bound

A pack the gate refuses gets a ``rejected`` record under reports/rejected/
and is not published; the run goes red. Build notes come only from a
person, so the scheduled run passes none: a check that warns refuses the
build rather than being noted (AER-4102 C8). A person who has looked at the
warn re-runs it by hand (``--notes-file``, the workflow's ``notes`` input),
with each note keyed to one edition: ``<pack id>@<cycle>:<CHECK>``.

Finally: prune old cycles, regenerate + sign the manifest, upload it.
Idempotent and safe to run repeatedly (skip-if-present); a fetch failure
for a not-yet-published "next" cycle is logged, not fatal.

The fetcher and builders are injected so the whole orchestration is unit
-testable without network or the pyEfis tools (see tests).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import sys
import tempfile
from pathlib import Path

from . import build as _build
from . import cycles, fetch, gate, report, schema_guard, schemasite, signing, summary
from .manifest import Manifest
from .packmeta import PackMeta, embed_sqlite
from .manifest import PackEntry
from .publish import sign_and_put
from .regions import load_regions, manifest_regions_block
from .schema_guard import SchemaGuardError
from .sources import Source, cyclical_sources
from .upload import LocalStore, ObjectStore, R2Store

MANIFEST_KEY = "manifest.json"
SIG_KEY = "manifest.json.minisig"
_DEFAULT_URL_BASE = "https://navdata.aerocommons.org/packs"


class PipelineFailure(RuntimeError):
    """One or more sources failed a HARD check (schema guard / build error).
    The manifest for the healthy sources is still signed + uploaded -- the
    failed source simply ships nothing and its previously published packs keep
    serving -- but the run exits nonzero so CI goes red and a human looks. A
    not-yet-published next cycle is NOT this: that is a benign fetch WARN."""


class CyclicalRunner:
    def __init__(self, *, store: ObjectStore, secret: signing.SecretKey | None,
                 url_base: str = _DEFAULT_URL_BASE,
                 work_dir: str | Path | None = None,
                 fetcher=fetch.fetch_and_extract,
                 builders: dict | None = None,
                 guard=schema_guard.DEFAULT,
                 reporter=report.build_report,
                 notes: dict | None = None,
                 published_checksums: str | Path | None = None,
                 today: _dt.date | None = None):
        self.store = store
        self.secret = secret
        self.url_base = url_base.rstrip("/")
        self.work_dir = Path(work_dir or tempfile.mkdtemp(prefix="packtools-"))
        self.fetcher = fetcher
        self.builders = builders if builders is not None else _build.BUILDERS
        # The data-contract guard (schema_guard). Injected like fetcher/builders;
        # tests that use fake FAA data pass schema_guard.NULL_GUARD.
        self.guard = guard
        # Builds each pack's record for the publish gate. Injected only so
        # tests with fake packs can stand in for it; the gate's binding and
        # publish_blockers checks are not injectable (packtools.gate).
        self.reporter = reporter
        # Build notes from the person who started this run, each keyed to one
        # edition (``<pack id>@<cycle>:<CHECK>``) so a note cannot carry over
        # to the next cycle. The scheduled run has none.
        self.notes = _edition_notes(notes)
        # Where to list the packs this run put in the signed manifest, as
        # sha256sum lines: the subjects the workflow's build-provenance
        # attestation covers (TV10). None writes nothing.
        self.published_checksums = Path(published_checksums) if published_checksums else None
        self.today = today or _dt.date.today()
        self.log = print
        # One row per (source, cycle) decision, for the job summary.
        self.checks: list[dict] = []
        # The signed live manifest as this run found it (the gate decides
        # first edition and previous edition from it), its raw object (the
        # previous for manifest.validate), and the records this run accepted.
        self.live: Manifest | None = None
        self.previous: dict = {"packs": []}
        self.accepted: dict = {}
        self._cur: dict = {}

    def _cycles_for(self, source: Source) -> list[cycles.Cycle]:
        cur, nxt = cycles.current_and_next(source.cadence, today=self.today)
        # FAA publishes the next AIRAC cycle ahead of time; DOF is a daily
        # product with no meaningful "next".
        return [cur, nxt] if source.cadence == "airac" else [cur]

    def _load_manifest(self, *, dry_run: bool) -> Manifest:
        """The live manifest to build on. A real run verifies its signature
        first (the gate decides from it); a dry run writes nothing and holds
        no key, so it parses it unverified."""
        stamp = f"{self.today.isoformat()}T00:00:00Z"
        if dry_run:
            raw = self.store.get_bytes(MANIFEST_KEY)
            live = Manifest.from_bytes(raw) if raw else None
        else:
            live, self.previous = gate.load_signed(self.store, self.secret)
        self.live = live
        if live is None:
            return Manifest.new(stamp)
        m = Manifest.from_bytes(live.to_bytes())
        m.generated = stamp
        return m

    def _sources(self, urls, cycle_work: Path, c: cycles.Cycle) -> list[dict]:
        """PACK-004's upstream record: each archive this cycle downloaded,
        with its sha256 and when it landed on disk."""
        out = []
        for url in urls:
            archive = cycle_work / Path(url.split("?")[0]).name
            if not archive.is_file():
                continue
            at = _dt.datetime.fromtimestamp(archive.stat().st_mtime, _dt.timezone.utc)
            out.append({"name": archive.name, "edition": c.cycle, "url": url,
                        "retrieved": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "sha256": signing.sha256_file(archive)})
        return out

    def _build_one(self, source: Source, c: cycles.Cycle) -> tuple[PackEntry, Path]:
        builder = self.builders[source.builder]
        cycle_work = self.work_dir / source.pack_id / c.cycle
        cycle_work.mkdir(parents=True, exist_ok=True)
        urls = source.url_for(c)
        if isinstance(urls, str):
            urls = (urls,)
        for url in urls:
            self.log(f"  fetch {url}")
            extracted = self.fetcher(url, cycle_work,
                                     member=(source.archive_member or None))
        # Contract check 1-3, against the live download, before we build.
        self.guard.check_inputs(source, Path(extracted))
        pack_path = cycle_work / f"{source.pack_id}-{c.cycle}.pack"
        self.log(f"  build {source.builder} -> {pack_path.name}")
        builder(Path(extracted), pack_path)
        # Contract check 4, against the built sqlite, before it is embedded /
        # uploaded -- a below-floor or empty pack must not ship.
        self.guard.check_output(source, pack_path)
        meta = PackMeta(id=source.pack_id, kind=source.kind, cycle=c.cycle,
                        effective=c.effective.isoformat(),
                        expires=c.expires.isoformat(),
                        attribution=source.attribution,
                        license=source.license, license_url=source.license_url)
        embed_sqlite(pack_path, meta)
        url = f"{self.url_base}/{pack_path.name}"
        entry = PackEntry.from_pack(pack_path, meta, url=url, regions=list(source.regions))
        # The publish gate, on the exact bytes about to be uploaded, with only
        # the notes a person wrote for this edition (C8).
        mine = {k: v for (pid, cyc, k), v in self.notes.items()
                if (pid, cyc) == (source.pack_id, c.cycle)}
        g = gate.gate_one(self.store, self.live, entry, pack_path, local=self.accepted,
                          opts=gate.Options(notes=mine, inputs=Path(extracted),
                                            sources=self._sources(urls, cycle_work, c),
                                            record_dir=self.work_dir / "reports",
                                            reporter=self.reporter),
                          log=self.log)
        self._cur["gate"] = g
        if not g.accepted:
            raise gate.GateRefused([g])
        return entry, pack_path

    def run(self, sources: list[Source] | None = None, *,
            dry_run: bool = False) -> Manifest:
        sources = sources if sources is not None else cyclical_sources()
        self.checks = []
        self.accepted = {}
        try:
            return self._load_and_run(sources, dry_run=dry_run)
        finally:
            summary.write(self.summary_markdown(dry_run=dry_run))

    def _load_and_run(self, sources: list[Source], *, dry_run: bool) -> Manifest:
        m = self._load_manifest(dry_run=dry_run)
        m.regions = manifest_regions_block(load_regions())
        # url_base is the single source of truth for the serving origin:
        # re-root every existing entry's URL onto it so a base change (e.g.
        # r2.dev -> custom domain) is corrected on the next run, even for
        # packs that are skipped (already present) and never rebuilt.
        for p in m.packs:
            p.url = f"{self.url_base}/{p.id}-{p.cycle}.pack"
        return self._run(sources, m, dry_run=dry_run)

    def _record(self, source: Source, c: cycles.Cycle, decision: str, detail: str = "") -> None:
        self.checks.append({"pack": source.pack_id, "cycle": c.cycle,
                            "decision": decision, "detail": detail,
                            "gate": self._cur.get("gate")})
        self._cur = {}

    def _run(self, sources: list[Source], m: Manifest, *, dry_run: bool) -> Manifest:
        built = 0
        failures: list[str] = []
        published: list[tuple[Path, dict]] = []

        for source in sources:
            for c in self._cycles_for(source):
                key = f"packs/{source.pack_id}-{c.cycle}.pack"
                in_store = self.store.exists(key)
                in_manifest = any(p.id == source.pack_id and p.cycle == c.cycle
                                  for p in m.packs)
                if in_store and in_manifest:
                    self.log(f"skip {source.pack_id} {c.cycle} (present)")
                    self._record(source, c, "skip (present)")
                    continue
                if dry_run:
                    self.log(f"WOULD build+upload {source.pack_id} {c.cycle} "
                             f"({c.effective}..{c.expires})")
                    self._record(source, c, "would build",
                                 f"in store: {in_store}, in manifest: {in_manifest}")
                    continue
                self._cur = {}
                try:
                    entry, pack_path = self._build_one(source, c)
                except (SchemaGuardError, _build.BuildError) as e:
                    # A HARD failure: an upstream contract break, a build
                    # error, or the publish gate refusing the pack (GateRefused
                    # and GateError are BuildErrors). Do not abort the run (one
                    # bad source must not block DOF), but record it so the run
                    # exits nonzero. The failing source ships nothing -- its
                    # pack was never put or upserted, so its previously
                    # published packs keep serving.
                    self.log(f"FAIL {source.pack_id} {c.cycle}: {e}")
                    failures.append(f"{source.pack_id} {c.cycle}: {e}")
                    self._record(source, c, f"FAIL ({type(e).__name__})", str(e))
                    continue
                except Exception as e:
                    # A not-yet-published next cycle, or a transient upstream
                    # error, must not abort the whole run and is NOT a failure.
                    self.log(f"WARN {source.pack_id} {c.cycle}: {e}")
                    self._record(source, c, "WARN (not fatal)", str(e))
                    continue
                gate.put_record(self.store, self._cur["gate"])
                self.store.put_file(key, pack_path, content_type="application/octet-stream")
                m.upsert(entry)
                self.accepted[(entry.id, entry.cycle)] = (entry, self._cur["gate"].record)
                published.append((pack_path, self._cur["gate"].record))
                built += 1
                self.log(f"built+uploaded {source.pack_id} {c.cycle} "
                         f"sha256={entry.sha256[:12]}… {entry.bytes:,} B")
                self._record(source, c, "built + uploaded",
                             f"sha256 {entry.sha256[:12]}, {entry.bytes:,} B")

        m.prune_old_cycles(keep=2)

        if dry_run:
            self.log(f"dry-run: {built} pack(s) would be built; manifest not written")
            return m

        sign_and_put(self.store, self.secret, m, previous=self.previous, sign=signing.sign,
                     comment=f"generated {self.today.isoformat()}")
        self.log(f"manifest: {len(m.packs)} pack(s), {built} new, signed + uploaded")
        self._write_published_checksums()
        schemasite.put_captures(self.store, published, log=self.log)

        # Healthy sources have published; now surface any hard failures as a
        # nonzero exit (a red run is the whole point of the guard).
        if failures:
            raise PipelineFailure(
                f"{len(failures)} source(s) failed a hard check; "
                f"healthy sources were still published:\n  - "
                + "\n  - ".join(failures))
        return m

    def _write_published_checksums(self) -> None:
        """One ``<sha256>  <pack file>`` line per pack this run accepted, written
        only once the manifest listing them is signed and uploaded. A pack the
        gate refused, or one that failed to build, is not listed, so it gets no
        attestation. The file is written even when empty, so the workflow can
        tell "nothing new" from "never ran"."""
        if self.published_checksums is None:
            return
        lines = [f"{e.sha256}  {e.id}-{e.cycle}.pack\n"
                 for e, _record in self.accepted.values()]
        self.published_checksums.parent.mkdir(parents=True, exist_ok=True)
        self.published_checksums.write_text("".join(lines))

    def summary_markdown(self, *, dry_run: bool = False) -> str:
        """``$GITHUB_STEP_SUMMARY``: one row per (pack, cycle) decision, then
        each built pack's test record, rendered from the record JSON the gate
        judged (accepted or rejected). A pack that never reached the gate
        (a fetch, guard or build failure) says it has no record."""
        rows = []
        for r in self.checks:
            g = r["gate"]
            rec = ("-" if r["decision"] in ("skip (present)", "would build")
                   else "none: no pack reached the gate" if g is None
                   else "accepted" if g.accepted else "REFUSED (record: rejected)")
            rows.append([r["pack"], r["cycle"], r["decision"], rec, r["detail"]])
        title = f"cyclical packs {self.today.isoformat()}" + (" (dry run)" if dry_run else "")
        n_fail = sum(r["decision"].startswith("FAIL") for r in self.checks)
        verdict = (f"**{n_fail} hard failure(s): this run is red.**" if n_fail
                   else "No hard failures.")
        md = (f"## {title}\n\n{verdict}\n\n"
              + summary.table(["pack", "cycle", "decision", "test record", "detail"], rows))
        for r in self.checks:
            if r["gate"] is not None:
                g = r["gate"]
                md += "\n" + summary.record_markdown(g.record, blockers=g.blockers,
                                                     key=g.key, previous=g.previous_how)
        return md


# --------------------------------------------------------------------------

_NOTE_KEY = re.compile(r"^(?P<id>[^@:\s]+)@(?P<cycle>[^@:\s]+):(?P<check>[A-Z0-9-]+)$")


def _edition_notes(notes: dict | None) -> dict:
    """``{"<pack id>@<cycle>:<CHECK>": text}`` -> ``{(id, cycle, CHECK): text}``.
    Any other key raises: a note not tied to one edition could explain a
    warn it was never written for."""
    out = {}
    for k, v in (notes or {}).items():
        m = _NOTE_KEY.match(k)
        if not m:
            raise ValueError(f"build note key {k!r}: the cyclical run needs "
                             "<pack id>@<cycle>:<CHECK>, e.g. procedures-conus@2612:ROW-002")
        out[(m["id"], m["cycle"], m["check"])] = v
    return out


def _secret(args) -> signing.SecretKey:
    if os.environ.get("MINISIGN_SECRET_KEY"):
        return signing.SecretKey.from_b64(os.environ["MINISIGN_SECRET_KEY"])
    if args.sec:
        return signing.SecretKey.from_b64(Path(args.sec).read_text().strip())
    raise SystemExit("no secret key: set MINISIGN_SECRET_KEY or pass --sec PATH")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="packtool-run-cyclical",
                                 description="daily NASR/DOF pack pipeline")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be built; touch nothing")
    ap.add_argument("--no-upload", action="store_true",
                    help="build locally into --work instead of uploading to R2")
    ap.add_argument("--date", help="treat this ISO date as today (reproducible)")
    ap.add_argument("--work", default="work", help="working dir for fetch/build")
    ap.add_argument("--url-base", default=_DEFAULT_URL_BASE)
    ap.add_argument("--sec", help="secret key file (or MINISIGN_SECRET_KEY)")
    ap.add_argument("--bucket", default=os.environ.get("R2_BUCKET", "makerplane-data"))
    ap.add_argument("--only", nargs="*", help="restrict to these pack ids")
    ap.add_argument("--notes-file", dest="notes_file",
                    help="build notes a person wrote for a check that warned: a JSON object "
                         "or KEY=TEXT lines, KEY = <pack id>@<cycle>:<CHECK>. Never set this "
                         "on the scheduled run")
    ap.add_argument("--published-checksums", dest="published_checksums",
                    help="write a sha256sum-format list of the packs this run published "
                         "(the build-provenance attestation's subjects)")
    args = ap.parse_args(argv)

    today = _dt.date.fromisoformat(args.date) if args.date else _dt.date.today()
    work = Path(args.work)

    if args.dry_run or args.no_upload:
        store: ObjectStore = LocalStore(work / "r2")
    else:
        store = R2Store.from_env(args.bucket)

    secret = None if args.dry_run else _secret(args)
    runner = CyclicalRunner(store=store, secret=secret,
                            url_base=args.url_base, work_dir=work, today=today,
                            notes=gate.read_notes_file(args.notes_file),
                            published_checksums=args.published_checksums)
    srcs = cyclical_sources()
    if args.only:
        srcs = [s for s in srcs if s.pack_id in set(args.only)]
    try:
        runner.run(srcs, dry_run=args.dry_run)
    except PipelineFailure as e:
        print(f"PIPELINE FAILED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
