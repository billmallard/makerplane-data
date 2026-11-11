# SPDX-License-Identifier: Apache-2.0
"""reportsite -- the test-report pages on navdata.aerocommons.org (TV5).

The fixture records under tests/fixtures/report_site/reports/ were written by
`packtool report` on seven published packs (2026-10-29), so the pages are
exercised on real records, not hand-made ones."""

import copy
import html
import json
import re
from pathlib import Path

import pytest

from packtools import reportsite as rs
from packtools.report import to_bytes
from packtools.sitedeploy import site_files
from packtools.upload import LocalStore

FIX = Path(__file__).resolve().parent / "fixtures" / "report_site"
REPO_SITE = Path(__file__).resolve().parents[1] / "site"
EDITIONS = {("airports-canada", "2026.06"), ("airports-conus", "2610"),
            ("airports-conus", "2611"), ("navaids-conus", "2610"),
            ("navaids-conus", "2611"), ("procedures-conus", "2610"),
            ("procedures-conus", "2611")}


def _manifest():
    return json.loads((FIX / "manifest.json").read_text())


def _record(pack_id="procedures-conus", cycle="2611"):
    return json.loads((FIX / "reports" / pack_id / cycle / "report.json").read_text())


def _store(tmp_path, records):
    """A store holding ``records`` at their published keys."""
    s = LocalStore(tmp_path / "bucket")
    for rec in records:
        s.put_bytes(rs.record_key(rec["pack"]["id"], rec["pack"]["cycle"]), to_bytes(rec))
    return s


@pytest.fixture(scope="module")
def site():
    return rs.build(rs.load_records(LocalStore(FIX)), _manifest(), commit="a" * 40)


# --- the fixture records render ---------------------------------------------

def test_every_fixture_edition_gets_a_page_a_history_and_an_index_entry(site):
    for pack_id, cycle in EDITIONS:
        assert f"reports/{pack_id}/{cycle}/report.html" in site
        assert f"reports/{pack_id}/index.html" in site
    index = json.loads(site[rs.INDEX_KEY])
    assert index["schema"] == rs.INDEX_SCHEMA
    assert {(i, c) for i, cs in index["packs"].items() for c in cs} == EDITIONS
    assert {"reports/index.html", "reports/check.html"} <= set(site)


def test_index_summary_is_the_records_own_tally(site):
    index = json.loads(site[rs.INDEX_KEY])["packs"]
    for pack_id, cycle in EDITIONS:
        assert index[pack_id][cycle]["summary"] == _record(pack_id, cycle)["summary"]


def test_report_page_numbers_agree_with_the_record(site):
    rec = _record()
    page = site["reports/procedures-conus/2611/report.html"].decode()
    for c in rec["checks"]:
        assert f'<tr data-status="{c["status"]}">' in page
        assert html.escape(c["cannot_catch"]) in page
    assert rec["pack"]["sha256"] in page
    # ROW-002 warns with no build note in this record; the page says so.
    assert "No build note recorded." in page
    assert "This record would stop a publish" in page
    # The airways delta from the plan's sketch, 1,504 -> 1,244.
    assert "-260 (-17.3 %)" in page


def test_every_record_page_carries_the_standing_text_verbatim(site):
    block = html.escape(rs.STANDING_TEXT)
    for key, data in site.items():
        if key.endswith(".html"):
            assert block in data.decode(), key


def test_no_page_carries_the_standing_text_heading(site):
    # Bill, 2026-11-01 (AER-4532): the boxed text stands alone, no heading.
    pages = dict(site, **rs.build([], _manifest()))
    for key, data in pages.items():
        if key.endswith(".html"):
            assert "What this report is and is not" not in data.decode(), key


def test_standing_text_is_the_plan_4_7_block():
    # Pinned so an edit to it is a visible diff, not a quiet drift.
    assert rs.STANDING_TEXT.startswith("These are the checks we ran on this pack,")
    assert rs.STANDING_TEXT.endswith("This data is provided as-is with no warranty "
                                     "and any use of the data is at your own risk. "
                                     "Verify against official publications.")
    assert "No authority has reviewed or approved this data or these checks." in rs.STANDING_TEXT


def test_auspex_absent_says_not_run(site):
    page = site["reports/navaids-conus/2611/report.html"].decode()
    assert "Black-box check (Auspex)</h2><p>Not run for this edition." in page


def test_history_is_ordered_oldest_first_and_charts_every_table(site):
    page = site["reports/procedures-conus/index.html"].decode()
    head = re.search(r"<tr><th>Check</th>(.*?)</tr>", page).group(1)
    assert head.index("2610") < head.index("2611")
    for table in _record()["tables"]:
        assert f'aria-label="{table} rows over 2 editions"' in page


def test_report_links_to_its_previous_edition_page(site):
    page = site["reports/procedures-conus/2611/report.html"].decode()
    assert '<a href="../2610/report.html">2610</a>' in page


def test_listed_state_comes_from_the_manifest(tmp_path):
    m = _manifest()
    m["packs"] = [p for p in m["packs"] if (p["id"], p["cycle"]) != ("navaids-conus", "2610")]
    out = rs.build(rs.load_records(LocalStore(FIX)), m)
    assert "not in the live catalog" in out["reports/navaids-conus/2610/report.html"].decode()
    assert "in the live catalog" in out["reports/navaids-conus/2611/report.html"].decode()


def test_report_sha256_is_compared_with_the_signed_catalog(tmp_path):
    rec = _record()
    store = _store(tmp_path, [rec])
    m = _manifest()
    for p in m["packs"]:
        if (p["id"], p["cycle"]) == ("procedures-conus", "2611"):
            p["report_sha256"] = __import__("hashlib").sha256(to_bytes(rec)).hexdigest()
    page = rs.build(rs.load_records(store), m)["reports/procedures-conus/2611/report.html"]
    assert b"matches the <span class=mono>report_sha256</span>" in page
    for p in m["packs"]:
        p["report_sha256"] = "0" * 64
    page = rs.build(rs.load_records(store), m)["reports/procedures-conus/2611/report.html"]
    assert b"differs from the" in page


def test_rendering_is_deterministic(site):
    again = rs.build(rs.load_records(LocalStore(FIX)), _manifest(), commit="a" * 40)
    assert again == site


# --- banned words -----------------------------------------------------------

@pytest.mark.parametrize("word", rs.BANNED_WORDS)
def test_a_banned_word_in_a_record_fails_the_build(tmp_path, word):
    rec = _record()
    rec["checks"][0]["cannot_catch"] = f"Whether the pack is {word.title()}."
    with pytest.raises(rs.SiteError, match=f"banned word '{word}'"):
        rs.build(rs.load_records(_store(tmp_path, [rec])), None)


def test_standing_text_and_notice_are_exempt_and_nothing_else():
    assert rs.lint(f"<p>{html.escape(rs.STANDING_TEXT)}</p><p>{rs.NOTICE}</p>") == []
    assert rs.lint("<p>Not certified.</p>") == ["banned word 'certified'"]
    # whole words only
    assert rs.lint("<p>unsafe, safety, approvedness</p>") == []


def test_lint_ignores_style_but_not_script():
    assert rs.lint("<style>.safe{}</style>") == []
    assert rs.lint("<script>x = 'safe'</script>") == ["banned word 'safe'"]


def test_site_index_and_every_site_page_pass_the_lint():
    for key, path, _ in site_files(REPO_SITE):
        if key.endswith((".html", ".js")):
            assert rs.lint(path.read_text()) == [], key


# --- hygiene ----------------------------------------------------------------

def test_hygiene_strips_paths_ips_hosts_and_board_links(tmp_path):
    rec = _record()
    rec["checks"][4]["note"] = (
        "No checkout at /home/runner/work/pyEfis or C:\\Users\\bill\\pyEfis; bench "
        "10.110.10.199 (wpballard@pyefis.wks.bcs.local) unreachable, see "
        "http://paperclip:3100/AER/issues/AER-1702 and AER-1702.")
    rec["built"]["run_url"] = "http://10.0.0.5/run/1"
    page = rs.build(rs.load_records(_store(tmp_path, [rec])), None)[
        "reports/procedures-conus/2611/report.html"].decode()
    for leak in ("/home/runner", "C:\\Users", "10.110.10.199", "bcs.local", "wpballard",
                 "paperclip", "10.0.0.5"):
        assert leak not in page
    assert "&lt;local path&gt;" in page and "&lt;host&gt;" in page
    assert "&lt;internal link removed&gt;" in page
    # Board ids stay, as bare text, never as links.
    assert "AER-1702" in page
    assert not re.search(r"<a [^>]*>[^<]*AER-\d+", page)


def test_lint_catches_what_hygiene_missed():
    assert rs.lint("<p>at 192.168.1.20</p>") == ["IP address '192.168.1.20'"]
    assert rs.lint("<p>/tmp/x.pack</p>") == ["local path '/tmp/x.pack'"]
    assert rs.lint("<p>bench.lan</p>") == ["internal hostname 'bench.lan'"]


def test_scrub_leaves_public_text_alone():
    s = ("https://github.com/billmallard/makerplane-data/actions/runs/1 "
         "tests/test_build_procedures.py 1,504 -> 1,244 cycle 2026.06")
    assert rs.scrub(s) == s


def test_only_allowed_hosts_are_linked():
    assert rs.link("https://github.com/x", "x") == '<a href="https://github.com/x">x</a>'
    assert rs.link("https://example.com/x", "x") == "x"
    assert rs.link("http://github.com/x", "x") == "x"


# --- loading ----------------------------------------------------------------

def test_record_whose_path_disagrees_with_its_pack_is_refused(tmp_path):
    rec = _record()
    s = LocalStore(tmp_path / "bucket")
    s.put_bytes(rs.record_key("procedures-conus", "2612"), to_bytes(rec))
    with pytest.raises(rs.SiteError, match="not procedures-conus 2612"):
        rs.load_records(s)


def test_malformed_record_is_refused(tmp_path):
    rec = copy.deepcopy(_record())
    rec["summary"]["pass"] += 1
    with pytest.raises(rs.SiteError, match="summary does not equal"):
        rs.load_records(_store(tmp_path, [rec]))


def test_non_record_keys_and_rejected_are_skipped(tmp_path):
    s = _store(tmp_path, [_record()])
    s.put_bytes("reports/procedures-conus/2611/report.html", b"old page")
    s.put_bytes("reports/procedures-conus/2611/status.json", b"{}")
    s.put_bytes("reports/rejected/procedures-conus/2612/report.json", b"{}")
    eds = rs.load_records(s)
    assert [(x["id"], x["cycle"]) for x in eds] == [("procedures-conus", "2611")]


def test_no_records_still_builds_the_index_and_check_pages():
    out = rs.build([], _manifest())
    assert json.loads(out[rs.INDEX_KEY])["packs"] == {}
    assert b"No pack has a published test record yet." in out["reports/index.html"]
    assert "reports/check.html" in out


# --- publishing -------------------------------------------------------------

def test_publish_writes_changed_pages_only(tmp_path, site):
    s = LocalStore(tmp_path / "bucket")
    rows = rs.publish(s, site, log=lambda *_: None)
    assert {r[2] for r in rows} == {"new"}
    rows = rs.publish(s, site, log=lambda *_: None)
    assert {r[2] for r in rows} == {"unchanged"}


def test_publish_dry_run_writes_nothing(tmp_path, site):
    s = LocalStore(tmp_path / "bucket")
    rs.publish(s, site, dry_run=True, log=lambda *_: None)
    assert s.list_keys("") == []


@pytest.mark.parametrize("key", ["reports/x/1/report.json", "reports/x/1/status.json",
                                 "index.html", "manifest.json", "packs/x.pack"])
def test_publish_refuses_anything_but_pages_and_the_index(tmp_path, key):
    with pytest.raises(rs.SiteError, match="not a generated page"):
        rs.publish(LocalStore(tmp_path), {key: b"x"}, log=lambda *_: None)


def test_local_store_lists_by_prefix(tmp_path):
    s = LocalStore(tmp_path)
    for k in ("reports/a/1/report.json", "reports/b/index.html", "packs/a.pack"):
        s.put_bytes(k, b"x")
    assert s.list_keys("reports/") == ["reports/a/1/report.json", "reports/b/index.html"]


# --- the catalog ------------------------------------------------------------

def test_catalog_shows_the_summary_line_or_says_there_is_no_report():
    page = (REPO_SITE / "index.html").read_text()
    assert "No test report yet for this edition." in page
    assert 'fetch("reports/index.json"' in page
    assert "${s.pass} pass / ${s.warn} warn / ${s.fail} fail / ${s.not_run} not run" in page


def test_every_page_loads_the_analytics_loader_and_nothing_else_does(site):
    assert '<script defer src="assets/analytics.js"></script>' in (REPO_SITE / "index.html").read_text()
    for key, data in site.items():
        if key.endswith(".html"):
            assert b'assets/analytics.js"></script>' in data, key
    assert b"analytics" not in site[rs.INDEX_KEY]


def test_analytics_uses_the_zone_token_and_never_loads_twice():
    js = (REPO_SITE / "assets" / "analytics.js").read_text()
    assert 'var TOKEN = "3522e4f857de4e05b4384a6a5777c4ed";' in js
    # The guard runs before the beacon is created.
    assert js.index('querySelector("script[data-cf-beacon]")') < js.index("createElement")


def test_check_page_shows_attestation_check_and_terrain_gap():
    # TV10: the check page carries the `gh attestation verify` command and
    # says plainly that terrain packs have none, and why.
    page = rs.check_page(commit="")
    assert "gh attestation verify" in page
    assert "--signer-workflow billmallard/makerplane-data/.github/workflows/" in page
    assert "Terrain packs have no attestation" in page
    assert "private repository" in page
    assert rs.lint(page) == []
