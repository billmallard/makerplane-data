# SPDX-License-Identifier: Apache-2.0
"""The publish gate (TV3, AER-4101): every pack gets its test record before
the manifest is signed; refused builds get a ``rejected`` record and publish
nothing; first edition comes only from the signed live manifest; build
notes come only from the caller; records are immutable once bound."""

import hashlib
import json
import sqlite3

import pytest

from packtools import gate, report, signing
from packtools.manifest import Manifest, ManifestError, PackEntry
from packtools.packmeta import PackMeta, embed_sqlite
from packtools.publish import publish, retract, sign_and_put
from packtools.upload import LocalStore
from tests.gatefake import STANDIN, standin_reporter

ORIGIN = "https://navdata.test"
GEN = "2026-06-14T00:00:00Z"
quiet = dict(log=lambda *a: None)


def _pack(tmp_path, id="highways-conus", kind="highways", cycle="2026q3r1", rows=1, **dates):
    p = tmp_path / "build" / f"{id}-{cycle}.pack"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.unlink(missing_ok=True)
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE t (x INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    con.commit()
    con.close()
    meta = PackMeta(id=id, kind=kind, cycle=cycle, attribution="test", license="test", **dates)
    embed_sqlite(p, meta)
    return PackEntry.from_pack(p, meta, url=f"{ORIGIN}/packs/{p.name}"), p


def _store(tmp_path):
    return LocalStore(tmp_path / "r2"), signing.generate_keypair()


def _live(store) -> Manifest:
    return Manifest.from_bytes(store.get_bytes("manifest.json"))


class Recording:
    """Wraps a reporter and remembers what the gate asked it for."""

    def __init__(self, inner=standin_reporter, mutate=None):
        self.inner, self.mutate, self.calls = inner, mutate, []

    def __call__(self, path, **kw):
        self.calls.append(kw)
        rec = self.inner(path, **kw)
        if self.mutate:
            self.mutate(rec)
        return rec


def _warn(check_id):
    def m(rec):
        for c in rec["checks"]:
            if c["id"] == check_id:
                c["status"], c["measured"] = "warn", "largest change: t -20.0% (10 -> 8)"
        rec["summary"] = {"pass": len(rec["checks"]) - 1, "warn": 1, "fail": 0, "not_run": 0}
    return m


# --- binding the record to the manifest --------------------------------------

def test_accepted_pack_publishes_its_record_bound_by_the_signature(tmp_path):
    store, (sk, pub) = _store(tmp_path)
    entry, path = _pack(tmp_path)
    publish(store, sk, [(entry, path)], generated=GEN, sign=signing.sign, gate=STANDIN, **quiet)

    raw = store.get_bytes("manifest.json")
    signing.verify(raw, store.get_bytes("manifest.json.minisig").decode(), pub)
    e = _live(store).packs[0]
    key = "reports/highways-conus/2026q3r1/report.json"
    body = store.get_bytes(key)
    assert e.report_url == f"{ORIGIN}/{key}"
    assert e.report_sha256 == hashlib.sha256(body).hexdigest()
    assert e.origin == "community" and e.build_commit == "c" * 40
    rec = json.loads(body)
    assert rec["record"] == "build" and rec["pack"]["sha256"] == e.sha256
    assert store.exists("packs/highways-conus-2026q3r1.pack")


def test_record_for_other_bytes_is_refused(tmp_path):
    # The gate checks the record against the file being signed, so a record
    # about any other bytes cannot clear it (AER-4102 closing verdict).
    store, (sk, _) = _store(tmp_path)
    entry, path = _pack(tmp_path)

    def other_bytes(rec):
        rec["pack"]["sha256"] = "0" * 64
    rep = Recording(mutate=other_bytes)
    with pytest.raises(gate.GateRefused, match="does not describe the bytes being signed"):
        publish(store, sk, [(entry, path)], generated=GEN, sign=signing.sign,
                gate=gate.Options(reporter=rep), **quiet)
    assert store.get_bytes("manifest.json") is None
    assert not store.exists("packs/highways-conus-2026q3r1.pack")


def test_entry_that_does_not_match_its_file_is_an_error(tmp_path):
    store, (sk, _) = _store(tmp_path)
    entry, path = _pack(tmp_path)
    entry.sha256 = "a" * 64
    with pytest.raises(gate.GateError, match="does not match"):
        publish(store, sk, [(entry, path)], generated=GEN, sign=signing.sign,
                gate=STANDIN, **quiet)
    assert store.list_keys("") == []


# --- refusal -------------------------------------------------------------------

def test_real_catalogue_refuses_and_publishes_a_rejected_record(tmp_path, monkeypatch):
    # The real reporter, no pyEfis checkout: ACC-001 (blocking) cannot run, so
    # the gate refuses. The refusal is published under reports/rejected/ and
    # nothing else is written.
    for v in ("PYEFIS_SRC_DIR", "PYEFIS_TOOLS_DIR"):
        monkeypatch.delenv(v, raising=False)
    store, (sk, _) = _store(tmp_path)
    entry, path = _pack(tmp_path)
    with pytest.raises(gate.GateRefused, match="ACC-001 is blocking and did not run") as ei:
        publish(store, sk, [(entry, path)], generated=GEN, sign=signing.sign,
                gate=gate.Options(record_dir=tmp_path / "records"), **quiet)
    (g,) = ei.value.refused
    assert g.key.startswith("reports/rejected/highways-conus/2026q3r1/")
    assert g.key.endswith(f"-{entry.sha256[:12]}/report.json")
    rec = json.loads(store.get_bytes(g.key))
    assert rec["record"] == "rejected"
    assert report.validate_report(rec) == []
    assert rec["pack"]["sha256"] == entry.sha256
    assert store.list_keys("") == [g.key]                 # no pack, no manifest
    local = tmp_path / "records"
    assert (local / "highways-conus-2026q3r1.rejected.json").read_bytes() == store.get_bytes(g.key)
    assert json.loads((local / "highways-conus-2026q3r1.gate.json").read_text())["accepted"] is False


def test_multi_pack_publish_is_all_or_nothing(tmp_path):
    store, (sk, _) = _store(tmp_path)
    good = _pack(tmp_path / "a", id="terrain-alaska", kind="terrain", cycle="e1")
    bad = _pack(tmp_path / "b", id="terrain-canada-west", kind="terrain", cycle="e1")

    def fail_one(rec):
        if rec["pack"]["id"] == "terrain-canada-west":
            c = next(c for c in rec["checks"] if c["id"] == "TER-002")
            c["status"], c["measured"] = "fail", "3 native tiles outside canada-west"
    with pytest.raises(gate.GateRefused, match="terrain-canada-west"):
        publish(store, sk, [good, bad], generated=GEN, sign=signing.sign,
                gate=gate.Options(reporter=Recording(mutate=fail_one), origin="community"),
                **quiet)
    keys = store.list_keys("")
    assert len(keys) == 1 and keys[0].startswith("reports/rejected/terrain-canada-west/")


def test_rejected_records_never_overwrite_each_other(tmp_path):
    rec = {"pack": {"id": "x", "cycle": "1", "sha256": "ab" * 32}, "built": {"at": "2026-06-14T00:00:00Z"}}
    later = dict(rec, built={"at": "2026-06-15T00:00:00Z"})
    assert gate.rejected_key(rec) != gate.rejected_key(later)
    assert gate.rejected_key(rec) == "reports/rejected/x/1/20260614T000000Z-abababababab/report.json"


# --- build notes (AER-4102 C8) ----------------------------------------------------

def test_warn_without_a_note_refuses_and_a_person_s_note_clears_it(tmp_path):
    store, (sk, _) = _store(tmp_path)
    entry, path = _pack(tmp_path)
    with pytest.raises(gate.GateRefused, match="ROW-002 warns with no build note"):
        publish(store, sk, [(entry, path)], generated=GEN, sign=signing.sign,
                gate=gate.Options(reporter=Recording(mutate=_warn("ROW-002"))), **quiet)
    publish(store, sk, [(entry, path)], generated=GEN, sign=signing.sign,
            gate=gate.Options(reporter=Recording(mutate=_warn("ROW-002")),
                              notes={"ROW-002": "Geofabrik split Texas; checked the counts"}),
            **quiet)
    rec = json.loads(store.get_bytes("reports/highways-conus/2026q3r1/report.json"))
    row = next(c for c in rec["checks"] if c["id"] == "ROW-002")
    assert row["note"] == "Geofabrik split Texas; checked the counts"


def test_note_for_an_unknown_check_is_refused(tmp_path):
    store, (sk, _) = _store(tmp_path)
    entry, path = _pack(tmp_path)
    with pytest.raises(gate.GateError, match="ROW-02 is not a check"):
        publish(store, sk, [(entry, path)], generated=GEN, sign=signing.sign,
                gate=gate.Options(reporter=standin_reporter, notes={"ROW-02": "typo"}), **quiet)


def test_scoped_note_applies_to_its_pack_only():
    notes = {"terrain-alaska:TER-004": "glacier voids", "TER-002": "every region"}
    assert gate.notes_for("terrain-alaska", "terrain", notes, is_zip=True) == {
        "TER-004": "glacier voids", "TER-002": "every region"}
    assert gate.notes_for("terrain-canada-west", "terrain", notes, is_zip=True) == {
        "TER-002": "every region"}


# --- first and previous edition, from the signed manifest ---------------------------

def test_first_edition_only_when_the_signed_manifest_lists_no_edition(tmp_path):
    store, (sk, _) = _store(tmp_path)
    rep = Recording()
    e1 = _pack(tmp_path / "1", cycle="r1")
    publish(store, sk, [e1], generated=GEN, sign=signing.sign,
            gate=gate.Options(reporter=rep), **quiet)
    assert rep.calls[-1]["first_edition"] is True and rep.calls[-1]["previous"] is None

    e2 = _pack(tmp_path / "2", cycle="r2")
    publish(store, sk, [e2], generated=GEN, sign=signing.sign,
            gate=gate.Options(reporter=rep), **quiet)
    assert rep.calls[-1]["first_edition"] is False
    # r1's bound record, read back and hash-checked against report_sha256.
    assert rep.calls[-1]["previous"]["pack"]["cycle"] == "r1"


def test_listed_edition_that_cannot_be_read_blocks_instead_of_first_edition(tmp_path):
    # r1 is listed but its record and pack are gone: that is not a first
    # edition, so the delta checks get no previous and (real catalogue) block.
    store, (sk, _) = _store(tmp_path)
    publish(store, sk, [_pack(tmp_path / "1", cycle="r1")], generated=GEN, sign=signing.sign,
            gate=STANDIN, **quiet)
    for k in store.list_keys(""):
        if not k.startswith("manifest"):
            (store.root / k).unlink()
    prev, first, how = gate.previous_record(store, _live(store),
                                            _pack(tmp_path / "2", cycle="r2")[0], **quiet)
    assert (prev, first) == (None, False)
    assert "not in the store" in how


def test_tampered_previous_record_falls_back_to_the_pack(tmp_path):
    store, (sk, _) = _store(tmp_path)
    publish(store, sk, [_pack(tmp_path / "1", cycle="r1", rows=10)], generated=GEN,
            sign=signing.sign, gate=STANDIN, **quiet)
    store.put_bytes("reports/highways-conus/r1/report.json", b'{"forged": true}')
    prev, first, how = gate.previous_record(store, _live(store),
                                            _pack(tmp_path / "2", cycle="r2")[0], **quiet)
    assert first is False and "reported on now from its published pack" in how
    assert prev["pack"]["cycle"] == "r1" and prev["tables"]["t"]["rows"] == 10


def test_previous_ranks_below_the_new_edition(tmp_path):
    live = Manifest.new(GEN)
    for c in ("2606", "2607"):
        live.upsert(PackEntry(id="airports-conus", kind="navdata", cycle=c, bytes=1,
                              sha256="a" * 64, url=f"{ORIGIN}/packs/airports-conus-{c}.pack",
                              effective=f"2026-{c[2:]}-01"))
    new = PackEntry(id="airports-conus", kind="navdata", cycle="2608", bytes=1, sha256="b" * 64,
                    url=f"{ORIGIN}/packs/airports-conus-2608.pack", effective="2026-08-01")
    rec = {"pack": {"cycle": "2607"}}
    local = {("airports-conus", "2607"): (live.packs[1], rec)}
    prev, first, _ = gate.previous_record(None, live, new, local=local, **quiet)
    assert prev is rec and first is False


# --- immutability and the signed live manifest ----------------------------------------

def test_already_published_cycle_is_refused(tmp_path):
    store, (sk, _) = _store(tmp_path)
    entry, path = _pack(tmp_path)
    publish(store, sk, [(entry, path)], generated=GEN, sign=signing.sign, gate=STANDIN, **quiet)
    before = store.get_bytes("reports/highways-conus/2026q3r1/report.json")
    entry2, path2 = _pack(tmp_path / "again", rows=2)
    with pytest.raises(gate.GateError, match="already in the signed manifest"):
        publish(store, sk, [(entry2, path2)], generated=GEN, sign=signing.sign,
                gate=STANDIN, **quiet)
    assert store.get_bytes("reports/highways-conus/2026q3r1/report.json") == before


def test_signing_refuses_a_new_entry_without_a_record(tmp_path):
    store, (sk, _) = _store(tmp_path)
    entry, _path = _pack(tmp_path)
    m = Manifest.new(GEN)
    m.upsert(entry)
    with pytest.raises(ManifestError, match="no report_url"):
        sign_and_put(store, sk, m, previous={"packs": []}, sign=signing.sign, comment="x")
    assert store.get_bytes("manifest.json") is None


@pytest.mark.parametrize("damage", ["no-sig", "bad-sig"])
def test_unverifiable_live_manifest_stops_everything(tmp_path, damage):
    store, (sk, _) = _store(tmp_path)
    publish(store, sk, [_pack(tmp_path)], generated=GEN, sign=signing.sign, gate=STANDIN, **quiet)
    if damage == "no-sig":
        (store.root / "manifest.json.minisig").unlink()
    else:
        other, _ = signing.generate_keypair()
        store.put_bytes("manifest.json.minisig",
                        signing.sign(store.get_bytes("manifest.json"), other).encode())
    before = sorted(store.list_keys(""))
    with pytest.raises(gate.GateError):
        publish(store, sk, [_pack(tmp_path / "2", cycle="r2")], generated=GEN,
                sign=signing.sign, gate=STANDIN, **quiet)
    with pytest.raises(gate.GateError):
        retract(store, sk, "highways-conus", "2026q3r1", generated=GEN, sign=signing.sign,
                **quiet)
    assert sorted(store.list_keys("")) == before


def test_retract_keeps_records_and_bindings(tmp_path):
    store, (sk, _) = _store(tmp_path)
    publish(store, sk, [_pack(tmp_path / "1", cycle="r1"), _pack(tmp_path / "2", cycle="r2")],
            generated=GEN, sign=signing.sign, gate=STANDIN, **quiet)
    bound = {p.cycle: p.report_sha256 for p in _live(store).packs}
    retract(store, sk, "highways-conus", "r1", generated=GEN, sign=signing.sign, **quiet)
    assert {p.cycle: p.report_sha256 for p in _live(store).packs} == {"r2": bound["r2"]}
    assert store.exists("reports/highways-conus/r1/report.json")   # never deleted
