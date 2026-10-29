# SPDX-License-Identifier: Apache-2.0
"""sitedeploy -- upload site/ to the bucket root, changed files only, and
never onto a key the pack pipeline owns (AER-4098)."""

from pathlib import Path

import pytest

from packtools.publish import MANIFEST_KEY
from packtools.sitedeploy import deploy, site_files
from packtools.upload import LocalStore

REPO_SITE = Path(__file__).resolve().parents[1] / "site"


class RecordingStore(LocalStore):
    """LocalStore that also records each write's content type."""

    def __init__(self, root):
        super().__init__(root)
        self.writes = {}

    def put_bytes(self, key, data, content_type=None):
        self.writes[key] = content_type
        super().put_bytes(key, data, content_type)


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    (root / "assets").mkdir(parents=True)
    # Bytes, not text: the comparison is byte-exact, and write_text would add
    # CRs on Windows.
    (root / "index.html").write_bytes(b"<p>new</p>\n")
    (root / "data.yaml.sample").write_bytes(b"packs: []\n")
    (root / "assets" / "logo.svg").write_bytes(b"<svg/>\n")
    return root


@pytest.fixture
def store(tmp_path):
    s = RecordingStore(tmp_path / "r2")
    s.put_bytes("index.html", b"<p>old</p>\n")
    s.put_bytes("data.yaml.sample", b"packs: []\n")
    s.put_bytes(MANIFEST_KEY, b'{"signed": true}')
    s.writes.clear()
    return s


def test_uploads_changed_and_new_files_with_their_content_types(site, store):
    rows = deploy(store, site, log=lambda *_: None)
    assert {r[0]: r[2] for r in rows} == {
        "assets/logo.svg": "new",
        "data.yaml.sample": "unchanged",
        "index.html": "changed",
    }
    assert store.writes == {"assets/logo.svg": "image/svg+xml",
                            "index.html": "text/html; charset=utf-8"}
    assert store.get_bytes("index.html") == b"<p>new</p>\n"
    assert store.get_bytes(MANIFEST_KEY) == b'{"signed": true}'


def test_dry_run_reports_the_same_and_writes_nothing(site, store):
    rows = deploy(store, site, dry_run=True, log=lambda *_: None)
    assert [r[2] for r in rows] == ["new", "unchanged", "changed"]
    assert store.writes == {}
    assert store.get_bytes("index.html") == b"<p>old</p>\n"


@pytest.mark.parametrize("key", [MANIFEST_KEY, "packs/airports-conus-2610.pack"])
def test_refuses_a_pack_pipeline_key_before_writing_anything(site, store, key):
    (site / key).parent.mkdir(parents=True, exist_ok=True)
    (site / key).write_text("not signed data")
    with pytest.raises(ValueError, match="reserved"):
        deploy(store, site, log=lambda *_: None)
    assert store.writes == {}


def test_refuses_an_unknown_file_type(site, store):
    (site / "notes.txt").write_text("x")
    with pytest.raises(ValueError, match="no content type"):
        deploy(store, site, log=lambda *_: None)
    assert store.writes == {}


def test_the_repo_site_tree_is_deployable():
    # A new file type under site/ fails here, in CI, not in the deploy job.
    keys = [k for k, _, _ in site_files(REPO_SITE)]
    assert "index.html" in keys and "data.yaml.sample" in keys
