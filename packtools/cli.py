# SPDX-License-Identifier: Apache-2.0
"""packtool — Phase A command line.

Three subcommands prove the contract end to end:

    packtool genkey --out keys/                 # one-time, throwaway for now
    packtool build-pack SOURCE --id ... --kind ...   # source -> signed catalog
    packtool verify manifest.json --pub keys/minisign.pub

The full daily pipeline (fetch upstream -> build with pyEfis tools ->
upload to R2) is Phase B; this CLI takes an already-built sqlite/zip and
turns it into a signed, manifest-registered pack so the format and the
trust chain can be exercised today.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import sys
from pathlib import Path

from . import cycles, signing
from .manifest import Manifest, PackEntry
from .packmeta import PackMeta, KINDS, read as read_packmeta
from .regions import load_regions, manifest_regions_block

# Which cycle cadence each AIRAC-or-DOF pack kind follows.
_KIND_CADENCE = {"navdata": "airac", "cifp": "airac", "obstacles": "dof"}

_DEFAULT_URL_BASE = "https://navdata.aerocommons.org/packs"


def _utc_stamp(date: _dt.date) -> str:
    # 'generated' is injected from --date so builds are reproducible.
    return f"{date.isoformat()}T00:00:00Z"


def cmd_genkey(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sk, pub = signing.generate_keypair(args.comment)
    (out / "minisign.pub").write_text(pub, encoding="ascii")
    sec_path = out / "minisign.sec"
    sec_path.write_text(sk.to_b64() + "\n", encoding="ascii")
    print(f"public key  -> {out / 'minisign.pub'}  (commit this)")
    print(f"secret key  -> {sec_path}  (gitignored; for CI use the base64 as MINISIGN_SECRET_KEY)")
    print(f"key id      -> {sk.key_id.hex()}")
    return 0


def _load_secret(args) -> signing.SecretKey:
    if os.environ.get("MINISIGN_SECRET_KEY"):
        return signing.SecretKey.from_b64(os.environ["MINISIGN_SECRET_KEY"])
    if args.sec:
        return signing.SecretKey.from_b64(Path(args.sec).read_text().strip())
    raise SystemExit("no secret key: set MINISIGN_SECRET_KEY or pass --sec PATH")


def _gate_options(args, record_dir: Path):
    """The publish gate's inputs from the command line. Build notes come
    only from ``--note`` / ``--notes-file``, never from a default."""
    import json
    from . import gate
    notes = gate.read_notes_file(getattr(args, "notes_file", None))
    notes.update(_kv_list(getattr(args, "note", None), "--note"))
    sources = []
    if getattr(args, "sources", None):
        sources = json.loads(Path(args.sources).read_text(encoding="utf-8"))
    return gate.Options(notes=notes, origin=getattr(args, "origin", None), sources=sources,
                        pyefis_src=getattr(args, "pyefis_src", None), record_dir=record_dir)


def _require_device_reader(args) -> None:
    """Fail before a long build rather than after it: ACC-001 is blocking,
    so a publish with no pyEfis checkout to probe with is refused anyway."""
    from .report import resolve_pyefis_src
    if resolve_pyefis_src(getattr(args, "pyefis_src", None)) is None:
        raise SystemExit("publish gate: no pyEfis checkout for device acceptance (ACC-001, "
                         "blocking). Pass --pyefis-src or set PYEFIS_SRC_DIR / PYEFIS_TOOLS_DIR.")


def _publish_refused(e) -> int:
    from .gate import GateRefused
    print(f"PUBLISH REFUSED: {e}", file=sys.stderr)
    if isinstance(e, GateRefused):
        for g in e.refused:
            print(f"  rejected record: {g.key}", file=sys.stderr)
    return 1


def cmd_build_pack(args) -> int:
    date = _dt.date.fromisoformat(args.date) if args.date else _dt.date.today()
    src = Path(args.source)
    if not src.exists():
        raise SystemExit(f"source not found: {src}")

    # Resolve the cycle + currency window. An explicit --cycle is enough
    # (non-cyclical kinds like water/terrain/highways have no effective/expires);
    # otherwise auto-compute from the kind's cadence.
    if args.cycle:
        cycle = args.cycle
        effective = args.effective
        expires = args.expires
    else:
        cadence = _KIND_CADENCE.get(args.kind)
        if cadence is None:
            raise SystemExit(f"kind {args.kind!r} is non-cyclical; pass --cycle explicitly")
        cur, _nxt = cycles.current_and_next(cadence, today=date)
        cycle = cur.cycle
        effective = cur.effective.isoformat()
        expires = cur.expires.isoformat()

    meta = PackMeta(id=args.id, kind=args.kind, cycle=cycle,
                    effective=effective, expires=expires,
                    attribution=args.attribution,
                    license=args.license, license_url=args.license_url)

    out_dir = Path(args.out)
    (out_dir / "packs").mkdir(parents=True, exist_ok=True)
    pack_path = out_dir / "packs" / f"{args.id}-{cycle}.pack"
    print(f"copying {src} -> {pack_path} ...")
    shutil.copy2(src, pack_path)

    # Embed pack_meta (sqlite table or zip member).
    import zipfile
    from . import packmeta
    if zipfile.is_zipfile(pack_path):
        packmeta.embed_zip(pack_path, meta)
    else:
        # highways, procedures: schema_version has to speak for the
        # on-disk tables, not just PackMeta's own fields -- detect it from
        # the built pack rather than trusting the package-wide default
        # (AER-1715, AER-4446).
        detected = packmeta.detect_content_schema_version(args.kind, pack_path)
        if detected is not None:
            meta.schema_version = detected
        packmeta.embed_sqlite(pack_path, meta)
    print(f"embedded pack_meta: {read_packmeta(pack_path).as_dict()}")

    url = f"{args.url_base.rstrip('/')}/{pack_path.name}"
    entry = PackEntry.from_pack(pack_path, meta, url=url,
                                regions=args.regions or [],
                                min_pyefis=args.min_pyefis)
    print(f"sha256 {entry.sha256}  bytes {entry.bytes:,}")
    sk = _load_secret(args)

    # --upload: push to R2 and re-sign the live manifest (adds this pack
    # alongside whatever is already published). Used for water and one-offs.
    if args.upload:
        from .gate import GateError, GateRefused
        from .upload import R2Store
        from .publish import publish
        _require_device_reader(args)
        opts = _gate_options(args, out_dir / "reports")
        store = R2Store.from_env(args.bucket)
        try:
            publish(store, sk, [(entry, pack_path)], generated=_utc_stamp(date),
                    sign=signing.sign, comment=f"{args.id} {cycle}", gate=opts)
        except (GateError, GateRefused) as e:
            return _publish_refused(e)
        print(f"uploaded {pack_path.name} + its test record -> R2, manifest re-signed")
        return 0

    # Otherwise write a local signed manifest (dev / staging).
    manifest_path = out_dir / "manifest.json"
    if manifest_path.exists():
        m = Manifest.read(manifest_path)
        m.generated = _utc_stamp(date)
    else:
        m = Manifest.new(_utc_stamp(date))
    m.regions = manifest_regions_block(load_regions())
    m.upsert(entry)
    m.prune_old_cycles(keep=args.keep)
    m.write(manifest_path)
    print(f"wrote {manifest_path} ({len(m.packs)} pack(s))")
    sig = signing.sign_file(manifest_path, sk,
                            trusted_comment=f"generated {date.isoformat()}")
    print(f"signed -> {sig}")
    return 0


def cmd_remove_pack(args) -> int:
    from .upload import R2Store
    from .publish import retract
    date = _dt.date.fromisoformat(args.date) if args.date else _dt.date.today()
    store = R2Store.from_env(args.bucket)
    sk = _load_secret(args)
    retract(store, sk, args.id, args.cycle, generated=_utc_stamp(date), sign=signing.sign)
    return 0


def cmd_backfill_licenses(args) -> int:
    from .publish import backfill_licenses
    date = _dt.date.fromisoformat(args.date) if args.date else _dt.date.today()
    if args.manifest_url:
        # Read-only preview against the public manifest: no credentials needed.
        import requests

        class _ReadOnly:
            def get_bytes(self, key):
                r = requests.get(args.manifest_url, timeout=60)
                r.raise_for_status()
                return r.content
        if not args.dry_run:
            raise SystemExit("--manifest-url is read-only; pass --dry-run")
        store, sk = _ReadOnly(), None
    else:
        from .upload import R2Store
        store = R2Store.from_env(args.bucket)
        sk = None if args.dry_run else _load_secret(args)
    backfill_licenses(store, sk, generated=_utc_stamp(date), sign=signing.sign,
                      dry_run=args.dry_run)
    return 0


def cmd_retro(args) -> int:
    """Write and bind a retro record for every live edition that has none
    (TV4), one pack at a time; --dry-run writes the records locally only."""
    from . import retro, summary
    if args.from_url:
        if not args.dry_run:
            raise SystemExit("--from-url is read-only; pass --dry-run")
        store = _PublicStore(args.from_url)
    elif args.source:
        from .upload import LocalStore
        store = LocalStore(args.source)
    else:
        from .upload import R2Store
        store = R2Store.from_env(args.bucket)
    sk = None if args.dry_run and not os.environ.get("MINISIGN_SECRET_KEY") and not args.sec \
        else _load_secret(args)
    if not args.dry_run and not args.no_device:
        _require_device_reader(args)
    only = [x for x in (args.only or "").split(",") if x] or None
    results = retro.run(store, sk, scratch=Path(args.scratch), only=only,
                        out_dir=Path(args.out) if args.out else None,
                        pyefis_src=args.pyefis_src, probe=not args.no_device,
                        schema=not args.no_schema, dry_run=args.dry_run)
    md = retro.summary_markdown(results)
    print(md)
    summary.write(md)
    return 1 if any(r.outcome == "error" for r in results) else 0


class _PublicStore:
    """Read-only store over the public origin (navdata.aerocommons.org), for
    a dry run with no credentials."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def get_bytes(self, key):
        import requests
        r = requests.get(f"{self.base}/{key}", timeout=120)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content

    def get_file(self, key, dest) -> bool:
        import requests
        with requests.get(f"{self.base}/{key}", stream=True, timeout=120) as r:
            if r.status_code == 404:
                return False
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        return True


def cmd_deploy_site(args) -> int:
    """Upload changed files under site/ to the bucket root (AER-4098), and
    write what changed to the job summary."""
    from . import sitedeploy, summary
    from .upload import R2Store
    store = R2Store.from_env(args.bucket)
    rows = sitedeploy.deploy(store, args.root, dry_run=args.dry_run)
    md = (f"## deploy-site (dry_run={args.dry_run})\n\n"
          + summary.table(["key", "bytes", "status"], rows))
    summary.write(md)
    return 0


def cmd_report_site(args) -> int:
    """Render the report pages from the published records and upload the
    changed ones (TV5), or render a local preview with --from/--out."""
    from . import reportsite, sitedeploy, summary
    from .upload import LocalStore
    if args.source:
        src = LocalStore(args.source)
    else:
        from .upload import R2Store
        src = R2Store.from_env(args.bucket)
    raw = src.get_bytes("manifest.json")
    manifest = json.loads(raw) if raw else None
    try:
        objects = reportsite.build(reportsite.load_records(src), manifest,
                                   commit=reportsite.generator_commit())
    except reportsite.SiteError as e:
        print(f"report-site: {e}", file=sys.stderr)
        return 1
    if args.out:
        # A browsable preview: the site itself, the catalog it reads, and the pages.
        dst = LocalStore(args.out)
        for key, path, _ in sitedeploy.site_files(args.root):
            dst.put_file(key, path)
        if raw:
            dst.put_bytes("manifest.json", raw)
        for key in src.list_keys(reportsite.PREFIX):
            dst.put_bytes(key, src.get_bytes(key))
    else:
        dst = src
    rows = reportsite.publish(dst, objects, dry_run=args.dry_run)
    md = (f"## report-site (dry_run={args.dry_run})\n\n"
          + summary.table(["key", "bytes", "status"], rows))
    summary.write(md)
    return 0


def cmd_schema_site(args) -> int:
    """Render the pack format pages from the schema captures and upload the
    changed ones (SCH1), or render a local preview with --from/--out."""
    from . import community, reportsite, schemasite, sitedeploy, summary
    from .upload import LocalStore
    if args.source:
        src = LocalStore(args.source)
    else:
        from .upload import R2Store
        src = R2Store.from_env(args.bucket)
    pub = Path(args.pub).read_text(encoding="utf-8") if args.pub else None
    try:
        manifest = community.load_live_manifest(src, pub)
        if args.bootstrap and not args.dry_run:
            schemasite.bootstrap(src, manifest)
        objects = schemasite.build(schemasite.load_kinds(src), manifest,
                                   commit=reportsite.generator_commit())
    except (community.CommunityError, reportsite.SiteError) as e:
        print(f"schema-site: {e}", file=sys.stderr)
        return 1
    if args.out:
        # A browsable preview: the site itself, the catalog and the captures.
        dst = LocalStore(args.out)
        for key, path, _ in sitedeploy.site_files(args.root):
            dst.put_file(key, path)
        dst.put_bytes("manifest.json", src.get_bytes("manifest.json"))
        for key in src.list_keys(schemasite.PREFIX):
            dst.put_bytes(key, src.get_bytes(key))
    else:
        dst = src
    rows = schemasite.publish(dst, objects, dry_run=args.dry_run)
    md = (f"## schema-site (dry_run={args.dry_run})\n\n"
          + summary.table(["key", "bytes", "status"], rows))
    summary.write(md)
    return 0


def cmd_status_overlay(args) -> int:
    """Write the nightly status.json beside every published record (TV8),
    and with --ensure-labels create a pack:<id> label for each live id."""
    from . import community, reportsite, summary
    from .upload import LocalStore, R2Store
    store = LocalStore(args.source) if args.source else R2Store.from_env(args.bucket)
    today = _dt.date.fromisoformat(args.date) if args.date else _dt.date.today()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    pub = Path(args.pub).read_text(encoding="utf-8") if args.pub else None
    try:
        manifest = community.load_live_manifest(store, pub)
        if args.ensure_labels:
            community.ensure_labels({p["id"] for p in manifest.get("packs") or []},
                                    args.repo, token, dry_run=args.dry_run)
        issues = (json.loads(Path(args.issues).read_text(encoding="utf-8")) if args.issues
                  else community.fetch_pack_issues(args.repo, token))
        objects = community.build_status(store, reportsite.load_records(store), manifest,
                                         issues, today)
        rows = community.publish_status(store, objects, dry_run=args.dry_run)
    except (community.CommunityError, reportsite.SiteError) as e:
        print(f"status-overlay: {e}", file=sys.stderr)
        return 1
    summary.write(f"## status-overlay (dry_run={args.dry_run}, as of {today})\n\n"
                  + summary.table(["key", "bytes", "status"], rows))
    return 0


def cmd_release_assets(args) -> int:
    """After a publish: the files for one GitHub Release per publish event
    (TV8). Writes nothing when this job published no edition."""
    from . import community, reportsite
    from .upload import LocalStore, R2Store
    store = LocalStore(args.source) if args.source else R2Store.from_env(args.bucket)
    pub = Path(args.pub).read_text(encoding="utf-8") if args.pub else None
    server, repo, run = (os.environ.get(k, "") for k in
                         ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    try:
        community.release_assets(
            args.records, community.load_live_manifest(store, pub), args.out,
            today=_dt.date.fromisoformat(args.date) if args.date else _dt.date.today(),
            run_id=run, run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            run_url=f"{server}/{repo}/actions/runs/{run}" if server and repo and run else "",
            commit=reportsite.generator_commit())
    except (community.CommunityError, reportsite.SiteError) as e:
        print(f"release-assets: {e}", file=sys.stderr)
        return 1
    return 0


def _records_markdown(record_dir: str) -> str | None:
    """The job summary's test records, rendered from the JSON the publish
    gate wrote (``<id>-<cycle>.gate.json`` beside the record). None if no
    pack reached the gate."""
    import json
    from . import summary
    out = []
    for verdict in sorted(Path(record_dir).glob("*.gate.json")):
        v = json.loads(verdict.read_text(encoding="utf-8"))
        stem = verdict.name[: -len(".gate.json")]
        rec_path = verdict.with_name(f"{stem}.{'report' if v['accepted'] else 'rejected'}.json")
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
        out.append(summary.record_markdown(rec, blockers=v["blockers"], previous=v["previous"]))
    return "\n".join(out) if out else None


def cmd_job_summary(args) -> int:
    """Append a dispatch build's summary to $GITHUB_STEP_SUMMARY (and print
    it). Never fails the job: it runs under `if: always()` to describe
    whatever happened, including a failed build. With ``--records`` the
    checks come from the test records the publish gate wrote; if no pack
    reached the gate it says so and describes the built files instead."""
    from . import summary
    inputs = dict(kv.split("=", 1) for kv in args.input)
    checks = [(c.split("|", 2) + ["", ""])[:3] for c in args.check]
    records = _records_markdown(args.records) if args.records else None
    if args.records:
        head = f"## {args.title}\n\n" + (summary.table(["input", "value"], list(
            map(list, inputs.items()))) if inputs else "")
        if records:
            md = head + "\n" + records
        else:
            md = (head + "\n**No test record:** no pack reached the publish gate (the build "
                  "failed, or publish was off).\n\n"
                  + "\n".join(summary.artifact_report(Path(f).name, f, level=3)
                               for f in args.file))
    else:
        if not args.file:
            raise SystemExit("job-summary: pass --file or --records")
        first, *rest = args.file
        md = "\n".join([summary.artifact_report(args.title, first, checks=checks, inputs=inputs)]
                        + [summary.artifact_report(Path(f).name, f, level=3) for f in rest])
    print(md)
    summary.write(md)
    return 0


def cmd_verify(args) -> int:
    pub = Path(args.pub).read_text(encoding="ascii")
    try:
        trusted = signing.verify_file(args.manifest, pub)
    except Exception as e:
        print(f"SIGNATURE INVALID: {e}", file=sys.stderr)
        return 2
    m = Manifest.read(args.manifest)
    print(f"signature OK  (trusted comment: {trusted!r})")
    print(f"manifest_version={m.manifest_version}  packs={len(m.packs)}")
    for p in m.packs:
        window = f"{p.effective}..{p.expires}" if p.effective else "non-cyclical"
        print(f"  {p.id:<22} {p.kind:<10} {p.cycle:<8} {window:<24} {p.bytes:>12,} B")
    return 0


def _published_at(store, secret, edition: str) -> set[str]:
    """Pack ids the signed live manifest already lists at ``edition``."""
    from .gate import load_signed
    live, _ = load_signed(store, secret)
    return {p.id for p in (live.packs if live else []) if p.cycle == edition}


def cmd_make_terrain(args) -> int:
    from . import make_terrain
    compress = not args.no_compress
    store = secret = None
    only = args.only or None
    if args.upload:
        # Terrain's origin label is make_terrain.TERRAIN_ORIGIN unless given.
        # Fail before the build, not after it, if the device probe (ACC-001)
        # has no pyEfis checkout.
        args.origin = args.origin or make_terrain.TERRAIN_ORIGIN
        _require_device_reader(args)
        from .regions import load_regions
        from .upload import R2Store
        store = R2Store.from_env(args.bucket)
        secret = _load_secret(args)
        # A published record is immutable, so an (id, edition) already listed
        # is skipped, as the cyclical run skips a present cycle. This keeps a
        # re-run of a part-failed edition re-runnable.
        listed = _published_at(store, secret, args.edition)
        if args.mosaic and make_terrain.MOSAIC_PACK_ID in listed:
            print(f"skip {make_terrain.MOSAIC_PACK_ID} {args.edition}: already published")
            return 0
        if not args.mosaic:
            keys = only or list(load_regions(args.regions))
            only = [k for k in keys if f"terrain-{k}" not in listed]
            for k in sorted(set(keys) - set(only)):
                print(f"skip terrain-{k} {args.edition}: already published")
            if not only:
                return 0
    if args.mosaic:
        # Package the pre-built coarse mosaic (one whole-extent pack, shared by
        # all region combinations) instead of the per-region native packs.
        tp = make_terrain.build_mosaic_pack(
            args.source, out_dir=args.out, edition=args.edition,
            url_base=args.url_base, compress=compress)
        if tp is None:
            print("no mosaic under <source>/.mip/mosaic/ -- run pyEfis "
                  "tools/build_terrain_mosaic.py first")
            return 1
        packs = [tp]
        print(f"  mosaic: {tp.tile_count} level file(s) -> {tp.path.name} "
              f"({tp.entry.bytes:,} B, sha {tp.entry.sha256[:12]}...)")
    else:
        packs = make_terrain.make_terrain_packs(
            src_root=args.source, out_dir=args.out, edition=args.edition,
            url_base=args.url_base, regions_path=args.regions,
            only_regions=only, compress=compress)
        if not packs:
            return 1
    if args.require_wmask:
        # Gate BEFORE upload/sign: an edition meant to carry water masks must
        # not publish maskless because the mask build quietly wrote nothing.
        bad = 0
        for tp in packs:
            missing = make_terrain.missing_wmask_members(tp.path)
            if missing:
                bad += 1
                print(f"!! {tp.path.name}: {len(missing)} masked-level .hgt "
                      f"member(s) without a .wmask sibling, e.g. {missing[:3]}",
                      file=sys.stderr)
        if bad:
            print(f"--require-wmask: {bad} pack(s) missing water masks; "
                  "not uploading", file=sys.stderr)
            return 1
        print(f"--require-wmask: .wmask present beside every masked-level "
              f".hgt in {len(packs)} pack(s)")
    if args.upload:
        from .gate import GateError, GateRefused
        try:
            make_terrain.update_manifest(store, secret, packs,
                                         generated=make_terrain._now_stamp(), sign=signing.sign,
                                         gate=_gate_options(args, Path(args.out) / "reports"))
        except (GateError, GateRefused) as e:
            return _publish_refused(e)
    else:
        print(f"built {len(packs)} pack(s) under {args.out}/packs "
              f"(not uploaded; pass --upload with R2_* env + a key)")
    return 0


def cmd_make_plates(args) -> int:
    from . import make_plates

    if args.cycle:
        if not (args.effective and args.expires):
            raise SystemExit("--cycle requires --effective and --expires too")
        cycle_obj = cycles.Cycle(cycle=args.cycle,
                                 effective=_dt.date.fromisoformat(args.effective),
                                 expires=_dt.date.fromisoformat(args.expires))
    else:
        date = _dt.date.fromisoformat(args.date) if args.date else _dt.date.today()
        cycle_obj, _nxt = cycles.current_and_next("airac", today=date)

    packs = make_plates.run(
        out_dir=args.out, cycle=cycle_obj, url_base=args.url_base, work_dir=args.work,
        only_regions=args.only or None, procedures_db=args.procedures_db,
        ourairports_cache_dir=args.ourairports_cache)
    if not packs:
        print("no plate packs built (no region matched any airport -- check --only / region coverage)")
        return 1
    if args.upload:
        from .upload import R2Store
        store = R2Store.from_env(args.bucket)
        secret = _load_secret(args)
        from .gate import GateError, GateRefused, Options
        try:
            make_plates.update_manifest(store, secret, packs,
                                        generated=_utc_stamp(_dt.date.today()), sign=signing.sign,
                                        gate=Options(record_dir=Path(args.out) / "reports"))
        except (GateError, GateRefused) as e:
            return _publish_refused(e)
        print(f"uploaded {len(packs)} plate pack(s) -> R2, manifest re-signed")
    else:
        print(f"built {len(packs)} plate pack(s) under {args.out}/packs "
              f"(not uploaded; pass --upload with R2_* env + a key)")
    return 0


def cmd_fetch_pack(args) -> int:
    """Download one published pack, verified through the signed manifest;
    the local path is the last line on stdout (for shell capture)."""
    from . import fetch_pack
    from .fetch import http_get as _get

    day = _dt.date.fromisoformat(args.date) if args.date else _dt.date.today()
    pub = Path(args.pub).read_text(encoding="ascii")
    try:
        entry = fetch_pack.resolve_entry(
            _get(args.manifest_url), _get(args.manifest_url + ".minisig").decode("ascii"),
            pub, args.id, day)
        log = lambda msg: print(msg, file=sys.stderr)
        path = fetch_pack.fetch_verified_pack(entry, args.dest, log=log)
    except Exception as e:
        print(f"fetch-pack {args.id}: {e}", file=sys.stderr)
        return 2
    print(path)
    return 0


def cmd_build_roads(args) -> int:
    from . import make_roads
    make_roads.build_na_roads(args.states, args.dest, args.cache_dir,
                              keep_zips=args.keep_zips)
    print(f"built {args.dest} -- next: packtool build-pack {args.dest} "
          "--id highways-conus --kind highways --cycle <edition> ... "
          "(docs/roads.md)")
    return 0


def _kv_list(items, flag: str) -> dict:
    out = {}
    for item in items or []:
        k, sep, v = item.partition("=")
        if not sep or not k:
            raise SystemExit(f"{flag} wants KEY=VALUE, got {item!r}")
        out[k] = v
    return out


def _list_checks(kind: str | None) -> int:
    from . import report
    cats = report.all_checks()
    for k in ([kind] if kind else sorted(cats)):
        print(f"== {k}" + (f"  (UNGATED: {report.UNGATED[k]})" if k in report.UNGATED else ""))
        for c in cats[k]:
            flag = "blocking" if c.blocking else "non-blocking"
            print(f"  {c.id:<14} {flag:<13} {c.title}")
            print(f"  {'':<14} expected:     {c.expected}")
            print(f"  {'':<14} cannot catch: {c.cannot_catch}")
    return 0


def _published_report_for(pack: Path, args) -> dict:
    """Find the published record for these bytes through the signed live
    manifest: verify the signature, match the pack's sha256 to an entry,
    fetch its report_url and check it against report_sha256."""
    import json
    from .fetch import http_get
    from .manifest import Manifest
    sha = signing.sha256_file(pack)
    raw = http_get(args.manifest_url)
    signing.verify(raw, http_get(args.manifest_url + ".minisig").decode("ascii"),
                   Path(args.pub).read_text(encoding="ascii"))
    entry = next((p for p in Manifest.from_bytes(raw, lenient=True).packs if p.sha256 == sha), None)
    if entry is None:
        raise SystemExit("these bytes match no entry in the signed manifest; pass --against")
    if not entry.report_url:
        raise SystemExit(f"{entry.id} {entry.cycle} has no published report yet; pass --against")
    body = http_get(entry.report_url)
    import hashlib
    if hashlib.sha256(body).hexdigest() != entry.report_sha256:
        raise SystemExit(f"{entry.report_url} does not match the report_sha256 the signed "
                         "manifest carries")
    print(f"published record: {entry.report_url} (sha256 matches the signed manifest)")
    return json.loads(body)


def _load_against(spec: str) -> dict:
    import json
    if spec.startswith(("https://", "http://")):
        from .fetch import http_get
        return json.loads(http_get(spec))
    return json.loads(Path(spec).read_text(encoding="utf-8"))


def cmd_report(args) -> int:
    from . import report
    if args.list_checks:
        return _list_checks(args.kind)
    if not args.pack:
        raise SystemExit("report: a pack file is required (or --list-checks)")
    pack = Path(args.pack)
    if not pack.is_file():
        raise SystemExit(f"pack not found: {pack}")
    previous = report.previous_from(args.previous) if args.previous else None
    common = dict(previous=previous, inputs=args.inputs, pyefis_src=args.pyefis_src,
                  probe=not args.no_device, terrain=args.terrain, airports=args.airports,
                  golden_facts=args.golden_facts)

    if args.check:
        published = (_load_against(args.against) if args.against
                     else _published_report_for(pack, args))
        # Re-run with --origin only if the record says it was passed, so
        # PACK-003's "how the label was set" reproduces either way.
        given = any(c["id"] == "PACK-003" and report.ORIGIN_PASSED in str(c.get("measured"))
                    for c in published.get("checks") or [])
        here = report.build_report(pack, origin=published.get("origin") if given else None,
                                   **common)
        rows, problems = report.compare(published, here)
        for r in rows:
            print(f"{r.id:<14} {r.outcome:<22} published {r.published}")
            if r.outcome in ("MISMATCH", "not reproducible here"):
                print(f"{'':<37} here      {r.here}")
        for line in report.auspex_lines(published):
            print(line)
        for p in problems:
            print(f"MISMATCH {p}")
        bad = problems + [r.id for r in rows if r.outcome == "MISMATCH"]
        n_ok = sum(1 for r in rows if r.outcome == "reproduced")
        print(f"{n_ok} reproduced, {len(bad)} mismatched, "
              f"{sum(1 for r in rows if r.outcome == 'not reproducible here')} not reproducible here")
        return 1 if bad else 0

    sources = []
    if args.sources:
        import json
        sources = json.loads(Path(args.sources).read_text(encoding="utf-8"))
    rec = report.build_report(
        pack, record=args.record, origin=args.origin, sources=sources,
        notes=_kv_list(args.note, "--note"), commit=args.build_commit,
        builder_refs=_kv_list(args.builder_ref, "--builder-ref"),
        first_edition=args.first_edition, **common)
    errs = report.validate_report(rec)
    out = Path(args.out or f"{rec['pack']['id']}-{rec['pack']['cycle']}.report.json")
    out.write_bytes(report.to_bytes(rec))
    s = rec["summary"]
    print(f"{out}: {s['pass']} pass / {s['warn']} warn / {s['fail']} fail / "
          f"{s['not_run']} not run")
    for c in rec["checks"]:
        if c["status"] != "pass":
            detail = c["measured"] if c["status"] != "not-run" else c.get("note", "")
            print(f"  {c['status'].upper():<8} {c['id']:<14} {detail}")
    for e in errs:
        print(f"  MALFORMED {e}", file=sys.stderr)
    blockers = report.publish_blockers(rec)
    for b in blockers:
        print(f"  BLOCKS PUBLISH  {b}")
    return 1 if (blockers or errs) else 0


def cmd_diffmap(args) -> int:
    """Write the per-state diff maps (OSM-002/OSM-006) for two records as
    one standalone HTML page: the same figures the report site renders."""
    import html
    from . import diffmap
    cur, prev = _load_against(args.report), _load_against(args.previous)
    body = diffmap.page(cur, prev)
    if not body:
        print("diffmap: the two records carry no per-state metrics to compare "
              "(OSM-002/OSM-006)", file=sys.stderr)
        return 1
    p = cur.get("pack") or {}
    title = html.escape(f"{p.get('id')} {p.get('cycle')}: changes by state or province")
    Path(args.out).write_text(
        f"<!doctype html><meta charset=utf-8><title>{title}</title><style>"
        "body{font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
        f"color:#1c2430;max-width:1000px;margin:20px auto;padding:0 16px}}{diffmap.CSS}"
        f"</style><h1>{title}</h1>{body}\n", encoding="utf-8")
    print(f"{args.out}: {body.count('<figure')} maps")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="packtool")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("genkey", help="generate a signing keypair")
    g.add_argument("--out", default="keys")
    g.add_argument("--comment", default="makerplane-data public key")
    g.set_defaults(func=cmd_genkey)

    b = sub.add_parser("build-pack", help="turn a built sqlite/zip into a signed pack")
    b.add_argument("source")
    b.add_argument("--id", required=True)
    b.add_argument("--kind", required=True, choices=KINDS)
    b.add_argument("--cycle")
    b.add_argument("--effective")
    b.add_argument("--expires")
    b.add_argument("--attribution", default="")
    b.add_argument("--license", default="", help="machine-checkable license tag, e.g. ODbL-1.0")
    b.add_argument("--license-url", dest="license_url", default="", help="canonical URL to the license text")
    b.add_argument("--regions", nargs="*", default=[])
    b.add_argument("--min-pyefis", dest="min_pyefis")
    b.add_argument("--out", default="work")
    b.add_argument("--url-base", default=_DEFAULT_URL_BASE)
    b.add_argument("--keep", type=int, default=2)
    b.add_argument("--date", help="treat this ISO date as 'today' (reproducible builds)")
    b.add_argument("--sec", help="secret key file (or set MINISIGN_SECRET_KEY)")
    b.add_argument("--upload", action="store_true", help="upload to R2 + re-sign the live manifest")
    b.add_argument("--bucket", default="makerplane-data")
    b.add_argument("--origin", help="origin label for the test record: federal / "
                    "community / user-supplied (default: by kind)")
    b.add_argument("--note", action="append", metavar="CHECK_ID=TEXT",
                    help="build note for a check that warns, written by a person for this "
                         "edition ([PACK_ID:]CHECK_ID=TEXT; a warn without one refuses the publish)")
    b.add_argument("--notes-file", dest="notes_file",
                    help="build notes: a JSON object or KEY=TEXT lines, same keys as --note")
    b.add_argument("--sources", help="JSON file: the test record's sources[] array")
    b.add_argument("--pyefis-src", dest="pyefis_src",
                    help="pyEfis checkout's src/ for device acceptance "
                         "(default: PYEFIS_SRC_DIR, or PYEFIS_TOOLS_DIR/../src)")
    b.set_defaults(func=cmd_build_pack)

    rp = sub.add_parser("remove-pack", help="retract one (id, cycle) entry from "
                        "the live manifest and re-sign (undo a bad publish)")
    rp.add_argument("--id", required=True)
    rp.add_argument("--cycle", required=True)
    rp.add_argument("--bucket", default="makerplane-data")
    rp.add_argument("--sec", help="secret key file (or set MINISIGN_SECRET_KEY)")
    rp.add_argument("--date", help="treat this ISO date as 'today' (reproducible)")
    rp.set_defaults(func=cmd_remove_pack)

    bl = sub.add_parser("backfill-licenses", help="fill blank license/license_url "
                        "on live manifest entries from their attribution, and re-sign")
    bl.add_argument("--dry-run", action="store_true", help="report the fills; write nothing")
    bl.add_argument("--manifest-url", help="preview against this public manifest "
                    "instead of R2 (requires --dry-run)")
    bl.add_argument("--bucket", default="makerplane-data")
    bl.add_argument("--sec", help="secret key file (or set MINISIGN_SECRET_KEY)")
    bl.add_argument("--date", help="treat this ISO date as 'today' (reproducible)")
    bl.set_defaults(func=cmd_backfill_licenses)

    rt = sub.add_parser("retro", help="write and bind a retro pack-report/1 record for "
                        "every live edition published without one (TV4)")
    rt.add_argument("--only", help="comma list of pack ids (default: every unbound edition)")
    rt.add_argument("--scratch", default="work/retro-scratch",
                    help="where each pack is downloaded; deleted before the next")
    rt.add_argument("--out", help="also write each record, schema and the run summary here")
    rt.add_argument("--dry-run", action="store_true",
                    help="write records locally only; upload nothing, sign nothing")
    rt.add_argument("--from-url", help="with --dry-run: read the manifest and packs from "
                    "this public origin, e.g. https://navdata.aerocommons.org")
    rt.add_argument("--from", dest="source", metavar="DIR",
                    help="use this directory as the store instead of the bucket")
    rt.add_argument("--pyefis-src", dest="pyefis_src",
                    help="pyEfis src/ for the device probe (default: PYEFIS_SRC_DIR)")
    rt.add_argument("--no-device", action="store_true",
                    help="skip the device probe (ACC-001 is then not-run in the record)")
    rt.add_argument("--no-schema", action="store_true",
                    help="do not write schema/<kind>.json (SCH1)")
    rt.add_argument("--bucket", default="makerplane-data")
    rt.add_argument("--sec", help="secret key file (or set MINISIGN_SECRET_KEY)")
    rt.set_defaults(func=cmd_retro)

    ds = sub.add_parser("deploy-site", help="upload changed site/ files to the "
                        "bucket root (navdata.aerocommons.org)")
    ds.add_argument("--root", default="site", help="site directory (default: site)")
    ds.add_argument("--dry-run", action="store_true",
                    help="compare against the live objects; write nothing")
    ds.add_argument("--bucket", default="makerplane-data")
    ds.set_defaults(func=cmd_deploy_site)

    rs = sub.add_parser("report-site", help="render the test-report pages from the "
                        "published records and upload the changed ones")
    rs.add_argument("--bucket", default="makerplane-data")
    rs.add_argument("--from", dest="source", metavar="DIR",
                    help="read records and manifest.json from this directory "
                    "instead of the bucket")
    rs.add_argument("--out", metavar="DIR", help="write a browsable preview here "
                    "(the site, the catalog, the records and the pages) instead of "
                    "uploading")
    rs.add_argument("--root", default="site", help="site directory for --out (default: site)")
    rs.add_argument("--dry-run", action="store_true",
                    help="compare against the live objects; write nothing")
    rs.set_defaults(func=cmd_report_site)

    sc = sub.add_parser("schema-site", help="render the pack format pages (/schema/) from "
                        "the schema captures and upload the changed ones")
    sc.add_argument("--bucket", default="makerplane-data")
    sc.add_argument("--from", dest="source", metavar="DIR",
                    help="read captures and the signed manifest from this directory "
                    "instead of the bucket")
    sc.add_argument("--out", metavar="DIR", help="write a browsable preview here "
                    "instead of uploading")
    sc.add_argument("--root", default="site", help="site directory for --out (default: site)")
    sc.add_argument("--pub", help="public key to check manifest.json against "
                    "(default: keys/minisign.pub)")
    sc.add_argument("--bootstrap", action="store_true",
                    help="first capture each live pack id that has no capture yet, from "
                    "the published pack (never replaces a publish-path capture)")
    sc.add_argument("--dry-run", action="store_true",
                    help="compare against the live objects; write nothing (skips --bootstrap)")
    sc.set_defaults(func=cmd_schema_site)

    so = sub.add_parser("status-overlay", help="write the nightly unsigned status.json "
                        "beside every published test record (state, pack:<id> issues, "
                        "Auspex addenda)")
    so.add_argument("--bucket", default="makerplane-data")
    so.add_argument("--from", dest="source", metavar="DIR",
                    help="read and write this directory instead of the bucket")
    so.add_argument("--repo", default="billmallard/makerplane-data",
                    help="GitHub repo whose pack:<id> issues are listed")
    so.add_argument("--issues", metavar="JSON", help="read the issues from this file "
                    "(GitHub REST shape) instead of the API")
    so.add_argument("--ensure-labels", action="store_true",
                    help="create a pack:<id> label for every id in the live catalog")
    so.add_argument("--pub", help="public key file (default: keys/minisign.pub)")
    so.add_argument("--date", help="treat this ISO date as 'today' (reproducible)")
    so.add_argument("--dry-run", action="store_true",
                    help="compare against the live objects; write nothing")
    so.set_defaults(func=cmd_status_overlay)

    ra = sub.add_parser("release-assets", help="after a publish: the files for a GitHub "
                        "Release holding each published edition's test record")
    ra.add_argument("--records", required=True, metavar="DIR",
                    help="the publish gate's record directory (<out>/reports)")
    ra.add_argument("--out", required=True, metavar="DIR")
    ra.add_argument("--bucket", default="makerplane-data")
    ra.add_argument("--from", dest="source", metavar="DIR",
                    help="read the signed manifest from this directory instead of the bucket")
    ra.add_argument("--pub", help="public key file (default: keys/minisign.pub)")
    ra.add_argument("--date", help="treat this ISO date as 'today' (reproducible)")
    ra.set_defaults(func=cmd_release_assets)

    js = sub.add_parser("job-summary", help="write a dispatch build's check table "
                        "to the GitHub Actions job summary")
    js.add_argument("--title", required=True)
    js.add_argument("--file", action="append", default=[],
                    help="built artifact to describe (repeatable; first gets the tables)")
    js.add_argument("--records", metavar="DIR",
                    help="the publish gate's record directory (<out>/reports): render "
                         "the summary from the test records")
    js.add_argument("--input", action="append", default=[], metavar="KEY=VALUE")
    js.add_argument("--check", action="append", default=[], metavar="CHECK|RESULT|DETAIL")
    js.set_defaults(func=cmd_job_summary)

    v = sub.add_parser("verify", help="verify a signed manifest")
    v.add_argument("manifest")
    v.add_argument("--pub", default="keys/minisign.pub")
    v.set_defaults(func=cmd_verify)

    t = sub.add_parser("make-terrain", help="build terrain packs from an HGT tile tree")
    t.add_argument("source", help="root of the HGT tile tree (e.g. .../glo30hgt)")
    t.add_argument("--edition", required=True, help="edition tag, e.g. 2024ed")
    t.add_argument("--regions", help="regions.yaml path (default: repo regions.yaml)")
    t.add_argument("--only", nargs="*", help="limit to these region keys")
    t.add_argument("--mosaic", action="store_true",
                   help="package the pre-built coarse mosaic (.mip/mosaic/) as "
                        "the single national terrain-mosaic pack (ignores --only)")
    t.add_argument("--out", default="work")
    t.add_argument("--url-base", default=_DEFAULT_URL_BASE)
    t.add_argument("--no-compress", action="store_true", help="store uncompressed (faster)")
    t.add_argument("--upload", action="store_true", help="upload to R2 + update the manifest")
    t.add_argument("--require-wmask", action="store_true",
                   help="refuse to upload unless every mip/mosaic .hgt member "
                        "carries a .wmask sibling (pyEfis build_water_masks.py)")
    t.add_argument("--bucket", default="makerplane-data")
    t.add_argument("--sec", help="secret key file (or set MINISIGN_SECRET_KEY)")
    t.add_argument("--origin", help="origin label for the test record: federal / "
                    "community / user-supplied (default: make_terrain.TERRAIN_ORIGIN, federal)")
    t.add_argument("--note", action="append", metavar="CHECK_ID=TEXT",
                    help="build note for a check that warns, written by a person for this "
                         "edition ([PACK_ID:]CHECK_ID=TEXT; a warn without one refuses the publish)")
    t.add_argument("--notes-file", dest="notes_file",
                    help="build notes: a JSON object or KEY=TEXT lines, same keys as --note")
    t.add_argument("--sources", help="JSON file: the test record's sources[] array")
    t.add_argument("--pyefis-src", dest="pyefis_src",
                    help="pyEfis checkout's src/ for device acceptance "
                         "(default: PYEFIS_SRC_DIR, or PYEFIS_TOOLS_DIR/../src)")
    t.set_defaults(func=cmd_make_terrain)

    p = sub.add_parser("make-plates", help="fetch+build per-region d-TPP plate packs")
    p.add_argument("--cycle", help="explicit AIRAC cycle (e.g. 2609); requires --effective/--expires")
    p.add_argument("--effective", help="ISO date, with --cycle")
    p.add_argument("--expires", help="ISO date, with --cycle")
    p.add_argument("--date", help="treat this ISO date as 'today' when auto-computing the cycle")
    p.add_argument("--work", default="work/plates", help="working dir for the metafile + PDF fetch")
    p.add_argument("--only", nargs="*", help="limit to these region keys")
    p.add_argument("--procedures-db", dest="procedures_db",
                   help="path to an already-built procedures-conus pack -- enables the "
                        "IAP georeferencing attempt (packtools/build/georef.py); omit to "
                        "skip it (every plate ships with no geo tag)")
    p.add_argument("--ourairports-cache", dest="ourairports_cache",
                   help="cache dir for the OurAirports region-join CSVs")
    p.add_argument("--out", default="work")
    p.add_argument("--url-base", default=_DEFAULT_URL_BASE)
    p.add_argument("--upload", action="store_true", help="upload to R2 + update the manifest")
    p.add_argument("--bucket", default="makerplane-data")
    p.add_argument("--sec", help="secret key file (or set MINISIGN_SECRET_KEY)")
    p.set_defaults(func=cmd_make_plates)

    f = sub.add_parser("fetch-pack", help="download one published pack, verified "
                       "against the signed manifest (sha256 + signature)")
    f.add_argument("id", help="pack id, e.g. water-na")
    f.add_argument("--dest", required=True, help="cache dir; a matching copy is reused")
    f.add_argument("--manifest-url", default=_DEFAULT_URL_BASE.rsplit("/", 1)[0] + "/manifest.json")
    f.add_argument("--pub", default="keys/minisign.pub")
    f.add_argument("--date", help="select the edition current on this ISO date")
    f.set_defaults(func=cmd_fetch_pack)

    r = sub.add_parser("build-roads", help="fetch Geofabrik state road layers "
                       "+ build highways.sqlite (docs/roads.md)")
    r.add_argument("--states", default="conus",
                   help="'conus', or a comma list of Geofabrik state slugs")
    r.add_argument("--dest", default="highways.sqlite")
    r.add_argument("--cache-dir", default="work/geofabrik-roads")
    r.add_argument("--keep-zips", action="store_true",
                   help="don't delete state zips after extraction")
    r.set_defaults(func=cmd_build_roads)

    rep = sub.add_parser("report", help="write a pack's pack-report/1 test record, or "
                         "re-run its open checks with --check (docs/pack_report.md)")
    rep.add_argument("pack", nargs="?", help="the pack file")
    rep.add_argument("--check", action="store_true",
                     help="re-run the open checks and diff against the published record")
    rep.add_argument("--against", help="published report.json (path or URL) for --check; "
                     "default: look the pack up in the signed live manifest")
    rep.add_argument("--list-checks", action="store_true", help="print the check catalogue")
    rep.add_argument("--kind", choices=KINDS, help="with --list-checks: one kind only")
    rep.add_argument("--previous", help="previous edition: its pack file or its report.json")
    rep.add_argument("--first-edition", action="store_true",
                     help="declare that this id has no earlier edition; the only way a "
                          "blocking delta check may be not-run in a build record")
    rep.add_argument("--inputs", help="extracted upstream files, for schema_guard layers 1-3")
    rep.add_argument("--pyefis-src", dest="pyefis_src",
                     help="pyEfis checkout's src/ dir for device acceptance "
                          "(default: PYEFIS_SRC_DIR, or PYEFIS_TOOLS_DIR/../src)")
    rep.add_argument("--no-device", action="store_true", help="skip device acceptance")
    rep.add_argument("--terrain", action="append", metavar="SOURCE",
                     help="Copernicus terrain for XS-001/XS-002: a terrain pack, a directory "
                          "of <N44>/<N44W122>.hgt tiles, or a published pack's https URL "
                          "(read by range request). Repeatable")
    rep.add_argument("--airports", help="an airports/navdata pack, for TER-005 on a terrain pack")
    rep.add_argument("--golden-facts", dest="golden_facts",
                     help="golden facts file for GLD-001 (default: the packaged one)")
    rep.add_argument("--record", choices=("build", "retro", "rejected"), default="build")
    rep.add_argument("--origin", help="federal / community / user-supplied "
                     "(default: by kind; required for terrain)")
    rep.add_argument("--sources", help="JSON file: the record's sources[] array")
    rep.add_argument("--note", action="append", metavar="CHECK_ID=TEXT",
                     help="build note for a check (a warn without one blocks a publish)")
    rep.add_argument("--builder-ref", action="append", metavar="NAME=SHA")
    rep.add_argument("--build-commit", help="makerplane-data commit (default: git HEAD)")
    rep.add_argument("-o", "--out", help="output path (default: <id>-<cycle>.report.json)")
    rep.add_argument("--manifest-url", default=_DEFAULT_URL_BASE.rsplit("/", 1)[0] + "/manifest.json")
    rep.add_argument("--pub", default="keys/minisign.pub")
    rep.set_defaults(func=cmd_report)

    dm = sub.add_parser("diffmap", help="per-state diff maps (OSM-002/OSM-006) for two "
                        "records of an OSM pack, as one HTML page")
    dm.add_argument("report", help="this edition's report.json (path or URL)")
    dm.add_argument("previous", help="the previous edition's report.json (path or URL)")
    dm.add_argument("-o", "--out", default="diffmap.html")
    dm.set_defaults(func=cmd_diffmap)

    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
