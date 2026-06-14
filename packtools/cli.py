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

    # Resolve the cycle + currency window.
    if args.cycle and args.effective:
        cycle, effective = args.cycle, args.effective
        expires = args.expires
    else:
        cadence = _KIND_CADENCE.get(args.kind)
        if cadence is None:
            raise SystemExit(f"kind {args.kind!r} is non-cyclical; pass --cycle (and "
                             f"--effective/--expires if dated) explicitly")
        cur, _nxt = cycles.current_and_next(cadence, today=date)
        cycle = args.cycle or cur.cycle
        effective = cur.effective.isoformat()
        expires = cur.expires.isoformat()

    meta = PackMeta(id=args.id, kind=args.kind, cycle=cycle,
                    effective=effective, expires=expires,
                    attribution=args.attribution)

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
        packmeta.embed_sqlite(pack_path, meta)
    print(f"embedded pack_meta: {read_packmeta(pack_path).as_dict()}")

    url = f"{args.url_base.rstrip('/')}/{pack_path.name}"
    entry = PackEntry.from_pack(pack_path, meta, url=url,
                                regions=args.regions or [],
                                min_pyefis=args.min_pyefis)
    print(f"sha256 {entry.sha256}  bytes {entry.bytes:,}")

    # Load-or-create the manifest, upsert, prune, write.
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

    # Sign the manifest.
    sk = _load_secret(args)
    sig = signing.sign_file(manifest_path, sk,
                            trusted_comment=f"generated {date.isoformat()}")
    print(f"signed -> {sig}")
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
    b.add_argument("--regions", nargs="*", default=[])
    b.add_argument("--min-pyefis", dest="min_pyefis")
    b.add_argument("--out", default="work")
    b.add_argument("--url-base", default=_DEFAULT_URL_BASE)
    b.add_argument("--keep", type=int, default=2)
    b.add_argument("--date", help="treat this ISO date as 'today' (reproducible builds)")
    b.add_argument("--sec", help="secret key file (or set MINISIGN_SECRET_KEY)")
    b.set_defaults(func=cmd_build_pack)

    v = sub.add_parser("verify", help="verify a signed manifest")
    v.add_argument("manifest")
    v.add_argument("--pub", default="keys/minisign.pub")
    v.set_defaults(func=cmd_verify)

    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
