# SPDX-License-Identifier: Apache-2.0
"""The in-browser pack inspector (TV6, site/inspect.html).

The JS logic has its own node tests (tests/inspector/). These hold it to the
Python side: a manifest signed by packtools.signing verifies in the JS
verifier and a tampered one does not; the JS key and kind list match the
repo's; and the report pages link findings into the page."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from packtools import reportsite as rs
from packtools import signing
from packtools.sitedeploy import site_files

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "site" / "assets" / "inspect-core.js"
NODE = shutil.which("node")

_VERIFY = """
import * as core from %s;
import { readFileSync } from "node:fs";
const [m, sig, pub] = process.argv.slice(1).map((p) => readFileSync(p));
try {
  const tc = await core.verifyManifest(new Uint8Array(m), sig.toString(), pub.toString(),
                                       globalThis.crypto.subtle);
  console.log(JSON.stringify({ ok: true, tc }));
} catch (e) { console.log(JSON.stringify({ ok: false, err: e.message })); }
"""


def _js_verify(tmp_path, data: bytes, sig: str, pub: str) -> dict:
    (tmp_path / "m").write_bytes(data)
    (tmp_path / "s").write_text(sig)
    (tmp_path / "p").write_text(pub)
    script = _VERIFY % json.dumps(CORE.as_uri())
    out = subprocess.run([NODE, "--input-type=module", "-e", script,
                          str(tmp_path / "m"), str(tmp_path / "s"), str(tmp_path / "p")],
                         capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_python_signature_verifies_in_the_js_verifier(tmp_path):
    sk, pub = signing.generate_keypair()
    data = b'{"manifest_version": 1, "packs": []}\n'
    sig = signing.sign(data, sk, trusted_comment="cycle 2612 published")
    assert _js_verify(tmp_path, data, sig, pub) == {"ok": True, "tc": "cycle 2612 published"}


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_js_verifier_refuses_what_python_refuses(tmp_path):
    sk, pub = signing.generate_keypair()
    other, _ = signing.generate_keypair()
    data = b'{"manifest_version": 1, "packs": []}\n'
    sig = signing.sign(data, sk, trusted_comment="t")
    cases = {
        "tampered body": (data.replace(b"1", b"2"), sig),
        "edited trusted comment": (data, sig.replace("trusted comment: t", "trusted comment: u")),
        "other key": (data, signing.sign(data, other, trusted_comment="t")),
    }
    for name, (d, s) in cases.items():
        with pytest.raises(Exception):
            signing.verify(d, s, pub)
        assert _js_verify(tmp_path, d, s, pub)["ok"] is False, name


def _js_string_list(name: str) -> list[str]:
    m = re.search(rf"export const {name} = \[([^\]]*)\]", CORE.read_text())
    return re.findall(r'"([^"]+)"', m.group(1))


def test_js_kind_list_matches_the_report_site():
    assert sorted(_js_string_list("SQLITE_KINDS")) == sorted(rs.INSPECTOR_KINDS)


def test_js_key_is_the_production_key():
    payload = re.search(r'"(RW[A-Za-z0-9+/=]+)\\n"', CORE.read_text()).group(1)
    assert payload == (ROOT / "keys" / "minisign.pub").read_text().splitlines()[1]


def test_the_page_and_its_module_deploy_with_the_site():
    keys = {k for k, _, _ in site_files(ROOT / "site")}
    assert {"inspect.html", "assets/inspect-core.js"} <= keys


def test_the_page_pins_sql_js_by_hash():
    page = (ROOT / "site" / "inspect.html").read_text()
    assert re.search(r'sql-wasm\.min\.js"\s+integrity="sha512-', page)
    assert re.search(r'WASM_SRI = "sha512-', page)
    assert "PRAGMA query_only = 1" in page


PACK = {"id": "obstacles-conus", "cycle": "261001", "kind": "obstacles", "sha256": "ab" * 32}


def test_inspector_href_pins_the_bytes_and_carries_the_locator():
    href = rs.inspector_href(PACK, {"table": "obstacles", "row_id": 7, "hole": 0,
                                    "lat": 44.0, "lon": -88.5}, "../../../")
    assert href == ("../../../inspect.html?pack=obstacles-conus&cycle=261001&sha256="
                    + "ab" * 32 + "&table=obstacles&row_id=7&lat=44.0&lon=-88.5")


def test_inspector_href_skips_kinds_it_cannot_open_and_odd_values():
    assert rs.inspector_href(dict(PACK, kind="water"), {"table": "water"}, "") is None
    href = rs.inspector_href(PACK, {"table": "a&b", "row_id": True, "lat": [1]}, "")
    assert "table=a%26b" in href and "row_id" not in href and "lat" not in href


def test_report_page_links_findings_and_the_edition_into_the_inspector():
    fix = ROOT / "tests" / "fixtures" / "report_site"
    rec = json.loads((fix / "reports" / "navaids-conus" / "2611" / "report.json").read_text())
    rec["auspex"] = {
        "engine": "Auspex -- proprietary AI test engine; method not published",
        "engine_build": "x", "check": "pack_check", "run_at": "2026-11-01T00:00:00Z",
        "when": "pre-publish", "verdict": "fail",
        "coverage": {"checked": 1, "covered": 1, "skipped": 0, "unreadable": 0},
        "findings": [{"locator": {"table": "fixes", "row_id": 12}, "statement": "s",
                      "expected": "e", "actual": "a", "issue": "#1"}],
        "what_a_pass_means": "The pack does not contradict itself."}
    html = rs.auspex_html(rec, "../../../")
    want = rs.inspector_href(rec["pack"], {"table": "fixes", "row_id": 12}, "../../../")
    assert f'<a href="{want.replace("&", "&amp;")}">open in the inspector</a>' in html
    site = rs.build(rs.load_records(rs_store(fix)), json.loads((fix / "manifest.json").read_text()))
    page = site["reports/navaids-conus/2611/report.html"].decode()
    assert "pack inspector</a>" in page and "inspect.html?pack=navaids-conus&amp;cycle=2611" in page
    assert '<a href="../inspect.html">pack inspector</a>' in site["reports/check.html"].decode()


def rs_store(fix):
    from packtools.upload import LocalStore
    return LocalStore(fix)
