# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _ack_log(tmp_path_factory, monkeypatch):
    """Point the software-notice acknowledgment log at a per-test temp file so
    no test ever appends to the real ~/.local/state log, and pre-seed one
    accept so CLI tests that drive `update`/`import` non-interactively get past
    the gate. tests/test_acknowledge.py points it at an empty file instead.
    Yields the path."""
    from pyefis_data import acknowledge as ack
    path = tmp_path_factory.mktemp("ackstate") / "acknowledgments.jsonl"
    monkeypatch.setenv("MAKERPLANE_ACK_LOG", str(path))
    ack.append_record(ack.make_record(via="caller", trigger="test-fixture"), path)
    yield path
