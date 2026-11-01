# SPDX-License-Identifier: Apache-2.0
"""PR impact report for builder changes (TV9, AER-4572).

A PR that touches a builder gets a comment saying what its change does to
the data, before it merges: "+12 runways, -100 % California roads". For each
kind with an in-repo builder and an offline fixture, this builds a small
fixture-region pack twice -- once with the PR base's builder, once with the
head's -- and diffs the two.

What is held fixed, so that a difference can only come from the builder:

* the fixture input: always the head checkout's ``tests/fixtures`` (the
  highways input is synthetic, generated from ``impact/roads_states.json``);
* the runner (``scripts/impact_build.py``, from head);
* the pyEfis checkout the highways builder shells out to (one ref, both sides);
* the packing and the check catalogue: this module and ``report`` from head
  run on both packs, with the base pack as the head pack's previous edition.

What it covers: highways (``make_roads`` -> pyEfis ``build_highway_db``),
procedures and airspace (the CIFP builders). Not covered: navdata, navaids
and obstacles, whose builders live in pyEfis and are not changed by a PR
here; water, terrain and plates, which have no offline fixture yet.

A regression is: the head build fails where the base built; a fixture state
(highways), airport (procedures) or airspace type that had features has
none; a table that had rows has none or is gone; a check that did not fail
on the base pack fails on the head pack. Any regression makes the run exit
1. Every other difference is listed as a change, for a person to read.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import packmeta, report

MARKER = "<!-- tv9-pr-impact-report -->"
RUNNER = Path(__file__).resolve().parent.parent / "scripts" / "impact_build.py"
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


@dataclass
class Fixture:
    kind: str
    label: str
    needs_pyefis: bool = False


FIXTURE_KINDS = {
    "highways": Fixture("highways", "synthetic roads, every CONUS Geofabrik state slug",
                        needs_pyefis=True),
    "procedures": Fixture("procedures", "FAACIFP18 golden fixture (tests/fixtures/cifp)"),
    "airspace": Fixture("airspace", "FAACIFP18 airspace fixture (tests/fixtures/cifp_airspace)"),
}


# --- building ---------------------------------------------------------------

def git_head(tree: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def run_builder(kind: str, tree: Path, out: Path, work: Path, *,
                fixtures: Path = FIXTURES, timeout: int = 900) -> str | None:
    """Build ``kind``'s fixture pack with ``tree``'s builder. Returns ``None``
    on success, else the reason the build failed."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tree)
    cmd = [sys.executable, "-P", str(RUNNER), kind, "--tree", str(tree), "--out", str(out),
           "--fixtures", str(fixtures), "--work", str(work)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"build timed out after {timeout} s"
    if proc.returncode == 0:
        return None
    lines = [ln for ln in (proc.stderr or proc.stdout).splitlines() if ln.strip()]
    return f"exit {proc.returncode}: {lines[-1] if lines else 'no output'}"


def pack(sqlite_path: Path, kind: str, dest: Path) -> Path:
    """Embed pack_meta the way ``packtool build-pack`` does, for the report."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(sqlite_path, dest)
    meta = packmeta.PackMeta(id=f"{kind}-fixture", kind=kind, cycle="fixture",
                             attribution="fixture")
    detected = packmeta.detect_content_schema_version(kind, dest)
    if detected is not None:
        meta.schema_version = detected
    packmeta.embed_sqlite(dest, meta)
    return dest


# --- per-kind breakdowns ----------------------------------------------------

def _roads_spec(fixtures: Path) -> dict:
    return json.loads((fixtures / "impact" / "roads_states.json").read_text(encoding="utf-8"))


def _highways_breakdowns(con, fixtures: Path) -> dict:
    spec = _roads_spec(fixtures)
    centres = spec["states"]
    reach = 3 * float(spec["half_width_deg"])
    states = {s: 0 for s in centres}
    states["(outside every fixture state)"] = 0
    classes: dict[str, int] = {}
    for fclass, la0, la1, lo0, lo1 in con.execute(
            "SELECT fclass, min_lat, max_lat, min_lon, max_lon FROM highway_lines"):
        classes[fclass] = classes.get(fclass, 0) + 1
        lat, lon = (la0 + la1) / 2, (lo0 + lo1) / 2
        best = min(centres, key=lambda s: math.dist((lat, lon), centres[s]))
        if math.dist((lat, lon), centres[best]) <= reach:
            states[best] += 1
        else:
            states["(outside every fixture state)"] += 1
    return {"state": states, "fclass": classes}


def _group_count(con, sql: str) -> dict:
    return {str(k): n for k, n in con.execute(sql)}


def breakdowns(kind: str, path: Path, fixtures: Path = FIXTURES) -> dict:
    """Feature counts by the unit a regression would hide in."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        if kind == "highways":
            return _highways_breakdowns(con, fixtures)
        if kind == "procedures":
            return {"airport": _group_count(con, "SELECT airport, COUNT(*) FROM procedures "
                                                 "GROUP BY airport"),
                    "procedure kind": _group_count(con, "SELECT kind, COUNT(*) FROM procedures "
                                                        "GROUP BY kind")}
        if kind == "airspace":
            return {"type": _group_count(con, "SELECT COALESCE(type, '(none)'), COUNT(*) "
                                              "FROM airspaces GROUP BY 1")}
    except sqlite3.Error as e:
        return {"error": {str(e): 0}}
    finally:
        con.close()
    return {}


# --- diffing ----------------------------------------------------------------

@dataclass
class KindResult:
    kind: str
    label: str
    base_error: str | None = None
    head_error: str | None = None
    not_run: str | None = None
    tables: dict = field(default_factory=dict)       # table -> (base, head)
    groups: dict = field(default_factory=dict)       # group -> {key: (base, head)}
    checks: list = field(default_factory=list)       # (id, base_status, head_status, head_measured)
    regressions: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.regressions or self.errors
                    or any(b != h for b, h in self.tables.values())
                    or any(b != h for g in self.groups.values() for b, h in g.values())
                    or self.checks)


def _pairs(base: dict, head: dict) -> dict:
    return {k: (base.get(k, 0), head.get(k, 0)) for k in sorted(set(base) | set(head))}


def diff(kind: str, base_rec: dict, head_rec: dict, base_bd: dict, head_bd: dict) -> KindResult:
    r = KindResult(kind, FIXTURE_KINDS[kind].label if kind in FIXTURE_KINDS else kind)
    bt = {t: v["rows"] for t, v in (base_rec.get("tables") or {}).items()}
    ht = {t: v["rows"] for t, v in (head_rec.get("tables") or {}).items()}
    for t in sorted(set(bt) | set(ht)):
        b, h = bt.get(t), ht.get(t)
        r.tables[t] = (b, h)
        if b and h is None:
            r.regressions.append(f"table `{t}` is gone (had {b:,} rows)")
        elif b and h == 0:
            r.regressions.append(f"table `{t}` is empty (had {b:,} rows)")
    for g in sorted(set(base_bd) | set(head_bd)):
        pairs = _pairs(base_bd.get(g, {}), head_bd.get(g, {}))
        r.groups[g] = pairs
        for k, (b, h) in pairs.items():
            if b and not h:
                r.regressions.append(f"{g} `{k}`: {b:,} -> 0 (-100 %)")
    bc = {c["id"]: c for c in base_rec.get("checks") or []}
    for c in head_rec.get("checks") or []:
        b = bc.get(c["id"])
        bs = b["status"] if b else "(absent)"
        if bs != c["status"]:
            r.checks.append((c["id"], bs, c["status"], c.get("measured")))
            if c["status"] == "fail":
                r.regressions.append(f"check {c['id']} fails on head ({bs} on base): "
                                     f"{c.get('measured')}")
    return r


def run_kind(kind: str, base: Path, head: Path, work: Path, *,
             fixtures: Path = FIXTURES) -> KindResult:
    fx = FIXTURE_KINDS[kind]
    if fx.needs_pyefis and not os.environ.get("PYEFIS_TOOLS_DIR"):
        return KindResult(kind, fx.label, not_run="PYEFIS_TOOLS_DIR is not set, so the "
                          "pyEfis builder this kind shells out to is not available")
    out = {}
    errs = {}
    for side, tree in (("base", base), ("head", head)):
        sq = work / side / f"{kind}.sqlite"
        errs[side] = run_builder(kind, tree, sq, work / side / "scratch", fixtures=fixtures)
        if errs[side] is None:
            out[side] = pack(sq, kind, work / side / f"{kind}-fixture.pack")
    res = KindResult(kind, fx.label, base_error=errs["base"], head_error=errs["head"])
    if errs["head"] and errs["base"]:
        res.errors.append("neither side builds, so nothing was measured")
        return res
    if errs["head"]:
        res.regressions.append(f"the head build fails (the base builds): {errs['head']}")
        return res
    head_bd = breakdowns(kind, out["head"], fixtures)
    if errs["base"]:
        # Show head's numbers alone: there is nothing to diff against.
        head_rec = report.build_report(out["head"], probe=False)
        res.tables = {t: (None, v["rows"]) for t, v in head_rec["tables"].items()}
        res.groups = {g: {k: (None, n) for k, n in v.items()} for g, v in head_bd.items()}
        return res
    # The base pack is the previous edition of BOTH records, so the delta
    # checks run on both sides (base against itself) and only a real
    # difference moves their status.
    prev = report.build_report(out["base"], probe=False)
    base_rec = report.build_report(out["base"], probe=False, previous=prev)
    head_rec = report.build_report(out["head"], probe=False, previous=prev)
    base_bd = breakdowns(kind, out["base"], fixtures)
    r = diff(kind, base_rec, head_rec, base_bd, head_bd)
    r.base_error, r.head_error = res.base_error, res.head_error
    return r


# --- rendering --------------------------------------------------------------

def _n(v) -> str:
    return "-" if v is None else f"{v:,}"


def _chg(b, h) -> str:
    if b is None or h is None:
        return ""
    if b == h:
        return "="
    if not b:
        return f"+{h:,} (new)"
    return f"{h - b:+,} ({(h - b) / b * 100:+.1f} %)"


def render(results: list[KindResult], *, base_label: str, head_label: str,
           pyefis: str | None = None) -> str:
    regs = [(r.kind, x) for r in results for x in r.regressions]
    errs = [(r.kind, x) for r in results for x in r.errors]
    lines = [MARKER, "## Builder impact report (fixture packs)", ""]
    lines.append(f"Base `{base_label}` vs head `{head_label}`. Both sides build from the same "
                 "fixture input" + (f" and the same pyEfis (`{pyefis[:12]}`)" if pyefis else "")
                 + ", and head's report code checks both packs, so a difference comes from the "
                 "builder change. Fixture packs are small and partly synthetic: this shows what "
                 "the change does, not how real data will look.")
    lines.append("")
    if regs or errs:
        lines.append(f"**{len(regs)} regression(s)" + (f", {len(errs)} error(s)" if errs else "")
                     + ":**")
        lines += [f"- **{k}**: {x}" for k, x in regs + errs]
    else:
        changed = [r.kind for r in results if r.changed]
        lines.append("**No regressions.** " + (f"Changes in: {', '.join(changed)}."
                                                if changed else "No change in any fixture pack."))
    for r in results:
        lines += ["", f"### {r.kind} ({r.label})", ""]
        if r.not_run:
            lines.append(f"Not run: {r.not_run}.")
            continue
        if r.base_error:
            lines.append(f"The base build fails: {r.base_error}. Head's numbers alone.")
            lines.append("")
        if r.head_error:
            lines.append(f"The head build fails: {r.head_error}")
            continue
        if not r.changed:
            lines.append("No change: every table, group count and check status matches the base.")
            continue
        lines += ["| table | base | head | change |", "|---|---:|---:|---|"]
        lines += [f"| `{t}` | {_n(b)} | {_n(h)} | {_chg(b, h)} |" for t, (b, h) in r.tables.items()]
        for g, pairs in r.groups.items():
            moved = {k: v for k, v in pairs.items() if v[0] != v[1]}
            same = len(pairs) - len(moved)
            lines += ["", f"By {g}: " + (f"{len(moved)} changed, {same} unchanged."
                                         if moved else f"all {same} unchanged.")]
            if moved:
                lines += ["", f"| {g} | base | head | change |", "|---|---:|---:|---|"]
                lines += [f"| `{k}` | {_n(b)} | {_n(h)} | {_chg(b, h)} |"
                          for k, (b, h) in moved.items()]
        if r.checks:
            lines += ["", "Checks whose status moved:", "",
                      "| check | base | head | head measured |", "|---|---|---|---|"]
            lines += [f"| {cid} | {b} | {h} | {str(m).replace('|', '/')} |"
                      for cid, b, h, m in r.checks]
    lines += ["", "<sub>TV9 (AER-4572): `python -m packtools.impact` -- see docs/pr_impact.md. "
              "Regenerated on every push to this PR.</sub>", ""]
    return "\n".join(lines)


def to_json(results: list[KindResult]) -> dict:
    return {"schema": "pr-impact/1", "kinds": [
        {"kind": r.kind, "label": r.label, "not_run": r.not_run, "base_error": r.base_error,
         "head_error": r.head_error, "regressions": r.regressions, "errors": r.errors,
         "tables": {t: list(v) for t, v in r.tables.items()},
         "groups": {g: {k: list(v) for k, v in p.items()} for g, p in r.groups.items()},
         "checks": [list(c) for c in r.checks]} for r in results]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m packtools.impact",
                                 description="Diff fixture packs built by two checkouts' builders.")
    ap.add_argument("--base", required=True, help="checkout of the PR's base")
    ap.add_argument("--head", required=True, help="checkout of the PR's head")
    ap.add_argument("--kinds", default=",".join(FIXTURE_KINDS))
    ap.add_argument("--work", required=True, help="scratch directory")
    ap.add_argument("--out-md", help="write the markdown comment here")
    ap.add_argument("--out-json", help="write the machine-readable result here")
    ap.add_argument("--base-label")
    ap.add_argument("--head-label")
    a = ap.parse_args(argv)
    base, head, work = Path(a.base).resolve(), Path(a.head).resolve(), Path(a.work)
    kinds = [k.strip() for k in a.kinds.split(",") if k.strip()]
    unknown = [k for k in kinds if k not in FIXTURE_KINDS]
    if unknown:
        ap.error(f"no fixture for {', '.join(unknown)}; known: {', '.join(FIXTURE_KINDS)}")
    results = [run_kind(k, base, head, work / k) for k in kinds]
    tools = os.environ.get("PYEFIS_TOOLS_DIR")
    md = render(results,
                base_label=a.base_label or (git_head(base) or "base")[:12],
                head_label=a.head_label or (git_head(head) or "head")[:12],
                pyefis=git_head(Path(tools)) if tools else None)
    if a.out_md:
        Path(a.out_md).write_text(md, encoding="utf-8")
    if a.out_json:
        Path(a.out_json).write_text(json.dumps(to_json(results), indent=2) + "\n",
                                    encoding="utf-8")
    print(md)
    return 1 if any(r.regressions or r.errors for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
