# SPDX-License-Identifier: Apache-2.0
"""CERT10b: every catalogue check points at the DO-200B data quality
attributes it protects, and docs/data_quality_requirements.md shows the
same per-kind matrix the code computes."""

from pathlib import Path

from packtools import cli, report

DOC = Path(__file__).resolve().parent.parent / "docs" / "data_quality_requirements.md"


def test_every_check_protects_a_known_attribute():
    for kind, checks in report.all_checks().items():
        for c in checks:
            assert c.protects, f"{kind} {c.id} names no attribute in report.PROTECTS"
            unknown = set(c.protects) - set(report.DQ_ATTRIBUTES)
            assert not unknown, f"{c.id}: {sorted(unknown)} not in DQ_ATTRIBUTES"


def test_protects_names_no_check_that_does_not_exist():
    ids = {c.id for checks in report.all_checks().values() for c in checks}
    assert set(report.PROTECTS) <= ids, sorted(set(report.PROTECTS) - ids)


def _doc_matrix() -> dict[str, dict[str, list[str]]]:
    lines = DOC.read_text(encoding="utf-8").splitlines()
    head = "| Kind | " + " | ".join(a.capitalize() for a in report.DQ_ATTRIBUTES) + " |"
    i = lines.index(head) + 2
    out = {}
    while i < len(lines) and lines[i].startswith("| "):
        cells = [c.strip() for c in lines[i].strip("|").split("|")]
        out[cells[0]] = {a: [] if v == "none" else [x.strip().strip("`") for x in v.split(",")]
                         for a, v in zip(report.DQ_ATTRIBUTES, cells[1:])}
        i += 1
    return out


def test_doc_matrix_matches_the_catalogue():
    assert _doc_matrix() == report.dq_matrix()


def test_list_checks_prints_what_each_check_protects(capsys):
    assert cli.main(["report", "--list-checks", "--kind", "terrain"]) == 0
    out = capsys.readouterr().out
    assert "protects:     format" in out
    assert out.count("protects:") == len(report.catalogue("terrain", is_zip=True))
