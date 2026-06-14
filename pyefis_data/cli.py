"""pyefis-data — the on-Pi navigation-data updater CLI.

    pyefis-data status [--json]      installed vs catalog; currency per pack
    pyefis-data update [--dry-run]   pull stale packs, verify, atomic-swap
    pyefis-data import <dir>         install from a USB stick (same verify path)
    pyefis-data verify [path]        check a manifest's signature

The signing public key is embedded below so verification needs no file and
no network — the Pi trusts this key and nothing else. If the key is ever
rotated, ship a release that updates this constant.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from packtools import signing

from .config import Config
from .core import Updater, EXPIRED, EXPIRES, UPDATE, MISSING, UNKNOWN

# makerplane-data production signing key (key id 178caefeabc5afb1).
# Mirrors keys/minisign.pub in the repo; embedded for runtime independence.
PUBLIC_KEY = (
    "untrusted comment: makerplane-data production signing key "
    "(custody: Bill Mallard; rotatable)\n"
    "RWQXjK7+q8WvsWtCd+QPQNxO7OcI5XuF1uxY7MGp+R2JidxFSO/20AR4\n"
)

# statuses that mean "the EFIS should annunciate" — also used for exit codes.
_ATTENTION = {EXPIRED, EXPIRES, UPDATE, MISSING}


def _updater(args) -> Updater:
    cfg = Config.load(args.config)
    if getattr(args, "base_url", None):
        cfg = _replace(cfg, base_url=args.base_url)
    if getattr(args, "root", None):
        cfg = _replace(cfg, root=Path(args.root))
    return Updater(cfg, PUBLIC_KEY)


def _replace(cfg: Config, **kw) -> Config:
    from dataclasses import replace
    return replace(cfg, **kw)


_SEV_RANK = {"none": 0, "white": 1, "amber": 2}


def _status_doc(up) -> dict:
    rows = up.status()
    worst = max((r.severity for r in rows), key=lambda s: _SEV_RANK.get(s, 1), default="none")
    return {
        "ok": True,
        "generated": up.manifest_generated,
        "worst": worst,
        "any_attention": any(r.status in _ATTENTION for r in rows),
        "packs": [r.as_dict() for r in rows],
    }


def _write_status_json(doc: dict, path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    os.replace(tmp, out)            # atomic: the EFIS never sees a half-written file
    return out


def cmd_status(args) -> int:
    up = _updater(args)
    try:
        doc = _status_doc(up)
    except Exception as e:
        if args.json:
            print(json.dumps({"ok": False, "error": str(e), "packs": []}))
        else:
            print(f"ERROR: could not read catalog: {e}", file=sys.stderr)
        return 2
    if args.json:
        if args.out:
            from .config import default_status_path
            out = default_status_path() if args.out is True else Path(args.out)
            print(f"wrote {_write_status_json(doc, out)}")
        else:
            print(json.dumps(doc, indent=2))
    else:
        for p in doc["packs"]:
            mark = {"amber": "!", "white": "*", "none": " "}.get(p["severity"], " ")
            print(f" {mark} {p['name']:<24} {p['status']:<18} {p['detail']}")
    return 0


def cmd_update(args) -> int:
    up = _updater(args)
    rows = up.update(dry_run=args.dry_run)
    for r in rows:
        print(f"  {r.pack_id:<22} {r.status:<18} {r.detail}")
    # Refresh the status JSON the EFIS reads, so the boot screen / DATA flag
    # reflect the result of this update (this is why a manual Update on the
    # device flips amber -> green without a separate `status` call).
    if not args.dry_run:
        try:
            from .config import default_status_path
            _write_status_json(_status_doc(up), default_status_path())
        except Exception as e:
            print(f"(could not refresh status.json: {e})", file=sys.stderr)
    if up.errors:
        print(f"FAILED: {len(up.errors)} verification error(s); current data left untouched",
              file=sys.stderr)
        return 2
    return 0


def cmd_import(args) -> int:
    up = _updater(args)
    src = Path(args.path)
    if not (src / "manifest.json").exists():
        print(f"no manifest.json under {src}", file=sys.stderr)
        return 2
    rows = up.import_dir(src, dry_run=args.dry_run)
    for r in rows:
        print(f"  {r.pack_id:<22} {r.status:<18} {r.detail}")
    return 2 if up.errors else 0


def cmd_verify(args) -> int:
    if args.path:
        p = Path(args.path)
        sig = p.with_name(p.name + ".minisig")
        try:
            trusted = signing.verify(p.read_bytes(), sig.read_text("ascii"), PUBLIC_KEY)
        except Exception as e:
            print(f"INVALID: {e}", file=sys.stderr)
            return 2
        print(f"OK  (trusted comment: {trusted!r})")
        return 0
    # no path: fetch + verify the live manifest
    up = _updater(args)
    try:
        m = up.fetch_manifest()
    except Exception as e:
        print(f"INVALID or unreachable: {e}", file=sys.stderr)
        return 2
    print(f"OK  signature valid; {len(m.packs)} pack(s); generated {m.generated}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="pyefis-data")
    ap.add_argument("--config", help="path to data.yaml (default ~/.makerplane/pyefis/data.yaml)")
    ap.add_argument("--base-url", help="override the data origin")
    ap.add_argument("--root", help="override the data root dir")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="show installed vs catalog currency")
    s.add_argument("--json", action="store_true")
    s.add_argument("--out", nargs="?", const=True, default=None,
                   help="with --json, write to PATH (default ~/.makerplane/pyefis/status.json)")
    s.set_defaults(func=cmd_status)

    u = sub.add_parser("update", help="download + verify + install stale packs")
    u.add_argument("--dry-run", action="store_true")
    u.set_defaults(func=cmd_update)

    i = sub.add_parser("import", help="install from a USB/local directory")
    i.add_argument("path")
    i.add_argument("--dry-run", action="store_true")
    i.set_defaults(func=cmd_import)

    v = sub.add_parser("verify", help="verify a manifest signature")
    v.add_argument("path", nargs="?", help="manifest file (default: fetch live)")
    v.set_defaults(func=cmd_verify)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
