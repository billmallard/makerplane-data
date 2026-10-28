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
