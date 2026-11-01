# SPDX-License-Identifier: Apache-2.0
"""TV4: retro records for editions published before the gate.

The live manifest here is written the way the pre-TV3 pipeline wrote it:
signed, with no report fields. The records are built by the real
``report.build_report`` (device probe off), so the binding and the site
rendering run on records the catalogue really produces."""

import json
import sqlite3
import zipfile

import pytest

from packtools import gate, packschema, report, retro, signing
from packtools import reportsite as rs
from packtools.manifest import NOTICE, Manifest, ManifestError, PackEntry
from packtools.packmeta import PackMeta, embed_sqlite, embed_zip
from packtools.upload import LocalStore

ORIGIN = "https://navdata.test"
quiet = dict(log=lambda *a: None)


def _pack(tmp_path, id, cycle, rows, effective):
    p = tmp_path / "build" / f"{id}-{cycle}.pack"
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE obstacles (oas TEXT,  -- FAA obstacle id\n lat REAL, lon REAL)")
    con.execute("CREATE INDEX obstacles_oas ON obstacles (oas)")
    con.execute("CREATE VIRTUAL TABLE obstacles_idx USING rtree(id, minlat, maxlat, minlon, maxlon)")
    con.executemany("INSERT INTO obstacles VALUES (?, ?, ?)",
                    [(f"{i:06d}", 39.0 + i * 1e-3, -105.0) for i in range(rows)])
    con.commit()
    con.close()
    meta = PackMeta(id=id, kind="obstacles", cycle=cycle, effective=effective,
                    attribution="FAA DOF", license="public domain")
    embed_sqlite(p, meta)
    return PackEntry.from_pack(p, meta, url=f"{ORIGIN}/packs/{p.name}", notice=""), p


@pytest.fixture
def live(tmp_path):
    """A pre-TV3 live manifest: two obstacle editions, no records."""
    store = LocalStore(tmp_path / "r2")
    sk, pub = signing.generate_keypair()
    m = Manifest.new("2026-10-01T00:00:00Z")
    for cyc, rows, eff in (("260806", 40, "2026-08-06"), ("261001", 38, "2026-10-01")):
        entry, path = _pack(tmp_path, "obstacles-conus", cyc, rows, eff)
        store.put_file(f"packs/{path.name}", path)
        m.upsert(entry)
    raw = m.to_bytes()
    store.put_bytes(gate.MANIFEST_KEY, raw)
    store.put_bytes(gate.SIG_KEY, signing.sign(raw, sk, trusted_comment="pre-TV3").encode())
    return store, sk, pub


def _run(store, sk, tmp_path, **kw):
    return retro.run(store, sk, scratch=tmp_path / "scratch", probe=False, **quiet, **kw)


def _signed(store, pub) -> Manifest:
    raw = store.get_bytes(gate.MANIFEST_KEY)
    signing.verify(raw, store.get_bytes(gate.SIG_KEY).decode(), pub)
    return Manifest.from_bytes(raw)


def test_every_unbound_edition_gets_a_bound_retro_record(live, tmp_path):
    store, sk, pub = live
    results = _run(store, sk, tmp_path)
    assert [r.outcome for r in results] == ["bound", "bound"]
    m = _signed(store, pub)
    for p in m.packs:
        raw = store.get_bytes(gate.report_key(p.id, p.cycle))
        assert p.report_url == f"{ORIGIN}/reports/{p.id}/{p.cycle}/report.json"
        assert p.report_sha256 == gate._sha256_bytes(raw)
        assert p.notice == NOTICE and p.origin == "federal"
        assert p.build_commit == ""          # not known, so not claimed
        rec = json.loads(raw)
        assert rec["record"] == "retro" and not report.validate_report(rec)
        assert (rec["pack"]["sha256"], rec["pack"]["bytes"]) == (p.sha256, p.bytes)
        assert rec["built"]["at"] is None and rec["built"]["commit"] is None
        assert rec["retro"]["written_at"]
        src = next(c for c in rec["checks"] if c["id"] == "PACK-004")
        assert src["status"] == "not-run" and src["note"] == retro.SOURCES_NOTE
    # The scratch copy of each pack is gone.
    assert not any((tmp_path / "scratch").iterdir())


def test_newer_edition_compares_against_the_older_retro_record(live, tmp_path):
    store, sk, pub = live
    results = _run(store, sk, tmp_path)
    assert results[0].previous == "no earlier edition is listed"
    assert results[1].previous.startswith("previous edition 260806")
    old, new = (json.loads(store.get_bytes(gate.report_key("obstacles-conus", c)))
                for c in ("260806", "261001"))
    assert new["previous"] == {"cycle": "260806", "sha256": old["pack"]["sha256"]}
    assert new["tables"]["obstacles"] == {"rows": 38, "prev": 40}
    # The oldest has nothing to compare with, and says why in retro terms.
    delta = [c for c in old["checks"] if c.get("not_run_reason") == "no-previous"]
    assert delta and all(c["note"].startswith("Retro record: no earlier edition is listed")
                         for c in delta)


def test_rerun_finds_nothing_to_do_and_rebinding_is_idempotent(live, tmp_path):
    store, sk, pub = live
    _run(store, sk, tmp_path)
    before = store.get_bytes(gate.MANIFEST_KEY)
    assert _run(store, sk, tmp_path) == []
    assert store.get_bytes(gate.MANIFEST_KEY) == before
    m = _signed(store, pub)
    p = m.packs[0]
    raw = store.get_bytes(gate.report_key(p.id, p.cycle))
    assert retro.bind(store, sk, p, raw, generated="x", sign=signing.sign, **quiet) == "already bound"


def test_a_retro_record_never_replaces_a_binding(live, tmp_path):
    store, sk, pub = live
    _run(store, sk, tmp_path)
    p = _signed(store, pub).packs[0]
    rec = json.loads(store.get_bytes(gate.report_key(p.id, p.cycle)))
    rec["retro"]["written_at"] = "2027-01-01T00:00:00Z"
    with pytest.raises(retro.RetroError, match="never replaces"):
        retro.bind(store, sk, p, report.to_bytes(rec), generated="x", sign=signing.sign, **quiet)


def test_a_build_record_cannot_be_bound_through_retro(live, tmp_path):
    store, sk, pub = live
    p = _signed(store, pub).packs[0]
    path = tmp_path / "p.pack"
    store.get_file(f"packs/{p.url.rsplit('/', 1)[1]}", path)
    rec = report.build_report(path, record="build", probe=False)
    with pytest.raises(ManifestError, match="not retro"):
        retro.bind(store, sk, p, report.to_bytes(rec), generated="x", sign=signing.sign, **quiet)
    # Refused before upload: no unbound record is left for the site to list.
    assert store.get_bytes(gate.report_key(p.id, p.cycle)) is None


def test_a_pack_that_does_not_match_the_signed_manifest_gets_nothing(live, tmp_path):
    store, sk, pub = live
    m = _signed(store, pub)
    key = f"packs/{m.packs[0].url.rsplit('/', 1)[1]}"
    path = tmp_path / "tampered.pack"
    store.get_file(key, path)
    path.write_bytes(path.read_bytes() + b"x")
    store.put_file(key, path)
    results = _run(store, sk, tmp_path)
    assert results[0].outcome == "error" and "does not match" in results[0].error
    assert results[1].outcome == "bound"     # the run carries on to the next pack
    m = _signed(store, pub)
    assert m.packs[0].report_url == "" and m.packs[1].report_url
    assert store.get_bytes(gate.report_key(m.packs[0].id, m.packs[0].cycle)) is None


def test_dry_run_writes_records_locally_and_nothing_to_the_store(live, tmp_path):
    store, sk, pub = live
    before = store.get_bytes(gate.MANIFEST_KEY)
    out = tmp_path / "out"
    results = _run(store, None, tmp_path, dry_run=True, out_dir=out)
    assert [r.outcome for r in results] == ["written (dry run)"] * 2
    assert store.get_bytes(gate.MANIFEST_KEY) == before
    assert store.list_keys("reports/") == [] and store.list_keys("schema/") == []
    assert (out / "obstacles-conus-261001.retro.json").exists()
    assert json.loads((out / "retro-summary.json").read_text())[1]["id"] == "obstacles-conus"
    with pytest.raises(retro.RetroError, match="signing key"):
        _run(store, None, tmp_path)


def test_failing_checks_are_reported_not_acted_on(live, tmp_path):
    store, sk, pub = live
    real = report.build_report

    def failing_reporter(path, **kw):
        rec = real(path, **kw)
        c = next(c for c in rec["checks"] if c["id"] == "PACK-002")
        c["status"], c["measured"] = "fail", "missing: license"
        rec["summary"] = {"pass": rec["summary"]["pass"] - 1, "warn": rec["summary"]["warn"],
                          "fail": rec["summary"]["fail"] + 1,
                          "not_run": rec["summary"]["not_run"]}
        return rec
    results = _run(store, sk, tmp_path, reporter=failing_reporter)
    # Published as found: still bound, the pack untouched and still listed.
    assert [r.outcome for r in results] == ["bound", "bound"]
    assert {"id": "PACK-002", "blocking": True,
            "measured": "missing: license"} in results[0].failing
    md = retro.summary_markdown(results)
    assert "obstacles-conus 260806 PACK-002 (blocking): missing: license" in md
    assert len(_signed(store, pub).packs) == 2


def test_schema_is_written_from_the_newest_edition(live, tmp_path):
    store, sk, pub = live
    _run(store, sk, tmp_path)
    sch = json.loads(store.get_bytes(packschema.key_for("obstacles")))
    assert sch["schema"] == packschema.SCHEMA and sch["from"] == {"id": "obstacles-conus",
                                                                   "cycle": "261001"}
    t = {x["name"]: x for x in sch["tables"]}
    # DDL verbatim, comment included; R-tree shadow tables folded under it.
    assert "oas TEXT,  -- FAA obstacle id\n lat REAL" in t["obstacles"]["sql"]
    assert [i["name"] for i in t["obstacles"]["indexes"]] == ["obstacles_oas"]
    assert t["obstacles_idx"]["virtual"] and "obstacles_idx_node" in t["obstacles_idx"]["shadow_tables"]
    assert "obstacles_idx_node" not in t
    assert "license" in sch["pack_meta_keys"] and "pack_meta" in t


def test_schema_of_a_zip_pack_is_its_member_layout(tmp_path):
    p = tmp_path / "terrain-x-1.pack"
    with zipfile.ZipFile(p, "w") as z:
        for n in ("N39W105.hgt", "N40W105.hgt", ".mip/L1/N39W105.hgt"):
            z.writestr(n, b"\0" * 8)
    embed_zip(p, PackMeta(id="terrain-x", kind="terrain", cycle="1"))
    sch = packschema.capture(p)
    assert sch["format"] == "zip" and sch["members"] == 4
    assert {"pattern": "N#W#.hgt", "count": 2} in sch["layout"]
    assert {"pattern": ".mip/L#/N#W#.hgt", "count": 1} in sch["layout"]


def test_retro_page_says_what_today_s_gate_would_refuse(live, tmp_path):
    store, sk, pub = live
    _run(store, sk, tmp_path)
    site = rs.build(rs.load_records(store), json.loads(store.get_bytes(gate.MANIFEST_KEY)),
                    commit="a" * 40)
    page = site["reports/obstacles-conus/260806/report.html"].decode()
    assert "written after the edition was published" in page
    assert "Judged by today&#x27;s publish gate as a new build" in page
    assert "record is retro, not build" not in page
    assert not rs.lint(page)


def test_a_binding_dropped_by_a_concurrent_publish_is_reported(live, tmp_path, monkeypatch):
    store, sk, pub = live
    pre = (store.get_bytes(gate.MANIFEST_KEY), store.get_bytes(gate.SIG_KEY))
    real_bind = retro.bind

    def bind_then_clobbered(store_, *a, **kw):
        out = real_bind(store_, *a, **kw)
        # A publish that loaded the manifest before this bind signs after it.
        store_.put_bytes(gate.MANIFEST_KEY, pre[0])
        store_.put_bytes(gate.SIG_KEY, pre[1])
        return out
    monkeypatch.setattr(retro, "bind", bind_then_clobbered)
    results = _run(store, sk, tmp_path, only=["obstacles-conus"])
    assert all(r.outcome == "error" and "re-run" in r.error for r in results)
