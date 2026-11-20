# SPDX-License-Identifier: Apache-2.0
"""CERT16 (AER-5994): every job that can read the signing secret runs in the
`signing` Environment, which only main may deploy to.

The Environment's branch rule is a repository setting, not a file, so this
test cannot see it (docs/signing_key.md says how to check it). What it does
hold is the other half: a workflow that reads MINISIGN_SECRET_KEY without
naming the Environment would get the repository-level secret on any branch,
for as long as one exists, and nothing else would notice.
"""

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
SECRET = "secrets.MINISIGN_SECRET_KEY"


def _jobs():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(path.read_text("utf-8")) or {}
        for name, job in (doc.get("jobs") or {}).items():
            yield path.name, name, job


def test_every_job_that_reads_the_signing_key_names_the_environment():
    readers = [(f, n, j) for f, n, j in _jobs() if SECRET in yaml.safe_dump(j)]
    assert readers, "no workflow reads the signing key; this test has gone stale"
    wrong = [f"{f}:{n}" for f, n, j in readers if j.get("environment") != "signing"]
    assert not wrong, f"jobs read {SECRET} outside the signing Environment: {wrong}"


def test_the_key_is_not_read_at_workflow_level():
    # A workflow-level env: block would hand the key to every job in it,
    # including ones with no environment.
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(path.read_text("utf-8")) or {}
        assert SECRET not in yaml.safe_dump(doc.get("env") or {}), path.name


def test_the_signing_workflows_are_the_known_set():
    # A new signing workflow is a custody change: list it here and in
    # docs/signing_key.md in the same PR.
    readers = {f for f, _n, j in _jobs() if SECRET in yaml.safe_dump(j)}
    assert readers == {"advisories.yml", "cyclical.yml", "manifest-admin.yml",
                       "roads.yml", "water.yml"}
