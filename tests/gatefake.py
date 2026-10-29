# SPDX-License-Identifier: Apache-2.0
"""A stand-in for ``report.build_report``, for tests whose packs are fakes.

Tests of the updater, the terrain and water installs and the orchestrator
publish one-row placeholder packs that the real catalogue would rightly
refuse. They inject this reporter instead (``gate.Options(reporter=...)``,
``CyclicalRunner(reporter=...)``). It reports every catalogue check as a
pass, but it describes the real file (its id, kind, cycle, sha256 and size
come from the bytes), so the gate's binding check and
``report.publish_blockers`` still run on it unchanged. The gate itself is
tested against the real reporter in tests/test_gate.py.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from packtools import packmeta, report
from packtools.gate import Options
from packtools.manifest import NOTICE
from packtools.signing import sha256_file


def standin_reporter(path, *, record="build", previous=None, origin=None, sources=(),
                     notes=None, **_):
    path = Path(path)
    meta = packmeta.read(path)
    checks = [{"id": c.id, "title": c.title, "status": "pass", "blocking": c.blocking,
               "measured": "stand-in", "expected": c.expected, "cannot_catch": c.cannot_catch}
              for c in report.catalogue(meta.kind, is_zip=zipfile.is_zipfile(path))]
    for c in checks:
        if (notes or {}).get(c["id"]):
            c["note"] = notes[c["id"]]
    return {
        "schema": report.SCHEMA, "record": record,
        "pack": {"id": meta.id, "kind": meta.kind, "cycle": meta.cycle,
                 "sha256": sha256_file(path), "bytes": path.stat().st_size,
                 "schema_version": str(meta.schema_version)},
        "origin": origin or report.ORIGIN_BY_KIND.get(meta.kind, "community"),
        "built": {"at": "2026-06-14T00:00:00Z", "repo": report.REPO, "commit": "c" * 40,
                  "builder_refs": {}, "run_url": None, "runner": "local"},
        "sources": list(sources),
        "previous": ({"cycle": previous["pack"]["cycle"], "sha256": previous["pack"]["sha256"]}
                     if previous else None),
        "checks": checks, "tables": {}, "metrics": {},
        "summary": {"pass": len(checks), "warn": 0, "fail": 0, "not_run": 0},
        "gated": meta.kind not in report.UNGATED, "notice": NOTICE,
    }


STANDIN = Options(reporter=standin_reporter)
