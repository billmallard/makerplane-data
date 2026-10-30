# SPDX-License-Identifier: Apache-2.0
"""packtools.summary + `packtool job-summary` -- the check tables the pack
workflows append to the public Actions run page (AER-4098)."""

import sqlite3

from packtools import cli, summary


def test_table_cells_cannot_break_the_markdown():
    md = summary.table(["a", "b"], [["x|y", "line1\nline2"]])
    assert md.splitlines()[2] == r"| x\|y | line1 line2 |"


def test_write_is_a_noop_off_actions(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert summary.write("# hi") is False
    out = tmp_path / "s.md"
    assert summary.write("# hi", out) and summary.write("# again", out)
    assert out.read_text() == "# hi\n\n# again\n\n"           # appends


def test_job_summary_cli_reports_checks_counts_and_missing_files(tmp_path, monkeypatch):
    out = tmp_path / "s.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(out))
    db = tmp_path / "highways.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE highway_lines (x)")
    con.executemany("INSERT INTO highway_lines VALUES (?)", [(i,) for i in range(1234)])
    con.commit()
    con.close()

    rc = cli.main(["job-summary", "--title", "roads-packs: highways-conus 2026q3r1",
                   "--file", str(db), "--file", str(tmp_path / "never-built.pack"),
                   "--input", "states=colorado", "--input", "publish=false",
                   "--check", "schema guard (input)|none|no data contract",
                   "--check", "sign + upload|off|states test-run"])
    assert rc == 0
    text = out.read_text()
    assert "## roads-packs: highways-conus 2026q3r1" in text
    assert "| states | colorado |" in text
    assert "| schema guard (input) | none | no data contract |" in text
    assert "| sign + upload | off | states test-run |" in text
    assert "| highway_lines | 1,234 |" in text
    assert "never-built.pack` -- not produced" in text


def test_job_summary_renders_the_gate_s_records(tmp_path, monkeypatch):
    # TV3: a dispatch build's summary comes from the test record the publish
    # gate wrote, accepted or refused, not from a hand-written check list.
    import pytest
    from packtools import gate, signing
    from packtools.packmeta import PackMeta, embed_sqlite
    from packtools.manifest import PackEntry
    from packtools.publish import publish
    from packtools.upload import LocalStore
    from tests.gatefake import standin_reporter

    out = tmp_path / "s.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(out))
    pack = tmp_path / "highways-conus-r9.pack"
    con = sqlite3.connect(pack)
    con.execute("CREATE TABLE highway_lines (x)")
    con.commit()
    con.close()
    meta = PackMeta(id="highways-conus", kind="highways", cycle="r9", attribution="a", license="l")
    embed_sqlite(pack, meta)
    entry = PackEntry.from_pack(pack, meta, url="https://x/packs/highways-conus-r9.pack")

    def warn(path, **kw):
        rec = standin_reporter(path, **kw)
        row = next(c for c in rec["checks"] if c["id"] == "ROW-002")
        row["status"], row["measured"] = "warn", "largest change: highway_lines -40.0%"
        rec["summary"] = {"pass": len(rec["checks"]) - 1, "warn": 1, "fail": 0, "not_run": 0}
        return rec
    records = tmp_path / "work" / "reports"
    with pytest.raises(gate.GateRefused):
        publish(LocalStore(tmp_path / "r2"), signing.generate_keypair()[0], [(entry, pack)],
                generated="2026-06-14T00:00:00Z", sign=signing.sign, log=lambda *a: None,
                gate=gate.Options(reporter=warn, record_dir=records))

    assert cli.main(["job-summary", "--title", "roads-packs: highways-conus r9",
                     "--records", str(records), "--file", str(pack),
                     "--input", "publish=true"]) == 0
    text = out.read_text()
    assert "### highways-conus r9: REFUSED by the publish gate" in text
    assert "| ROW-002 | warn | no | largest change: highway_lines -40.0% |  |" in text
    assert "- ROW-002 warns with no build note" in text
    assert "| ACC-001 | pass | yes |" in text
    assert "| publish | true |" in text


def test_job_summary_says_when_no_pack_reached_the_gate(tmp_path, monkeypatch):
    out = tmp_path / "s.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(out))
    (tmp_path / "reports").mkdir()
    assert cli.main(["job-summary", "--title", "water-packs: water-conus q9",
                     "--records", str(tmp_path / "reports"),
                     "--file", str(tmp_path / "water.sqlite")]) == 0
    text = out.read_text()
    assert "**No test record:** no pack reached the publish gate" in text
    assert "water.sqlite` -- not produced" in text
