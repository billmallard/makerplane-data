"""CyclicalRunner — orchestration logic with injected fakes (no network,
no pyEfis tools)."""

import datetime as dt
import sqlite3
from pathlib import Path

import pytest

from packtools import signing
from packtools.manifest import Manifest
from packtools.run_cyclical import CyclicalRunner
from packtools.sources import SOURCES
from packtools.upload import LocalStore

TODAY = dt.date(2026, 6, 14)
AIRPORTS = SOURCES["airports-conus"]
OBSTACLES = SOURCES["obstacles-conus"]


def fake_fetch(url, work_dir, member=None):
    d = Path(work_dir) / "extracted"
    d.mkdir(parents=True, exist_ok=True)
    (d / "input.csv").write_text("dummy")
    return d


def fake_build(input_dir, out_path):
    con = sqlite3.connect(str(out_path))
    con.execute("CREATE TABLE t (x INTEGER)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()
    return Path(out_path)


def make_runner(tmp_path):
    sk, pub = signing.generate_keypair()
    store = LocalStore(tmp_path / "r2")
    runner = CyclicalRunner(
        store=store, secret=sk, work_dir=tmp_path / "work",
        url_base="https://data.makerplane.org/packs",
        fetcher=fake_fetch,
        builders={"airports": fake_build, "obstacles": fake_build},
        today=TODAY,
    )
    return runner, store, pub


def test_run_builds_current_and_next_for_airac_only_current_for_dof(tmp_path):
    runner, store, pub = make_runner(tmp_path)
    m = runner.run([AIRPORTS, OBSTACLES])

    ids = sorted((p.id, p.cycle) for p in m.packs)
    # airports: current 2606 + next 2607; obstacles: current only (DOF daily).
    assert ("airports-conus", "2606") in ids
    assert ("airports-conus", "2607") in ids
    assert ("obstacles-conus", "260611") in ids
    assert sum(1 for p in m.packs if p.id == "obstacles-conus") == 1

    # packs landed in the store
    assert store.exists("packs/airports-conus-2606.pack")
    assert store.exists("packs/obstacles-conus-260611.pack")


def test_manifest_uploaded_and_signature_verifies(tmp_path):
    runner, store, pub = make_runner(tmp_path)
    runner.run([AIRPORTS, OBSTACLES])

    raw = store.get_bytes("manifest.json")
    sig = store.get_bytes("manifest.json.minisig")
    assert raw and sig
    trusted = signing.verify(raw, sig.decode("ascii"), pub)   # raises if bad
    assert "generated 2026-06-14" == trusted
    # and the manifest's sha256 matches the stored pack bytes
    m = Manifest.from_bytes(raw)
    entry = next(p for p in m.packs if p.cycle == "2606")
    import hashlib
    assert hashlib.sha256(store.get_bytes("packs/airports-conus-2606.pack")).hexdigest() == entry.sha256


def test_idempotent_second_run_builds_nothing(tmp_path):
    runner, store, pub = make_runner(tmp_path)
    runner.run([AIRPORTS, OBSTACLES])

    calls = []
    runner.fetcher = lambda *a, **k: calls.append(a) or fake_fetch(*a, **k)
    runner.run([AIRPORTS, OBSTACLES])
    assert calls == []          # everything already present -> no fetches


def test_dry_run_touches_nothing(tmp_path):
    sk, pub = signing.generate_keypair()
    store = LocalStore(tmp_path / "r2")
    calls = []
    runner = CyclicalRunner(
        store=store, secret=None, work_dir=tmp_path / "work",
        fetcher=lambda *a, **k: calls.append(a),
        builders={"airports": fake_build, "obstacles": fake_build},
        today=TODAY,
    )
    runner.run([AIRPORTS, OBSTACLES], dry_run=True)
    assert calls == []
    assert store.get_bytes("manifest.json") is None     # nothing written


def test_fetch_failure_on_next_is_non_fatal(tmp_path):
    runner, store, pub = make_runner(tmp_path)

    def flaky(url, work_dir, member=None):
        if "2607" in url or "09_Jul_2026" in url:
            raise RuntimeError("next cycle not published yet (simulated 404)")
        return fake_fetch(url, work_dir, member)

    runner.fetcher = flaky
    m = runner.run([AIRPORTS])
    cycles_built = {p.cycle for p in m.packs if p.id == "airports-conus"}
    assert "2606" in cycles_built           # current succeeded
    assert "2607" not in cycles_built       # next failed, but run completed
    assert store.get_bytes("manifest.json") is not None
