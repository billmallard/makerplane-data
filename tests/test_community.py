# SPDX-License-Identifier: Apache-2.0
"""community -- the GitHub community loop (TV8): the status overlay, the
issue-form parsing and pack:<id> labelling, and the Release assets.

The records are the real fixture records under tests/fixtures/report_site/
(written by `packtool report` on seven published packs); the manifest is
that fixture's, signed here with a throwaway key so the overlay's
verify-before-trust path runs for real."""

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from packtools import community as cm
from packtools import reportsite as rs
from packtools import signing
from packtools.cli import main as cli_main
from packtools.report import to_bytes
from packtools.upload import LocalStore

FIX = Path(__file__).resolve().parent / "fixtures" / "report_site"
DAY = dt.date(2026, 10, 30)          # 2611 is current, 2610 has expired


def _manifest():
    return json.loads((FIX / "manifest.json").read_text())


def _record(pack_id="procedures-conus", cycle="2611"):
    return json.loads((FIX / "reports" / pack_id / cycle / "report.json").read_text())


def _signed_store(tmp_path, manifest=None, records=None):
    """A store with the fixture records and ``manifest`` signed by a fresh
    key; returns ``(store, public key text)``."""
    s = LocalStore(tmp_path / "bucket")
    for key in LocalStore(FIX).list_keys("reports/"):
        if records is None:
            s.put_bytes(key, LocalStore(FIX).get_bytes(key))
    for rec in records or []:
        s.put_bytes(rs.record_key(rec["pack"]["id"], rec["pack"]["cycle"]), to_bytes(rec))
    sk, pub = signing.generate_keypair()
    raw = (json.dumps(manifest or _manifest(), indent=2, sort_keys=True) + "\n").encode()
    s.put_bytes("manifest.json", raw)
    s.put_bytes("manifest.json.minisig", signing.sign(raw, sk).encode())
    return s, pub


def _issue(number, labels, *, state="open", body="", title="t"):
    return {"number": number, "title": title, "state": state, "body": body,
            "html_url": f"https://github.com/billmallard/makerplane-data/issues/{number}",
            "created_at": "2026-10-30T12:00:00Z",
            "closed_at": "2026-10-31T09:00:00Z" if state == "closed" else None,
            "labels": [{"name": n} for n in labels]}


def _overlays(store, pub, issues=(), today=DAY):
    m = cm.load_live_manifest(store, pub)
    out = cm.build_status(store, rs.load_records(store), m, list(issues), today)
    return {k: json.loads(v) for k, v in out.items()}


# --- the signed catalog is checked before anything is decided from it -------

def test_a_manifest_that_does_not_verify_writes_no_overlay(tmp_path):
    s, pub = _signed_store(tmp_path)
    _, other = signing.generate_keypair()
    with pytest.raises(cm.CommunityError, match="does not verify"):
        cm.load_live_manifest(s, other)
    s.put_bytes("manifest.json", s.get_bytes("manifest.json") + b" ")
    with pytest.raises(cm.CommunityError, match="does not verify"):
        cm.load_live_manifest(s, pub)


def test_the_committed_key_is_the_default(tmp_path):
    s, _ = _signed_store(tmp_path)
    assert cm.PUB_KEY.is_file()
    with pytest.raises(cm.CommunityError):   # signed by a throwaway, not the real key
        cm.load_live_manifest(s)


# --- state ---------------------------------------------------------------------

def test_every_record_gets_one_overlay_beside_it(tmp_path):
    s, pub = _signed_store(tmp_path)
    ov = _overlays(s, pub)
    keys = {k for k in LocalStore(FIX).list_keys("reports/") if k.endswith("report.json")}
    assert set(ov) == {k.replace("report.json", "status.json") for k in keys}
    o = ov["reports/procedures-conus/2611/status.json"]
    assert o["schema"] == cm.STATUS_SCHEMA and o["signed"] is False
    assert o["as_of"] == "2026-10-30"
    raw = LocalStore(FIX).get_bytes("reports/procedures-conus/2611/report.json")
    assert o["record"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert o["pack"]["sha256"] == _record()["pack"]["sha256"]


def test_listed_editions_follow_the_window_and_the_updaters_choice(tmp_path):
    s, pub = _signed_store(tmp_path)
    ov = _overlays(s, pub)
    st = {k.split("/", 1)[1].rsplit("/", 1)[0]: v["state"] for k, v in ov.items()}
    assert st["procedures-conus/2611"] == "current"
    assert st["procedures-conus/2610"] == "expired"
    assert st["airports-canada/2026.06"] == "current"     # no dates: always covered
    early = _overlays(s, pub, today=dt.date(2026, 10, 20))
    assert early["reports/procedures-conus/2611/status.json"]["state"] == "pending"
    assert early["reports/procedures-conus/2610/status.json"]["state"] == "current"


def test_an_undated_edition_the_updater_passes_over_is_superseded(tmp_path):
    m = _manifest()
    m["packs"] = [p for p in m["packs"] if p["id"] == "water-na"]
    new = _record("airports-canada", "2026.06")
    old = copy.deepcopy(new)
    for rec, cyc in ((old, "2026q2r5"), (new, "2026q2r6")):
        rec["pack"].update(id="water-na", kind="water", cycle=cyc)
    s, pub = _signed_store(tmp_path, m, [old, new])
    ov = _overlays(s, pub)
    assert ov["reports/water-na/2026q2r6/status.json"]["state"] == "current"
    o = ov["reports/water-na/2026q2r5/status.json"]
    assert o["state"] == "superseded" and "2026q2r6" in o["state_reason"]


def test_an_unlisted_edition_newer_than_everything_listed_is_retracted(tmp_path):
    """A newer edition pulled from the catalog: nothing listed is newer by
    cycle or by build time, so it is retracted, not superseded."""
    m = _manifest()
    m["packs"] = [p for p in m["packs"] if (p["id"], p["cycle"]) != ("procedures-conus", "2611")]
    r10, r11 = _record("procedures-conus", "2610"), _record("procedures-conus", "2611")
    r10["built"]["at"], r11["built"]["at"] = "2026-10-01T00:00:00Z", "2026-10-28T00:00:00Z"
    s, pub = _signed_store(tmp_path, m, [r10, r11])
    o = _overlays(s, pub)["reports/procedures-conus/2611/status.json"]
    assert (o["state"], o["listed"]) == ("retracted", False)


def test_a_pruned_edition_is_superseded_by_a_higher_cycle_whatever_the_build_time(tmp_path):
    """prune_old_cycles dropped 2610 when a newer cycle arrived. The newer
    one being listed is enough, even with no record or an earlier stamp."""
    m = _manifest()
    m["packs"] = [p for p in m["packs"] if (p["id"], p["cycle"]) != ("procedures-conus", "2610")]
    r10, r11 = _record("procedures-conus", "2610"), _record("procedures-conus", "2611")
    r10["built"]["at"], r11["built"]["at"] = "2026-10-28T00:00:00Z", "2026-10-01T00:00:00Z"
    s, pub = _signed_store(tmp_path, m, [r10, r11])
    o = _overlays(s, pub)["reports/procedures-conus/2610/status.json"]
    assert o["state"] == "superseded" and "2611" in o["state_reason"]
    s2, pub2 = _signed_store(tmp_path / "b", m, [r10])
    assert _overlays(s2, pub2)["reports/procedures-conus/2610/status.json"]["state"] \
        == "superseded"


def test_a_test_cycle_is_compared_by_build_time_only(tmp_path):
    """The AER-623 shape: a -smoketest edition retracted from the catalog.
    It sorts above the canonical cycle, so only build time can rank it."""
    m = _manifest()
    m["packs"] = [p for p in m["packs"] if p["id"] == "water-na"]
    smoke = _record("airports-canada", "2026.06")
    smoke["pack"].update(id="water-na", kind="water", cycle="2026q2r6-smoketest")
    s, pub = _signed_store(tmp_path, m, [smoke])
    o = _overlays(s, pub)["reports/water-na/2026q2r6-smoketest/status.json"]
    assert o["state"] == "retracted"


def test_an_older_listed_edition_never_supersedes_a_newer_one(tmp_path):
    m = _manifest()
    m["packs"] = [p for p in m["packs"] if (p["id"], p["cycle"]) != ("procedures-conus", "2611")]
    s, pub = _signed_store(tmp_path, m, [_record("procedures-conus", "2611")])
    assert _overlays(s, pub)["reports/procedures-conus/2611/status.json"]["state"] == "retracted"


# --- known issues ------------------------------------------------------------------

def test_known_issues_come_from_pack_labels_with_their_state(tmp_path):
    s, pub = _signed_store(tmp_path)
    body = (f"### {cm.PACK_FIELD}\n\nprocedures-conus\n\n### {cm.CYCLE_FIELD}\n\n2610\n\n"
            "### What is wrong\n\nV4 is missing")
    issues = [_issue(17, ["pack:procedures-conus", "data-problem"], body=body),
              _issue(9, ["pack:procedures-conus"], state="closed",
                     title="leaked /home/bill/x path"),
              _issue(20, ["pack:navaids-conus"]), _issue(21, ["bug"])]
    ov = _overlays(s, pub, issues)
    k = ov["reports/procedures-conus/2611/status.json"]["known_issues"]
    assert [i["number"] for i in k] == [9, 17]
    assert k[0]["state"] == "closed" and k[0]["closed"] == "2026-10-31"
    assert "/home/" not in k[0]["title"]                 # the hygiene scrub ran
    assert k[1]["reported_cycle"] == "2610"
    assert [i["number"] for i in ov["reports/navaids-conus/2611/status.json"]["known_issues"]] == [20]
    assert ov["reports/airports-conus/2611/status.json"]["known_issues"] == []


def test_a_failed_issues_page_raises_rather_than_listing_none(monkeypatch):
    monkeypatch.setattr(cm, "_gh", lambda *a, **k: (502, None, ""))
    with pytest.raises(cm.CommunityError, match="no overlay written"):
        cm.fetch_pack_issues()


def test_issue_paging_follows_next_and_drops_pull_requests(monkeypatch):
    pages = {"/repos/billmallard/makerplane-data/issues?state=all&per_page=100":
             ([_issue(1, ["pack:a"]), dict(_issue(2, ["pack:a"]), pull_request={})],
              '<https://api.github.com/p2>; rel="next"'),
             "https://api.github.com/p2": ([_issue(3, ["pack:b"]), _issue(4, ["bug"])], "")}
    monkeypatch.setattr(cm, "_gh", lambda m, url, **k: (200, *pages[url]))
    assert [i["number"] for i in cm.fetch_pack_issues()] == [1, 3]


# --- Auspex addenda: listed by name and hash, never read into ---------------------

def test_addenda_are_listed_with_sha256(tmp_path):
    s, pub = _signed_store(tmp_path)
    blob = b'{"anything": "QA wrote"}\n'
    s.put_bytes("reports/procedures-conus/2611/auspex-2026-10-31.json", blob)
    s.put_bytes("reports/procedures-conus/2611/notes.json", b"{}")
    a = _overlays(s, pub)["reports/procedures-conus/2611/status.json"]["auspex_addenda"]
    assert a == [{"name": "auspex-2026-10-31.json", "date": "2026-10-31",
                  "url": "https://navdata.aerocommons.org/reports/procedures-conus/2611/"
                         "auspex-2026-10-31.json",
                  "sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob)}]


# --- writing ------------------------------------------------------------------------

def test_the_writer_refuses_anything_but_a_status_overlay(tmp_path):
    s = LocalStore(tmp_path)
    for bad in ("reports/a/1/report.json", "reports/a/1/auspex-2026-10-31.json",
                "manifest.json", "reports/rejected/a/1/status.json", "reports/a/status.json"):
        with pytest.raises(cm.CommunityError, match="not a status overlay"):
            cm.publish_status(s, {bad: b"{}"})
    assert s.list_keys("") == []


def test_overlays_rewrite_only_what_changed_and_dry_run_writes_nothing(tmp_path):
    s, pub = _signed_store(tmp_path)
    objs = cm.build_status(s, rs.load_records(s), cm.load_live_manifest(s, pub), [], DAY)
    assert {r[2] for r in cm.publish_status(s, objs, dry_run=True)} == {"new"}
    assert not any(k.endswith("status.json") for k in s.list_keys("reports/"))
    cm.publish_status(s, objs)
    assert {r[2] for r in cm.publish_status(s, objs)} == {"unchanged"}


def test_cli_status_overlay_end_to_end(tmp_path):
    s, pub = _signed_store(tmp_path)
    (tmp_path / "pub").write_text(pub)
    (tmp_path / "issues.json").write_text(json.dumps([_issue(5, ["pack:airports-conus"])]))
    rc = cli_main(["status-overlay", "--from", str(tmp_path / "bucket"),
                   "--pub", str(tmp_path / "pub"), "--issues", str(tmp_path / "issues.json"),
                   "--date", "2026-10-30"])
    assert rc == 0
    o = json.loads(s.get_bytes("reports/airports-conus/2611/status.json"))
    assert o["state"] == "current" and o["known_issues"][0]["number"] == 5


# --- issue forms ----------------------------------------------------------------------

FORMS = Path(__file__).resolve().parents[1] / ".github" / "ISSUE_TEMPLATE"


def test_form_fields_parse_like_github_renders_them():
    body = (f"### {cm.PACK_FIELD}\n\nAirports-CONUS, navaids-conus\n../evil, pack:x\n\n"
            f"### {cm.CYCLE_FIELD}\n\n_No response_\n\n### Location\n\nKOSH")
    assert cm.pack_ids_in(body) == ["airports-conus", "navaids-conus"]
    assert cm.form_field(body, cm.CYCLE_FIELD) is None
    assert cm.form_field(body, "Location") == "KOSH"
    assert cm.form_field("no form here", cm.PACK_FIELD) is None


def test_both_forms_carry_the_fields_the_parser_reads():
    import yaml
    for name, label in (("data_problem.yml", "data-problem"),
                        ("tested_on_hardware.yml", "tested-on-hardware")):
        form = yaml.safe_load((FORMS / name).read_text())
        assert form["labels"] == [label] and label in cm.FORM_LABELS
        fields = {b["id"]: b["attributes"]["label"] for b in form["body"] if "id" in b}
        assert fields["pack"] == cm.PACK_FIELD
    dp = yaml.safe_load((FORMS / "data_problem.yml").read_text())
    assert {b["id"]: b["attributes"]["label"] for b in dp["body"] if "id" in b}["cycle"] \
        == cm.CYCLE_FIELD


def test_label_issue_adds_only_labels_that_exist(monkeypatch):
    calls = []

    def gh(method, path, **k):
        calls.append((method, path, k.get("body")))
        if method == "GET":
            return (200 if path.endswith("pack%3Aairports-conus") else 404), None, ""
        return 200, [], ""
    monkeypatch.setattr(cm, "_gh", gh)
    body = f"### {cm.PACK_FIELD}\n\nairports-conus typo-pack\n"
    assert cm.label_issue(body, 42, log=lambda *a: None) == ["pack:airports-conus"]
    assert calls[-1] == ("POST", "/repos/billmallard/makerplane-data/issues/42/labels",
                         {"labels": ["pack:airports-conus"]})
    assert not any(m == "POST" and p.endswith("/labels") and "issues" not in p
                   for m, p, _ in calls)      # never creates a label


def test_ensure_labels_creates_only_missing_and_dry_run_creates_none(monkeypatch):
    posts = []

    def gh(method, path, **k):
        if method == "GET":
            return (200 if "airports" in path else 404), None, ""
        posts.append(k["body"]["name"])
        return 201, None, ""
    monkeypatch.setattr(cm, "_gh", gh)
    forms = sorted(cm.FORM_LABELS)
    assert cm.ensure_labels(["airports-conus", "water-na"], dry_run=True,
                            log=lambda *a: None) == sorted(forms + ["pack:water-na"])
    assert posts == []
    cm.ensure_labels(["airports-conus", "water-na"], log=lambda *a: None)
    assert posts == sorted(forms + ["pack:water-na"])


# --- Release assets -------------------------------------------------------------------

def _gate_dir(tmp_path, recs, accepted=True):
    d = tmp_path / "reports"
    d.mkdir(parents=True, exist_ok=True)
    for rec in recs:
        stem = f"{rec['pack']['id']}-{rec['pack']['cycle']}"
        (d / f"{stem}.{'report' if accepted else 'rejected'}.json").write_bytes(to_bytes(rec))
        (d / f"{stem}.gate.json").write_text(json.dumps({"accepted": accepted, "blockers": []}))
    return d


def _bound(manifest, rec):
    for p in manifest["packs"]:
        if (p["id"], p["cycle"]) == (rec["pack"]["id"], rec["pack"]["cycle"]):
            p["report_sha256"] = hashlib.sha256(to_bytes(rec)).hexdigest()
    return manifest


def test_release_holds_each_bound_record_byte_for_byte(tmp_path):
    rec = _record()
    d = _gate_dir(tmp_path, [rec])
    out = tmp_path / "release"
    done = cm.release_assets(d, _bound(_manifest(), rec), out, today=DAY, run_id="123",
                             run_attempt="1", run_url="https://github.com/x/y/actions/runs/123",
                             commit="b" * 40, log=lambda *a: None)
    assert [(x["id"], x["cycle"]) for x in done] == [("procedures-conus", "2611")]
    assert (out / "assets" / "procedures-conus-2611.report.json").read_bytes() == to_bytes(rec)
    page = (out / "assets" / "procedures-conus-2611.report.html").read_text()
    assert '<base href="https://navdata.aerocommons.org/reports/procedures-conus/2611/">' in page
    assert rs.lint(page) == []
    assert (out / "tag.txt").read_text().strip() == "data/2026-10-30/123.1"
    assert "procedures-conus 2611" in (out / "title.txt").read_text()
    notes = (out / "notes.md").read_text()
    assert "actions/runs/123" in notes and "`bbbbbbbbbbbb`" in notes and rs.lint(notes) == []


def test_no_release_when_nothing_reached_the_catalog(tmp_path):
    rec = _record()
    out = tmp_path / "release"
    # Accepted by the gate, but the catalog's report_sha256 is not its bytes.
    assert cm.release_assets(_gate_dir(tmp_path, [rec]), _manifest(), out, today=DAY,
                             log=lambda *a: None) == []
    # Refused by the gate.
    assert cm.release_assets(_gate_dir(tmp_path / "r", [rec], accepted=False),
                             _bound(_manifest(), rec), out, today=DAY,
                             log=lambda *a: None) == []
    assert not out.exists()


def test_release_leaves_out_an_edition_that_did_not_reach_the_catalog(tmp_path):
    a, b = _record(), _record("navaids-conus", "2611")
    lines = []
    done = cm.release_assets(_gate_dir(tmp_path, [a, b]), _bound(_manifest(), a),
                             tmp_path / "out", today=DAY, log=lines.append)
    assert [x["id"] for x in done] == ["procedures-conus"]
    assert any("left out navaids-conus 2611" in ln for ln in lines)
    assert not (tmp_path / "out" / "assets" / "navaids-conus-2611.report.json").exists()


# --- the report page's known-issues section ---------------------------------------

def test_the_report_page_reads_the_overlay_and_links_the_form():
    site = rs.build(rs.load_records(LocalStore(FIX)), _manifest(), commit="a" * 40)
    page = site["reports/procedures-conus/2611/report.html"].decode()
    assert 'fetch("status.json"' in page
    assert ("issues/new?template=data_problem.yml&amp;pack=procedures-conus&amp;cycle=2611"
            in page)
    assert "label%3A%22pack%3Aprocedures-conus%22" in page
