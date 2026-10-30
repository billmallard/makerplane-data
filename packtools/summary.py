# SPDX-License-Identifier: Apache-2.0
"""GitHub Actions job summaries: what a pack build actually checked.

The public Actions run page is the one record anyone outside the repo can see,
so each pack workflow appends a check table to ``$GITHUB_STEP_SUMMARY``: the
schema-guard layers and floors that ran (or that none exist for this kind),
the build result, and the skip-if-present decisions. Off Actions (no
``GITHUB_STEP_SUMMARY``) writing is a no-op, so local runs and tests are
unaffected unless they point the variable at a file.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import zipfile
from pathlib import Path


def _cell(v) -> str:
    # One line per cell: a pipe or newline would break the markdown table.
    return str(v).replace("|", "\\|").replace("\n", " ").strip()


def table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def write(markdown: str, path: str | os.PathLike | None = None) -> bool:
    """Append ``markdown`` to the step summary. Returns False (and writes
    nothing) when there is no summary file to write to."""
    path = path or os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return False
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(markdown.rstrip("\n") + "\n\n")
    return True


def sqlite_counts(path: str | os.PathLike) -> dict[str, int]:
    """Row count per user table of a built sqlite (pack_meta included)."""
    con = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                for t in names}
    finally:
        con.close()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def artifact_report(title: str, path: str | os.PathLike, *,
                    checks: list[list[str]] | None = None,
                    inputs: dict[str, str] | None = None, level: int = 2) -> str:
    """Markdown for a one-off (dispatch) build: its inputs, the checks that
    did or did not run, and what the built file contains. ``checks`` rows are
    [check, result, detail]; state "none" plainly rather than omit a layer."""
    md = [f"{'#' * level} {title}\n"]
    if inputs:
        md.append(table(["input", "value"], [[k, v] for k, v in inputs.items()]))
    if checks:
        md.append(table(["check", "result", "detail"], checks))
    p = Path(path)
    if not p.exists():
        md.append(f"**Built file:** `{p}` -- not produced.\n")
        return "\n".join(md)
    md.append(f"**Built file:** `{p.name}` -- {p.stat().st_size:,} B, "
              f"sha256 `{_sha256(p)}`\n")
    if zipfile.is_zipfile(p):
        with zipfile.ZipFile(p) as z:
            md.append(f"zip, {len(z.namelist()):,} member(s)\n")
    else:
        try:
            counts = sqlite_counts(p)
        except sqlite3.DatabaseError as e:
            md.append(f"not a readable sqlite: {e}\n")
        else:
            md.append(table(["table", "rows"],
                            [[t, f"{n:,}"] for t, n in counts.items()]))
    return "\n".join(md)


def record_markdown(rec: dict, *, blockers: list[str] | None = None, key: str = "",
                    previous: str = "", level: int = 3) -> str:
    """One pack's test record for the job summary, rendered from the
    ``pack-report/1`` JSON the publish gate judged, never from anything
    else. Every catalogue check is a row, ``not-run`` ones included, with
    whether it blocks; then why the gate refused the pack, if it did."""
    p = rec["pack"]
    s = rec["summary"]
    refused = rec.get("record") == "rejected"
    md = [f"{'#' * level} {p['id']} {p['cycle']}: "
          + ("REFUSED by the publish gate" if refused else "accepted by the publish gate") + "\n"]
    md.append(f"`{p['sha256']}`, {p['bytes']:,} B, origin {rec.get('origin') or '(unset)'}; "
              f"{s['pass']} pass / {s['warn']} warn / {s['fail']} fail / {s['not_run']} not run"
              + (f"; record at `{key}`" if key else "") + "\n")
    if previous:
        md.append(f"Compared against: {previous}\n")
    if rec.get("gated") is False:
        md.append(f"Ungated kind: {rec.get('ungated', '')}\n")
    rows = []
    for c in rec["checks"]:
        shown = c.get("note", "") if c["status"] == "not-run" else c["measured"]
        rows.append([c["id"], c["status"], "yes" if c["blocking"] else "no", shown,
                     c.get("note", "") if c["status"] != "not-run" else ""])
    md.append(table(["check", "status", "blocks", "measured (or why not run)", "build note"], rows))
    if blockers:
        md.append("**Why it was refused:**\n\n" + "\n".join(f"- {_cell(b)}" for b in blockers) + "\n")
    return "\n".join(md)
