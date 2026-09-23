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
        # highways: schema_version has to speak for the on-disk
        # highway_lines table, not just PackMeta's own fields -- detect it
        # from the built pack rather than trusting the package-wide default
        # (AER-1715).
        if args.kind == "highways":
            meta.schema_version = packmeta.detect_highways_schema_version(pack_path)
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
        from .upload import R2Store
        from .publish import publish
        store = R2Store.from_env(args.bucket)
        publish(store, sk, [(entry, pack_path)], generated=_utc_stamp(date),
                sign=signing.sign, comment=f"{args.id} {cycle}")
        print(f"uploaded {pack_path.name} -> R2, manifest re-signed")
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


def cmd_make_terrain(args) -> int:
    from . import make_terrain
    compress = not args.no_compress
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
            only_regions=args.only or None, compress=compress)
        if not packs:
            return 1
    if args.upload:
        from .upload import R2Store
        store = R2Store.from_env(args.bucket)
        secret = _load_secret(args)
        make_terrain.update_manifest(store, secret, packs,
                                     generated=make_terrain._now_stamp(), sign=signing.sign)
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
        make_plates.update_manifest(store, secret, packs,
                                    generated=_utc_stamp(_dt.date.today()), sign=signing.sign)
        print(f"uploaded {len(packs)} plate pack(s) -> R2, manifest re-signed")
    else:
        print(f"built {len(packs)} plate pack(s) under {args.out}/packs "
              f"(not uploaded; pass --upload with R2_* env + a key)")
    return 0


def cmd_build_roads(args) -> int:
    from . import make_roads
    make_roads.build_na_roads(args.states, args.dest, args.cache_dir,
                              keep_zips=args.keep_zips)
    print(f"built {args.dest} -- next: packtool build-pack {args.dest} "
          "--id highways-conus --kind highways --cycle <edition> ... "
          "(docs/roads.md)")
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
    b.set_defaults(func=cmd_build_pack)

    rp = sub.add_parser("remove-pack", help="retract one (id, cycle) entry from "
                        "the live manifest and re-sign (undo a bad publish)")
    rp.add_argument("--id", required=True)
    rp.add_argument("--cycle", required=True)
    rp.add_argument("--bucket", default="makerplane-data")
    rp.add_argument("--sec", help="secret key file (or set MINISIGN_SECRET_KEY)")
    rp.add_argument("--date", help="treat this ISO date as 'today' (reproducible)")
    rp.set_defaults(func=cmd_remove_pack)

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
    t.add_argument("--bucket", default="makerplane-data")
    t.add_argument("--sec", help="secret key file (or set MINISIGN_SECRET_KEY)")
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

    r = sub.add_parser("build-roads", help="fetch Geofabrik state road layers "
                       "+ build highways.sqlite (docs/roads.md)")
    r.add_argument("--states", default="conus",
                   help="'conus', or a comma list of Geofabrik state slugs")
    r.add_argument("--dest", default="highways.sqlite")
    r.add_argument("--cache-dir", default="work/geofabrik-roads")
    r.add_argument("--keep-zips", action="store_true",
                   help="don't delete state zips after extraction")
    r.set_defaults(func=cmd_build_roads)

    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
