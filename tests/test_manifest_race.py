# SPDX-License-Identifier: Apache-2.0
"""MANIFEST1 (AER-5176): two manifest writers that overlap both keep their
entries. Every writer reads the live manifest, works, then puts the whole
file; before the write was a compare-and-set, the later put silently
dropped whatever the other writer had published in between."""

import threading

import pytest

from packtools import publish as _publish
from packtools import signing
from packtools.manifest import Manifest
from packtools.publish import ManifestConflict, publish, retract
from packtools.run_cyclical import CyclicalRunner
from packtools.schema_guard import NULL_GUARD
from packtools.sources import SOURCES
from packtools.upload import LocalStore, PreconditionFailed, R2Store
from tests.gatefake import STANDIN, standin_reporter
from tests.test_gate import GEN, _pack, quiet
from tests.test_orchestrator import TODAY, fake_build, fake_fetch

OBSTACLES = SOURCES["obstacles-conus"]


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    monkeypatch.setattr(_publish, "BACKOFF_S", 0.001)


class HookedStore(LocalStore):
    """Runs ``hook`` once, the first time a pack file is uploaded: the moment
    a terrain publish holds the manifest it read through a long upload."""

    def __init__(self, root):
        super().__init__(root)
        self.hook = None

    def put_file(self, key, path, content_type=None):
        hook, self.hook = self.hook, None
        super().put_file(key, path, content_type)
        if hook and key.startswith("packs/"):
            hook()


def _runner(store, sk, tmp_path):
    return CyclicalRunner(reporter=standin_reporter, store=store, secret=sk,
                          work_dir=tmp_path / "work", url_base="https://navdata.test/packs",
                          fetcher=fake_fetch, builders={"obstacles": fake_build},
                          guard=NULL_GUARD, today=TODAY)


def _ids(store, pub) -> set:
    raw = store.get_bytes("manifest.json")
    signing.verify(raw, store.get_bytes("manifest.json.minisig").decode(), pub)
    return {(p.id, p.cycle) for p in Manifest.from_bytes(raw).packs}


def test_a_cyclical_run_inside_a_publish_keeps_both(tmp_path):
    # The AER-4595 shape: a late nightly run lands while a terrain-style
    # publish is still uploading its pack.
    store = HookedStore(tmp_path / "r2")
    sk, pub = signing.generate_keypair()
    store.hook = lambda: _runner(store, sk, tmp_path).run([OBSTACLES])
    entry, path = _pack(tmp_path)
    publish(store, sk, [(entry, path)], generated=GEN, sign=signing.sign, gate=STANDIN, **quiet)
    assert _ids(store, pub) == {("highways-conus", "2026q3r1"), ("obstacles-conus", "260611")}


def test_a_publish_inside_a_cyclical_run_keeps_both(tmp_path):
    store = HookedStore(tmp_path / "r2")
    sk, pub = signing.generate_keypair()
    entry, path = _pack(tmp_path)
    store.hook = lambda: publish(store, sk, [(entry, path)], generated=GEN,
                                 sign=signing.sign, gate=STANDIN, **quiet)
    runner = _runner(store, sk, tmp_path)
    runner.log = lambda *a: None
    runner.run([OBSTACLES])
    assert _ids(store, pub) == {("highways-conus", "2026q3r1"), ("obstacles-conus", "260611")}


def test_a_retract_inside_a_publish_is_not_undone(tmp_path):
    store = HookedStore(tmp_path / "r2")
    sk, pub = signing.generate_keypair()
    bad, bad_path = _pack(tmp_path, id="water-conus", kind="water", cycle="2026q3-smoketest")
    publish(store, sk, [(bad, bad_path)], generated=GEN, sign=signing.sign, gate=STANDIN, **quiet)
    store.hook = lambda: retract(store, sk, "water-conus", "2026q3-smoketest",
                                 generated=GEN, sign=signing.sign, **quiet)
    entry, path = _pack(tmp_path)
    publish(store, sk, [(entry, path)], generated=GEN, sign=signing.sign, gate=STANDIN, **quiet)
    assert _ids(store, pub) == {("highways-conus", "2026q3r1")}


def test_writers_racing_on_threads_all_keep_their_entries(tmp_path):
    store = LocalStore(tmp_path / "r2")
    sk, pub = signing.generate_keypair()
    n = 6
    pairs = [_pack(tmp_path / f"w{i}", id=f"highways-r{i}") for i in range(n)]
    go, errors = threading.Barrier(n), []

    def writer(i):
        go.wait()
        try:
            publish(store, sk, [pairs[i]], generated=GEN, sign=signing.sign, gate=STANDIN, **quiet)
        except Exception as e:  # noqa: BLE001 -- reported below
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert _ids(store, pub) == {(f"highways-r{i}", "2026q3r1") for i in range(n)}


def test_another_writer_changing_the_same_pack_is_a_loud_conflict(tmp_path):
    # The gate judged the outer publish against a manifest with no
    # highways-conus; an inner publish of the same id voids that verdict.
    store = HookedStore(tmp_path / "r2")
    sk, pub = signing.generate_keypair()
    inner, inner_path = _pack(tmp_path / "inner", cycle="2026q3r2")
    store.hook = lambda: publish(store, sk, [(inner, inner_path)], generated=GEN,
                                 sign=signing.sign, gate=STANDIN, **quiet)
    outer, outer_path = _pack(tmp_path)
    with pytest.raises(ManifestConflict, match="highways-conus"):
        publish(store, sk, [(outer, outer_path)], generated=GEN, sign=signing.sign,
                gate=STANDIN, **quiet)
    assert _ids(store, pub) == {("highways-conus", "2026q3r2")}


def test_a_manifest_that_never_settles_fails_without_writing(tmp_path):
    class Busy(LocalStore):
        def put_bytes(self, key, data, content_type=None, **cond):
            if cond.get("if_match") or cond.get("if_none_match"):
                raise PreconditionFailed(key)
            super().put_bytes(key, data, content_type)

    store = Busy(tmp_path / "r2")
    sk, _pub = signing.generate_keypair()
    entry, path = _pack(tmp_path)
    with pytest.raises(ManifestConflict, match="every one of"):
        publish(store, sk, [(entry, path)], generated=GEN, sign=signing.sign, gate=STANDIN, **quiet)
    assert store.get_bytes("manifest.json") is None


# --- the store's compare-and-set ----------------------------------------------

def test_local_store_conditional_put(tmp_path):
    s = LocalStore(tmp_path)
    s.put_bytes("k", b"one", if_none_match="*")
    with pytest.raises(PreconditionFailed):
        s.put_bytes("k", b"two", if_none_match="*")
    _raw, etag = s.get_bytes_etag("k")
    s.put_bytes("k", b"two", if_match=etag)
    with pytest.raises(PreconditionFailed):
        s.put_bytes("k", b"three", if_match=etag)
    assert s.get_bytes("k") == b"two"
    assert s.list_keys("") == ["k"]


class FakeS3:
    """The two put_object behaviours that matter: honour the conditional
    headers (412), or ignore them as a non-conforming store would."""

    def __init__(self, honours: bool):
        self.honours, self.objects = honours, {}

    def get_object(self, Bucket, Key):
        import io
        from botocore.exceptions import ClientError
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        body = self.objects[Key]
        return {"Body": io.BytesIO(body), "ETag": f'"{len(body)}"'}

    def put_object(self, Bucket, Key, Body, IfMatch=None, IfNoneMatch=None, **kw):
        from botocore.exceptions import ClientError
        cur = self.objects.get(Key)
        fails = ((IfNoneMatch == "*" and cur is not None)
                 or (IfMatch is not None and (cur is None or IfMatch != f'"{len(cur)}"')))
        if fails and self.honours:
            raise ClientError({"Error": {"Code": "PreconditionFailed"},
                               "ResponseMetadata": {"HTTPStatusCode": 412}}, "PutObject")
        self.objects[Key] = Body


def _r2(honours):
    pytest.importorskip("botocore")
    s = R2Store.__new__(R2Store)
    s.bucket, s._s3 = "b", FakeS3(honours)
    return s


def test_r2_store_refuses_a_store_that_ignores_conditions():
    s = _r2(honours=False)
    with pytest.raises(RuntimeError, match="not enforced"):
        s.put_bytes("manifest.json", b"{}", if_none_match="*")
    assert "manifest.json" not in s._s3.objects


def test_r2_store_conditional_put_maps_412():
    s = _r2(honours=True)
    s.put_bytes("manifest.json", b"{}", if_none_match="*")
    with pytest.raises(PreconditionFailed):
        s.put_bytes("manifest.json", b"{1}", if_match='"999"')
    _raw, etag = s.get_bytes_etag("manifest.json")
    s.put_bytes("manifest.json", b"{1}", if_match=etag)
    assert s._s3.objects["manifest.json"] == b"{1}"
    assert set(s._s3.objects) == {"manifest.json", R2Store.PROBE_KEY}
