"""The daily pipeline orchestrator (Leg 1).

For each cyclical source, for the current cycle (and the next, for AIRAC
products the FAA publishes ahead):

    compute cycle -> is the pack already in the store? -> if yes, skip
    -> else fetch upstream -> build sqlite -> embed pack_meta -> compute
    sha256/size -> upload pack -> upsert into the manifest

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
import sys
import tempfile
from pathlib import Path

from . import build as _build
from . import cycles, fetch, signing
from .manifest import Manifest
from .packmeta import PackMeta, embed_sqlite
from .manifest import PackEntry
from .regions import load_regions, manifest_regions_block
from .sources import Source, cyclical_sources
from .upload import LocalStore, ObjectStore, R2Store

MANIFEST_KEY = "manifest.json"
SIG_KEY = "manifest.json.minisig"
_DEFAULT_URL_BASE = "https://data.makerplane.org/packs"


class CyclicalRunner:
    def __init__(self, *, store: ObjectStore, secret: signing.SecretKey | None,
                 url_base: str = _DEFAULT_URL_BASE,
                 work_dir: str | Path | None = None,
                 fetcher=fetch.fetch_and_extract,
                 builders: dict | None = None,
                 today: _dt.date | None = None):
        self.store = store
        self.secret = secret
        self.url_base = url_base.rstrip("/")
        self.work_dir = Path(work_dir or tempfile.mkdtemp(prefix="packtools-"))
        self.fetcher = fetcher
        self.builders = builders if builders is not None else _build.BUILDERS
        self.today = today or _dt.date.today()
        self.log = print

    def _cycles_for(self, source: Source) -> list[cycles.Cycle]:
        cur, nxt = cycles.current_and_next(source.cadence, today=self.today)
        # FAA publishes the next AIRAC cycle ahead of time; DOF is a daily
        # product with no meaningful "next".
        return [cur, nxt] if source.cadence == "airac" else [cur]

    def _load_manifest(self) -> Manifest:
        raw = self.store.get_bytes(MANIFEST_KEY)
        stamp = f"{self.today.isoformat()}T00:00:00Z"
        if raw:
            m = Manifest.from_bytes(raw)
            m.generated = stamp
            return m
        return Manifest.new(stamp)

    def _build_one(self, source: Source, c: cycles.Cycle) -> tuple[PackEntry, Path]:
        builder = self.builders[source.builder]
        cycle_work = self.work_dir / source.pack_id / c.cycle
        cycle_work.mkdir(parents=True, exist_ok=True)
        self.log(f"  fetch {source.url_for(c)}")
        extracted = self.fetcher(source.url_for(c), cycle_work,
                                 member=(source.archive_member or None))
        pack_path = cycle_work / f"{source.pack_id}-{c.cycle}.pack"
        self.log(f"  build {source.builder} -> {pack_path.name}")
        builder(Path(extracted), pack_path)
        meta = PackMeta(id=source.pack_id, kind=source.kind, cycle=c.cycle,
                        effective=c.effective.isoformat(),
                        expires=c.expires.isoformat(),
                        attribution=source.attribution)
        embed_sqlite(pack_path, meta)
        url = f"{self.url_base}/{pack_path.name}"
        return PackEntry.from_pack(pack_path, meta, url=url,
                                   regions=list(source.regions)), pack_path

    def run(self, sources: list[Source] | None = None, *,
            dry_run: bool = False) -> Manifest:
        sources = sources if sources is not None else cyclical_sources()
        m = self._load_manifest()
        m.regions = manifest_regions_block(load_regions())
        built = 0

        for source in sources:
            for c in self._cycles_for(source):
                key = f"packs/{source.pack_id}-{c.cycle}.pack"
                in_store = self.store.exists(key)
                in_manifest = any(p.id == source.pack_id and p.cycle == c.cycle
                                  for p in m.packs)
                if in_store and in_manifest:
                    self.log(f"skip {source.pack_id} {c.cycle} (present)")
                    continue
                if dry_run:
                    self.log(f"WOULD build+upload {source.pack_id} {c.cycle} "
                             f"({c.effective}..{c.expires})")
                    continue
                try:
                    entry, pack_path = self._build_one(source, c)
                except Exception as e:
                    # A not-yet-published next cycle, or a transient upstream
                    # error, must not abort the whole run.
                    self.log(f"WARN {source.pack_id} {c.cycle}: {e}")
                    continue
                self.store.put_file(key, pack_path, content_type="application/octet-stream")
                m.upsert(entry)
                built += 1
                self.log(f"built+uploaded {source.pack_id} {c.cycle} "
                         f"sha256={entry.sha256[:12]}… {entry.bytes:,} B")

        m.prune_old_cycles(keep=2)

        if dry_run:
            self.log(f"dry-run: {built} pack(s) would be built; manifest not written")
            return m

        raw = m.to_bytes()
        sig = signing.sign(raw, self.secret,
                           trusted_comment=f"generated {self.today.isoformat()}")
        self.store.put_bytes(MANIFEST_KEY, raw, content_type="application/json")
        self.store.put_bytes(SIG_KEY, sig.encode("ascii"), content_type="text/plain")
        self.log(f"manifest: {len(m.packs)} pack(s), {built} new, signed + uploaded")
        return m


# --------------------------------------------------------------------------

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
    args = ap.parse_args(argv)

    today = _dt.date.fromisoformat(args.date) if args.date else _dt.date.today()
    work = Path(args.work)

    if args.dry_run or args.no_upload:
        store: ObjectStore = LocalStore(work / "r2")
    else:
        store = R2Store.from_env(args.bucket)

    secret = None if args.dry_run else _secret(args)
    runner = CyclicalRunner(store=store, secret=secret,
                            url_base=args.url_base, work_dir=work, today=today)
    srcs = cyclical_sources()
    if args.only:
        srcs = [s for s in srcs if s.pack_id in set(args.only)]
    runner.run(srcs, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
