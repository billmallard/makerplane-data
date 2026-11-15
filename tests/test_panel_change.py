# SPDX-License-Identifier: Apache-2.0
"""CERT17: a config pull that changes the panel is shown on the Data Status
screen (the ``panel_change`` block of status.json) until a pilot acknowledges
it; an unchanged pull raises nothing."""
import argparse
import datetime as dt
import json

import yaml

from pyefis_data import cli, config_pull, panel_change
from tests.test_config_pull import _config_dir, _doc


def _args(tmp_path, wait_online=60, no_restart=True):
    return argparse.Namespace(config=str(tmp_path / "data.yaml"), configurator_url=None,
                              no_restart=no_restart, wait_online=wait_online)


def _serve(monkeypatch, tmp_path, *results):
    """Point config-pull at a fake config dir and serve the given fetch
    results in order."""
    cd = _config_dir(tmp_path)
    monkeypatch.setattr(config_pull, "config_dir", lambda: cd)
    seq = list(results)
    monkeypatch.setattr(config_pull, "fetch_config", lambda cfg: seq.pop(0))
    return cd


def _status(_panel_log):
    return json.loads(_panel_log[1].read_text("utf-8"))


def _ack(tmp_path, *extra):
    return cli.main(["--config", str(tmp_path / "data.yaml"), "panel-ack", *extra])


PANEL_A = yaml.safe_dump(_doc("PFD_AI_ONLY"))
PANEL_B = yaml.safe_dump(_doc("PFD_AI_ONLY", "EMS"))


def test_changed_pull_at_boot_is_shown_until_acknowledged(tmp_path, monkeypatch, _panel_log):
    _serve(monkeypatch, tmp_path, ("updated", 7, PANEL_A, '"e7"'))
    assert cli.cmd_config_pull(_args(tmp_path)) == 0
    block = _status(_panel_log)["panel_change"]
    assert block["pending"] is True
    assert block["trigger"] == "boot"
    assert block["config_version"] == 7
    assert block["config_hash"] == panel_change.config_hash(PANEL_A)
    dt.datetime.strptime(block["changed"], "%Y-%m-%dT%H:%M:%SZ")

    assert _ack(tmp_path, "--yes", "--hash", block["config_hash"]) == 0
    block = _status(_panel_log)["panel_change"]
    assert block["pending"] is False and block["acknowledged"]


def test_unchanged_pull_raises_nothing(tmp_path, monkeypatch, _panel_log):
    """A 304, and a 200 that serves the config already installed, record
    nothing: an acknowledged panel stays acknowledged."""
    _serve(monkeypatch, tmp_path, ("updated", 7, PANEL_A, '"e7"'),
           ("up-to-date", 7, None, None), ("updated", 7, PANEL_A, '"e7"'))
    assert cli.cmd_config_pull(_args(tmp_path)) == 0
    assert _ack(tmp_path, "--yes") == 0
    assert cli.cmd_config_pull(_args(tmp_path)) == 0          # 304
    assert cli.cmd_config_pull(_args(tmp_path)) == 0          # same bytes again
    assert [e["event"] for e in panel_change.read_events()] == ["changed", "acknowledged"]
    assert _status(_panel_log)["panel_change"]["pending"] is False


def test_a_second_change_needs_a_second_acknowledgment(tmp_path, monkeypatch, _panel_log):
    _serve(monkeypatch, tmp_path, ("updated", 7, PANEL_A, '"e7"'),
           ("updated", 8, PANEL_B, '"e8"'))
    assert cli.cmd_config_pull(_args(tmp_path)) == 0
    assert _ack(tmp_path, "--yes") == 0
    assert cli.cmd_config_pull(_args(tmp_path, wait_online=0)) == 0   # by hand
    block = _status(_panel_log)["panel_change"]
    assert block["pending"] is True
    assert block["config_version"] == 8 and block["trigger"] == "manual"


def test_ack_of_a_hash_no_longer_installed_is_refused(tmp_path, monkeypatch, _panel_log):
    """The screen acknowledges the config it showed, never a newer one."""
    _serve(monkeypatch, tmp_path, ("updated", 7, PANEL_A, '"e7"'),
           ("updated", 8, PANEL_B, '"e8"'))
    assert cli.cmd_config_pull(_args(tmp_path)) == 0
    shown = _status(_panel_log)["panel_change"]["config_hash"]
    assert cli.cmd_config_pull(_args(tmp_path)) == 0
    assert _ack(tmp_path, "--yes", "--hash", shown) == 2
    assert _status(_panel_log)["panel_change"]["pending"] is True


def test_ack_with_nothing_pending_is_a_quiet_no_op(tmp_path, _panel_log):
    assert _ack(tmp_path, "--yes") == 0
    assert panel_change.read_events() == []


def test_crashed_config_rolled_back_withdraws_its_cue(tmp_path, monkeypatch, _panel_log):
    """The new config crashed pyEfis and the old panel came back: the cue is
    for the panel actually installed, the acknowledged one."""
    _serve(monkeypatch, tmp_path, ("updated", 7, PANEL_A, '"e7"'),
           ("updated", 8, PANEL_B, '"e8"'))
    assert cli.cmd_config_pull(_args(tmp_path)) == 0
    assert _ack(tmp_path, "--yes") == 0
    monkeypatch.setattr(config_pull, "restart_and_verify",
                        lambda **kw: config_pull.RESTART_CRASHED)
    monkeypatch.setattr(config_pull, "restart_pyefis", lambda **kw: True)
    assert cli.cmd_config_pull(_args(tmp_path, no_restart=False)) == 1
    block = _status(_panel_log)["panel_change"]
    assert block["config_hash"] == panel_change.config_hash(PANEL_A)
    assert block["pending"] is False


def test_refresh_keeps_the_rest_of_status_json(_panel_log):
    _panel_log[1].write_text(json.dumps({"ok": True, "packs": [{"id": "x"}]}))
    panel_change.record_change(PANEL_A, 3, "boot")
    panel_change.refresh_status()
    doc = _status(_panel_log)
    assert doc["ok"] is True and doc["packs"] == [{"id": "x"}]
    assert doc["panel_change"]["pending"] is True


def test_corrupt_log_reads_as_nothing_pending(_panel_log):
    _panel_log[0].write_text('not json\n{"schema": 1, "event": "changed"}\n')
    assert panel_change.report()["pending"] is False
    panel_change.record_change(PANEL_A, 3, "boot")
    assert panel_change.report()["pending"] is True
