"""pyefis_data end-to-end — the Pi updater against real orchestrator-built,
signed packs served from a local directory (no network, no pyEfis tools).

The build side (CyclicalRunner) produces the exact bytes the Pi consumes, so
these tests exercise the real sign->verify and manifest contract across both
halves of the system."""

import datetime as dt
import sqlite3
from pathlib import Path

import pytest
import nacl.exceptions

from packtools import signing
from packtools.run_cyclical import CyclicalRunner
from packtools.sources import SOURCES
from packtools.upload import LocalStore

from pyefis_data import cli, core
from pyefis_data.config import Config
from pyefis_data.core import Updater, LocalDirRemote, CURRENT, MISSING, UPDATE

TODAY = dt.date(2026, 6, 14)
ORIGIN = "https://test.local"


def _fake_fetch(url, work_dir, member=None):
    d = Path(work_dir) / "x"
    d.mkdir(parents=True, exist_ok=True)
    (d / "f").write_text("x")
    return d


def _fake_build(input_dir, out):
    con = sqlite3.connect(str(out))
    con.execute("CREATE TABLE t (x INTEGER)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()
    return Path(out)


def build_store(tmp_path):
    """Run the real pipeline into a LocalStore; return (store_root, pubkey)."""
    sk, pub = signing.generate_keypair()
    store = LocalStore(tmp_path / "r2")
    CyclicalRunner(
        store=store, secret=sk, work_dir=tmp_path / "wbuild",
        url_base=f"{ORIGIN}/packs", fetcher=_fake_fetch,
        builders={"airports": _fake_build, "obstacles": _fake_build},
        today=TODAY,
    ).run([SOURCES["airports-conus"], SOURCES["obstacles-conus"]])
    return store.root, pub


def make_updater(tmp_path, pub, *, remote_root=None, packs=("airports-conus", "obstacles-conus")):
    cfg = Config(base_url=ORIGIN, root=tmp_path / "pi", packs=packs)
    remote = LocalDirRemote(remote_root) if remote_root else None
    return Updater(cfg, pub, remote=remote, today=TODAY)


class CountingRemote:
    def __init__(self, inner):
        self.inner, self.downloads = inner, 0

    def get_bytes(self, url, timeout=30):
        return self.inner.get_bytes(url)

    def download(self, url, dest):
        self.downloads += 1
        return self.inner.download(url, dest)


def test_selection_navdata_by_kind_bulk_opt_in(tmp_path):
    """Default tracks core navdata kinds; terrain is opt-in by region."""
    import datetime as _dt
    from packtools.manifest import Manifest, PackEntry
    m = Manifest.new("2026-06-14T00:00:00Z")
    m.upsert(PackEntry(id="airports-conus", kind="navdata", cycle="2606",
                       bytes=1, sha256="a"*64, url="u", effective="2026-06-11",
                       expires="2026-07-09"))
    m.upsert(PackEntry(id="obstacles-conus", kind="obstacles", cycle="260611",
                       bytes=1, sha256="a"*64, url="u", effective="2026-06-11",
                       expires="2026-08-06"))
    m.upsert(PackEntry(id="terrain-us-west", kind="terrain", cycle="2024ed",
                       bytes=1, sha256="a"*64, url="u", regions=["us-west"]))

    # Fresh defaults: navdata + obstacles, NOT terrain.
    cfg = Config(base_url=ORIGIN, root=tmp_path / "pi1")
    up = Updater(cfg, "x", today=TODAY)
    assert up._tracked_ids(m) == ["airports-conus", "obstacles-conus"]

    # Opt into the us-west region: terrain joins.
    cfg2 = Config(base_url=ORIGIN, root=tmp_path / "pi2", regions=("us-west",))
    up2 = Updater(cfg2, "x", today=TODAY)
    assert "terrain-us-west" in up2._tracked_ids(m)


def test_status_json_is_rich(tmp_path):
    root, pub = build_store(tmp_path)
    up = make_updater(tmp_path, pub, remote_root=root)
    up.update()
    up2 = make_updater(tmp_path, pub, remote_root=root)
    rows = {r.pack_id: r for r in up2.status()}
    a = rows["airports-conus"]
    assert a.name == "Airports & Runways"
    assert a.kind == "navdata" and a.cycle == "2606"
    assert a.severity == "none"
    d = a.as_dict()
    assert set(d) >= {"id", "name", "kind", "status", "severity", "cycle",
                      "expires", "days", "detail"}


def test_update_refreshes_status_doc(tmp_path):
    """`update` must leave a fresh status doc (this is what flips the EFIS
    boot screen amber->green after a manual Update)."""
    root, pub = build_store(tmp_path)
    up = make_updater(tmp_path, pub, remote_root=root)
    up.update()
    doc = cli._status_doc(up)
    assert doc["ok"] and doc["worst"] == "none"        # all current after install
    assert {p["id"] for p in doc["packs"]} == {"airports-conus", "obstacles-conus"}
    out = tmp_path / "sj.json"
    cli._write_status_json(doc, out)
    import json
    assert json.loads(out.read_text())["packs"][0]["severity"] in ("none", "white", "amber")


def test_status_missing_then_current(tmp_path):
    root, pub = build_store(tmp_path)
    up = make_updater(tmp_path, pub, remote_root=root)
    assert all(r.status == MISSING for r in up.status())
    up.update()
    up2 = make_updater(tmp_path, pub, remote_root=root)   # reloads inventory
    assert all(r.status == CURRENT for r in up2.status())


def test_update_installs_verifies_and_flips_current(tmp_path):
    root, pub = build_store(tmp_path)
    up = make_updater(tmp_path, pub, remote_root=root)
    up.update()
    piroot = tmp_path / "pi"

    # current cycle installed under the canonical filename
    assert (piroot / "navdata" / "2606" / "airports.sqlite").exists()
    assert (piroot / "obstacles" / "260611" / "obstacles.sqlite").exists()
    # current pointer resolves to the right cycle
    assert up._current_target(piroot / "navdata") == "2606"
    # next AIRAC cycle pre-staged (not current)
    assert (piroot / "navdata" / "2607" / "airports.sqlite").exists()
    assert up._current_target(piroot / "navdata") == "2606"
    # inventory reflects it
    assert up.inventory.get("airports-conus")["current"] == "2606"
    assert up.inventory.get("airports-conus")["staged"] == "2607"


def test_installed_sqlite_is_real_and_matches_manifest_sha(tmp_path):
    import hashlib
    root, pub = build_store(tmp_path)
    up = make_updater(tmp_path, pub, remote_root=root)
    up.update()
    f = tmp_path / "pi" / "navdata" / "2606" / "airports.sqlite"
    con = sqlite3.connect(str(f))
    assert con.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    con.close()
    m = up.fetch_manifest()
    e = next(p for p in m.packs if p.cycle == "2606")
    assert hashlib.sha256(f.read_bytes()).hexdigest() == e.sha256


def test_update_is_idempotent(tmp_path):
    root, pub = build_store(tmp_path)
    up = make_updater(tmp_path, pub, remote_root=root)
    up.update()
    counting = CountingRemote(LocalDirRemote(root))
    up.remote = counting
    up.update()
    assert counting.downloads == 0          # nothing re-downloaded


def test_bad_sha_leaves_current_untouched(tmp_path):
    root, pub = build_store(tmp_path)
    # Corrupt the current airports pack bytes (sha no longer matches manifest).
    pack = root / "packs" / "airports-conus-2606.pack"
    pack.write_bytes(pack.read_bytes() + b"tamper")
    up = make_updater(tmp_path, pub, remote_root=root)
    up.update()
    # airports failed verification -> nothing installed, error recorded
    assert up.errors
    assert not (tmp_path / "pi" / "navdata" / "current").exists()
    assert up.inventory.get("airports-conus") is None
    # obstacles still installed fine (independent)
    assert (tmp_path / "pi" / "obstacles" / "260611" / "obstacles.sqlite").exists()


def test_bad_signature_is_refused(tmp_path):
    root, pub = build_store(tmp_path)
    manifest = root / "manifest.json"
    manifest.write_bytes(manifest.read_bytes().replace(b"airports", b"a1rports"))
    up = make_updater(tmp_path, pub, remote_root=root)
    with pytest.raises(nacl.exceptions.BadSignatureError):
        up.update()
    assert not (tmp_path / "pi" / "navdata").exists()    # nothing installed


def test_offline_uses_cached_manifest(tmp_path):
    root, pub = build_store(tmp_path)
    up = make_updater(tmp_path, pub, remote_root=root)
    up.update()                                   # caches a verified manifest

    class Dead:
        def get_bytes(self, url, timeout=30):
            raise ConnectionError("no network")

        def download(self, url, dest):
            raise ConnectionError("no network")

    up.remote = Dead()
    rows = up.status()                            # must fall back to cache
    assert any(r.status == CURRENT for r in rows)


def test_import_dir_via_cli_offline(tmp_path, monkeypatch):
    root, pub = build_store(tmp_path)
    monkeypatch.setattr(cli, "PUBLIC_KEY", pub)   # cli embeds the prod key; sub the test one
    piroot = tmp_path / "pi"
    rc = cli.main(["--base-url", ORIGIN, "--root", str(piroot),
                   "import", str(root)])
    assert rc == 0
    assert (piroot / "navdata" / "2606" / "airports.sqlite").exists()


def test_cli_verify_file(tmp_path, monkeypatch):
    root, pub = build_store(tmp_path)
    monkeypatch.setattr(cli, "PUBLIC_KEY", pub)
    assert cli.main(["verify", str(root / "manifest.json")]) == 0
    # tamper -> non-zero
    (root / "manifest.json").write_bytes(b"{}")
    assert cli.main(["verify", str(root / "manifest.json")]) == 2


def test_catalog_cli_via_source(tmp_path, monkeypatch, capsys):
    """`catalog --json --source <dir>` lists the whole catalog from a USB/local
    dir, with the picker fields and the tracked/installed flags."""
    import json
    root, pub = build_store(tmp_path)
    monkeypatch.setattr(cli, "PUBLIC_KEY", pub)
    capsys.readouterr()                 # discard build_store's progress output
    rc = cli.main(["--base-url", ORIGIN, "--root", str(tmp_path / "pi"),
                   "catalog", "--json", "--source", str(root)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"]
    assert {p["id"] for p in out["packs"]} == {"airports-conus", "obstacles-conus"}
    a = next(p for p in out["packs"] if p["id"] == "airports-conus")
    assert a["bytes"] > 0 and a["tracked"] is True and a["installed"] is False


def test_update_only_persists_and_installs(tmp_path, monkeypatch):
    """`update --only <id> --source <dir>` installs exactly the selection and
    writes it to data.yaml (core kinds emptied so the resolved set == the
    selection), so the next auto-update tracks the same set."""
    root, pub = build_store(tmp_path)
    monkeypatch.setattr(cli, "PUBLIC_KEY", pub)
    piroot = tmp_path / "pi"
    cfgpath = tmp_path / "data.yaml"
    rc = cli.main(["--config", str(cfgpath), "--base-url", ORIGIN, "--root", str(piroot),
                   "update", "--only", "airports-conus", "--source", str(root)])
    assert rc == 0
    # exactly the selection installed; obstacles (a default-tracked kind) skipped
    assert (piroot / "navdata" / "2606" / "airports.sqlite").exists()
    assert not (piroot / "obstacles").exists()
    # selection persisted as an explicit packs list
    saved = Config.load(cfgpath)
    assert saved.packs == ("airports-conus",)
    assert saved.track_kinds == () and saved.regions == ()


def test_prune_removes_old_cycles(tmp_path):
    root, pub = build_store(tmp_path)
    up = make_updater(tmp_path, pub, remote_root=root)
    base = tmp_path / "pi" / "navdata"
    for cyc in ("2604", "2605", "2606"):
        (base / cyc).mkdir(parents=True, exist_ok=True)
        (base / cyc / "airports.sqlite").write_text("x")
    up._flip_current(base, "2606")
    up.inventory.set_current("airports-conus", "2606", "x", kind="navdata")
    up.inventory.set_staged("airports-conus", "2607", "x")
    (base / "2607").mkdir(exist_ok=True)
    up._prune("airports-conus", "navdata")
    remaining = sorted(p.name for p in base.iterdir() if p.is_dir())
    assert "2604" not in remaining and "2605" not in remaining
    assert "2606" in remaining and "2607" in remaining
