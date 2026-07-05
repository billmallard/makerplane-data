#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""SPDX license-header check (and fixer) for this repository.

The repo is multi-licensed by component (see docs/LICENSE-AUDIT.md):

  * packtools/, pyefis_data/, site/, tests/, scripts/  -> Apache-2.0
    (interfaces, formats, SDK/client code -- permissive so third parties
    can build against the ecosystem)
  * configurator/                                       -> AGPL-3.0-or-later
    (the hosted service -- copyleft so the platform cannot be re-hosted
    closed)
  * configurator/public/editor.html                     -> GPL-3.0-or-later
    (contains instrument-twin rendering code PORTED from GPL-2.0-or-later
    pyEfis widgets; a port is a derived work, taken at v3 for AGPL
    compatibility)

CI runs `python scripts/spdx_check.py` and fails on any tracked source
file without a SPDX header. `--fix` inserts the correct header.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

EXTS = {".py", ".ts", ".js", ".html", ".sql", ".mjs"}

#: (path-prefix or exact path, SPDX id) -- FIRST match wins, so exact
#: file overrides come before their directory.
LICENSE_MAP = [
    ("configurator/public/editor.html", "GPL-3.0-or-later"),
    ("configurator/", "AGPL-3.0-or-later"),
    ("packtools/", "Apache-2.0"),
    ("pyefis_data/", "Apache-2.0"),
    ("site/", "Apache-2.0"),
    ("tests/", "Apache-2.0"),
    ("scripts/", "Apache-2.0"),
]

COMMENT = {
    ".py": "# SPDX-License-Identifier: {}",
    ".sql": "-- SPDX-License-Identifier: {}",
    ".ts": "// SPDX-License-Identifier: {}",
    ".js": "// SPDX-License-Identifier: {}",
    ".mjs": "// SPDX-License-Identifier: {}",
    ".html": "<!-- SPDX-License-Identifier: {} -->",
}


def spdx_for(rel: str) -> str | None:
    for prefix, lic in LICENSE_MAP:
        if rel == prefix or rel.startswith(prefix):
            return lic
    return None


def tracked_sources(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True,
                         text=True, check=True).stdout.splitlines()
    return [f for f in out if Path(f).suffix in EXTS and spdx_for(f)]


def has_header(text: str) -> bool:
    return "SPDX-License-Identifier:" in text[:1000]


def insert_header(path: Path, line: str) -> None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    at = 0
    if lines and (lines[0].startswith("#!")
                  or lines[0].lstrip().lower().startswith("<!doctype")):
        at = 1  # after shebang / doctype (a comment before <!DOCTYPE>
                # would put browsers in quirks mode)
    lines.insert(at, line + "\n")
    path.write_text("".join(lines), encoding="utf-8", newline="\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fix", action="store_true",
                    help="insert missing headers instead of failing")
    args = ap.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    missing = []
    for rel in tracked_sources(root):
        p = root / rel
        if not p.exists():
            continue
        if has_header(p.read_text(encoding="utf-8", errors="replace")):
            continue
        lic = spdx_for(rel)
        if args.fix:
            insert_header(p, COMMENT[p.suffix].format(lic))
            print(f"fixed  {rel}  ({lic})")
        else:
            missing.append((rel, lic))

    if missing:
        for rel, lic in missing:
            print(f"MISSING SPDX header: {rel}  (expected {lic})")
        print(f"\n{len(missing)} file(s) without SPDX headers. "
              "Run: python scripts/spdx_check.py --fix")
        return 1
    print("SPDX headers OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
