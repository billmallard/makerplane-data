# SPDX-License-Identifier: Apache-2.0
"""PR impact report (TV9, AER-4572): packtools.impact + scripts/impact_build.py.

The end-to-end tests build real fixture packs with two copies of this
tree's builders, one of them with a regression planted in it, and check the
report names the regression. The highways one is the makerplane-data#17
shape: a state's roads vanish from the pack while the build stays green.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from packtools import impact

ROOT = Path(__file__).resolve().parent.parent


def _copy_tree(dest: Path) -> Path:
    """A builder tree: this repo's packtools, copied byte for byte (no cp:
    see the workspace note on block-cloned copies)."""
    for src in (ROOT / "packtools").rglob("*"):
        if "__pycache__" in src.parts or not src.is_file():
            continue
        out = dest / src.relative_to(ROOT)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(src.read_bytes())
    return dest


def _plant(tree: Path, rel: str, old: str, new: str) -> None:
    p = tree / rel
    text = p.read_text(encoding="utf-8")
    assert old in text, f"planting failed: {old!r} not in {rel}"
    p.write_text(text.replace(old, new), encoding="utf-8")


# --- diff and render, on records alone --------------------------------------

def _rec(tables: dict, checks: list) -> dict:
    return {"tables": {t: {"rows": n} for t, n in tables.items()},
            "checks": [{"id": i, "status": s, "measured": m} for i, s, m in checks]}


def test_no_difference_is_no_change():
    r = impact.diff("highways", _rec({"t": 5}, [("A", "pass", 1)]),
                    _rec({"t": 5}, [("A", "pass", 1)]), {"state": {"x": 5}}, {"state": {"x": 5}})
    assert not r.regressions and not r.changed
    md = impact.render([r], base_label="b", head_label="h")
    assert md.startswith(impact.MARKER)
    assert "**No regressions.** No change in any fixture pack." in md


def test_a_group_going_to_zero_is_a_regression():
    r = impact.diff("highways", _rec({"t": 10}, []), _rec({"t": 7}, []),
                    {"state": {"ca": 3, "nv": 7}}, {"state": {"nv": 7}})
    assert r.regressions == ["state `ca`: 3 -> 0 (-100 %)"]
    md = impact.render([r], base_label="b", head_label="h")
    assert "**1 regression(s):**" in md and "| `ca` | 3 | 0 | -3 (-100.0 %) |" in md
    assert "| `nv` |" not in md          # unchanged rows are counted, not listed


def test_an_emptied_or_dropped_table_is_a_regression():
    r = impact.diff("procedures", _rec({"a": 4, "b": 2}, []), _rec({"a": 0}, []), {}, {})
    assert "table `a` is empty (had 4 rows)" in r.regressions
    assert "table `b` is gone (had 2 rows)" in r.regressions


def test_a_check_that_newly_fails_is_a_regression_and_other_moves_are_listed():
    r = impact.diff("airspace", _rec({}, [("A", "pass", 0), ("B", "pass", 0)]),
                    _rec({}, [("A", "fail", 3), ("B", "warn", 1)]), {}, {})
    assert r.regressions == ["check A fails on head (pass on base): 3"]
    assert ("B", "pass", "warn", 1) in r.checks


def test_a_failing_head_build_is_a_regression(tmp_path, monkeypatch):
    calls = []

    def fake(kind, tree, out, work, **kw):
        calls.append(tree)
        return "exit 1: BuildError: boom" if tree.name == "head" else None
    monkeypatch.setattr(impact, "run_builder", fake)
    monkeypatch.setattr(impact, "pack", lambda sq, kind, dest: dest)
    r = impact.run_kind("procedures", tmp_path / "base", tmp_path / "head", tmp_path / "w")
    assert r.regressions == ["the head build fails (the base builds): exit 1: BuildError: boom"]


def test_highways_without_pyefis_is_not_run_not_failed(tmp_path, monkeypatch):
    monkeypatch.delenv("PYEFIS_TOOLS_DIR", raising=False)
    r = impact.run_kind("highways", ROOT, ROOT, tmp_path)
    assert r.not_run and not r.regressions and not r.errors


def test_roads_fixture_covers_every_conus_slug():
    import json
    from packtools.make_roads import CONUS_STATES
    spec = json.loads((impact.FIXTURES / "impact" / "roads_states.json").read_text("utf-8"))
    assert sorted(spec["states"]) == sorted(CONUS_STATES)


# --- end to end: the CIFP builders ------------------------------------------

def test_same_tree_both_sides_reports_no_change(tmp_path):
    rc = impact.main(["--base", str(ROOT), "--head", str(ROOT), "--kinds",
                      "procedures,airspace", "--work", str(tmp_path / "w"),
                      "--out-md", str(tmp_path / "i.md")])
    assert rc == 0
    md = (tmp_path / "i.md").read_text(encoding="utf-8")
    assert md.count("No change: every table") == 2


def test_planted_procedures_regression_is_caught(tmp_path):
    head = _copy_tree(tmp_path / "head")
    # Drop one airport's procedures on the way into the pack.
    _plant(head, "packtools/build/procedures.py",
           "def build_procedures(input_dir: str | Path, out_path: str | Path) -> Path:",
           "def build_procedures(input_dir, out_path):\n"
           "    out = _tv9_unplanted(input_dir, out_path)\n"
           "    con = sqlite3.connect(str(out))\n"
           "    con.execute(\"DELETE FROM procedures WHERE airport = 'KABQ'\")\n"
           "    con.commit(); con.close()\n"
           "    return out\n\n\n"
           "def _tv9_unplanted(input_dir: str | Path, out_path: str | Path) -> Path:")
    rc = impact.main(["--base", str(ROOT), "--head", str(head), "--kinds", "procedures",
                      "--work", str(tmp_path / "w"), "--out-md", str(tmp_path / "i.md")])
    md = (tmp_path / "i.md").read_text(encoding="utf-8")
    assert rc == 1
    assert "**procedures**: airport `KABQ`:" in md and "-> 0 (-100 %)" in md


# --- end to end: highways, the makerplane-data#17 shape ---------------------

# A stand-in for pyEfis tools/build_highway_db.py: same inputs, same table
# shape (schema_version 2), same class filter, no decimation. The real tool
# runs in the PR workflow; this keeps the test free of a pyEfis checkout.
_FAKE_TOOL = '''
import argparse, sqlite3, struct, shapefile
ap = argparse.ArgumentParser()
ap.add_argument("shapefiles", nargs="+"); ap.add_argument("--dest", required=True)
ap.add_argument("--overwrite", action="store_true")
a = ap.parse_args()
keep = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
        "secondary", "secondary_link"}
con = sqlite3.connect(a.dest)
con.executescript("""
CREATE TABLE highway_lines (id INTEGER PRIMARY KEY, fclass TEXT NOT NULL, min_lat REAL,
  max_lat REAL, min_lon REAL, max_lon REAL, verts BLOB NOT NULL,
  flags INTEGER NOT NULL DEFAULT 0, ref TEXT);
CREATE VIRTUAL TABLE highway_rtree USING rtree(id, min_lat, max_lat, min_lon, max_lon);
""")
for p in a.shapefiles:
    sf = shapefile.Reader(p)
    for sr in sf.iterShapeRecords():
        if sr.record["fclass"] not in keep:
            continue
        lons = [x for x, _ in sr.shape.points]; lats = [y for _, y in sr.shape.points]
        box = (min(lats), max(lats), min(lons), max(lons))
        cur = con.execute("INSERT INTO highway_lines (fclass, min_lat, max_lat, min_lon, "
                          "max_lon, verts, ref) VALUES (?,?,?,?,?,?,?)",
                          (sr.record["fclass"], *box, struct.pack("<2f", lats[0], lons[0]),
                           sr.record["ref"]))
        con.execute("INSERT INTO highway_rtree VALUES (?,?,?,?,?)", (cur.lastrowid, *box))
con.commit()
'''


@pytest.fixture
def fake_pyefis(tmp_path, monkeypatch):
    pytest.importorskip("shapefile")
    tools = tmp_path / "pyefis-tools"
    tools.mkdir()
    (tools / "build_highway_db.py").write_text(_FAKE_TOOL, encoding="utf-8")
    monkeypatch.setenv("PYEFIS_TOOLS_DIR", str(tools))
    return tools


def test_highways_same_tree_reports_no_change(tmp_path, fake_pyefis):
    rc = impact.main(["--base", str(ROOT), "--head", str(ROOT), "--kinds", "highways",
                      "--work", str(tmp_path / "w"), "--out-md", str(tmp_path / "i.md"),
                      "--out-json", str(tmp_path / "i.json")])
    assert rc == 0
    import json
    k = json.loads((tmp_path / "i.json").read_text(encoding="utf-8"))["kinds"][0]
    # Every fixture state has roads, and the class filter dropped the
    # residential and track lines the fixture also carries.
    states = k["groups"]["state"]
    assert all(b > 0 for s, (b, h) in states.items() if not s.startswith("("))
    assert "residential" not in k["groups"]["fclass"]


def test_planted_dropped_state_is_caught(tmp_path, fake_pyefis):
    """The DoD's test PR, as a test: a "tidy-up" of the state list drops
    southern California. Nothing fails at build time -- the #17 shape."""
    head = _copy_tree(tmp_path / "head")
    _plant(head, "packtools/make_roads.py",
           '"california/norcal", "california/socal", "colorado",',
           '"california/norcal", "colorado",')
    rc = impact.main(["--base", str(ROOT), "--head", str(head), "--kinds", "highways",
                      "--work", str(tmp_path / "w"), "--out-md", str(tmp_path / "i.md")])
    md = (tmp_path / "i.md").read_text(encoding="utf-8")
    assert rc == 1
    assert "- **highways**: state `california/socal`: 9 -> 0 (-100 %)" in md


def test_runner_refuses_a_tree_it_did_not_import(tmp_path):
    import subprocess
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    p = subprocess.run([sys.executable, "-P", str(impact.RUNNER), "procedures", "--tree",
                        str(tmp_path), "--out", str(tmp_path / "o.sqlite"), "--fixtures",
                        str(impact.FIXTURES), "--work", str(tmp_path)],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 3 and "not from" in p.stderr
