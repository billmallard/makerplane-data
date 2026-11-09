# SPDX-License-Identifier: Apache-2.0
"""CERT5a (AER-5181): a pack built by a pyEfis script names the pyEfis
commit it ran, in its own pack_meta and in its pack-report/1 record.

The workflows check pyEfis out at a branch (``PYEFIS_REF``), so without this
nothing records which commit built a published pack."""

import datetime as dt
import sqlite3
import subprocess
from pathlib import Path

import pytest

from packtools import build as _build
from packtools import cli, packmeta, report, signing
from packtools.build.errors import BuildError
from packtools.run_cyclical import CyclicalRunner
from packtools.schema_guard import NULL_GUARD
from packtools.sources import SOURCES
from packtools.upload import LocalStore
from tests.gatefake import standin_reporter

TODAY = dt.date(2026, 6, 14)
OBSTACLES = SOURCES["obstacles-conus"]

# Stands in for pyEfis tools/build_obstacle_db.py: same command line, one row.
_FAKE_TOOL = '''
import argparse, sqlite3
p = argparse.ArgumentParser()
p.add_argument("--dof-dir")
p.add_argument("--output")
a = p.parse_args()
con = sqlite3.connect(a.output)
con.execute("CREATE TABLE obstacles (oas TEXT)")
con.execute("INSERT INTO obstacles VALUES ('01-000001')")
con.commit()
con.close()
'''


def _git(d, *args):
    return subprocess.run(["git", "-C", str(d), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def pyefis_checkout(tmp_path, monkeypatch):
    """A git checkout shaped like pyEfis, with PYEFIS_TOOLS_DIR on its tools/."""
    root = tmp_path / "pyefis-src"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / "build_obstacle_db.py").write_text(_FAKE_TOOL)
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.org",
         "commit", "-q", "-m", "fixture")
    monkeypatch.setenv("PYEFIS_TOOLS_DIR", str(root / "tools"))
    return _git(root, "rev-parse", "HEAD")


def _fetch(url, work_dir, member=None):
    d = Path(work_dir) / "extracted"
    d.mkdir(parents=True, exist_ok=True)
    (d / "DOF.DAT").write_text("dummy")
    return d


def _fake_build(input_dir, out_path):
    con = sqlite3.connect(str(out_path))
    con.execute("CREATE TABLE obstacles (oas TEXT)")
    con.commit()
    con.close()
    return Path(out_path)


def _run(tmp_path, builder):
    sk, _pub = signing.generate_keypair()
    store = LocalStore(tmp_path / "r2")
    calls = []

    def reporter(path, **kw):
        calls.append(kw)
        return standin_reporter(path, **kw)

    runner = CyclicalRunner(reporter=reporter, store=store, secret=sk,
                            work_dir=tmp_path / "work", fetcher=_fetch,
                            builders={"obstacles": builder}, guard=NULL_GUARD, today=TODAY)
    runner.run([OBSTACLES])
    pack = tmp_path / "work" / "obstacles-conus" / "260611" / "obstacles-conus-260611.pack"
    assert store.exists("packs/obstacles-conus-260611.pack")
    return pack


def _meta_keys(pack):
    con = sqlite3.connect(str(pack))
    try:
        return {k for (k,) in con.execute("SELECT key FROM pack_meta")}
    finally:
        con.close()


def test_cyclical_pack_records_the_pyefis_commit_it_was_built_from(tmp_path, pyefis_checkout):
    pack = _run(tmp_path, _build.build_obstacles)
    assert packmeta.read(pack).pyefis_commit == pyefis_checkout


def test_pack_built_by_no_pyefis_code_carries_no_pyefis_commit(tmp_path):
    pack = _run(tmp_path, _fake_build)
    assert packmeta.read(pack).pyefis_commit is None
    assert "pyefis_commit" not in _meta_keys(pack)


def test_pyefis_commit_refuses_a_tools_dir_outside_git(tmp_path, monkeypatch):
    (tmp_path / "tools").mkdir()
    monkeypatch.setenv("PYEFIS_TOOLS_DIR", str(tmp_path / "tools"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    with pytest.raises(BuildError, match="not in a git checkout"):
        _build.pyefis_commit()


def _pack_with(tmp_path, sha):
    p = tmp_path / "obstacles.pack"
    _fake_build(None, p)
    packmeta.embed_sqlite(p, packmeta.PackMeta(id="obstacles-conus", kind="obstacles",
                                               cycle="260611", pyefis_commit=sha))
    return p


def test_record_builder_refs_name_the_pack_meta_pyefis_commit(tmp_path):
    sha = "a" * 40
    rec = report.build_report(_pack_with(tmp_path, sha), probe=False, first_edition=True)
    assert rec["built"]["builder_refs"] == {"pyEfis": sha}


def test_record_refuses_builder_refs_that_contradict_pack_meta(tmp_path):
    with pytest.raises(report.ReportError, match="contradicts"):
        report.build_report(_pack_with(tmp_path, "a" * 40), probe=False, first_edition=True,
                            builder_refs={"pyEfis": "b" * 40})


def _build_pack(tmp_path, *extra):
    keys = tmp_path / "keys"
    cli.main(["genkey", "--out", str(keys)])
    src = tmp_path / "highways.sqlite"
    _fake_build(None, src)
    return cli.main(["build-pack", str(src), "--id", "water-conus", "--kind", "water",
                     "--cycle", "2026q2", "--regions", "conus", *extra,
                     "--sec", str(keys / "minisign.sec"), "--out", str(tmp_path / "work")])


def test_build_pack_builder_ref_pyefis_lands_in_pack_meta(tmp_path):
    sha = "c" * 40
    assert _build_pack(tmp_path, "--builder-ref", f"pyEfis={sha}") == 0
    meta = packmeta.read(tmp_path / "work" / "packs" / "water-conus-2026q2.pack")
    assert meta.pyefis_commit == sha


@pytest.mark.parametrize("bad", ["", "dev", "c" * 39])
def test_build_pack_refuses_a_pyefis_ref_that_is_not_a_full_sha(tmp_path, bad):
    with pytest.raises(SystemExit, match="40-hex"):
        _build_pack(tmp_path, "--builder-ref", f"pyEfis={bad}")
