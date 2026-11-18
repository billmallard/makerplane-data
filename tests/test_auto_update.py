# SPDX-License-Identifier: Apache-2.0
"""`auto_update: false` in data.yaml stops the daily timer's update (#196).

The timer's unit runs `pyefis-data update --scheduled`. With auto_update off,
that run installs nothing and exits 0; an update a person starts (no
--scheduled: the picker, a terminal, a USB import) still runs."""

from functools import partial
from pathlib import Path

import pytest

from pyefis_data import cli
from pyefis_data.core import Updater
from tests.test_pi_updater import ORIGIN, TODAY, build_store


@pytest.fixture
def store(tmp_path, monkeypatch, capsys):
    root, pub = build_store(tmp_path)
    monkeypatch.setattr(cli, "PUBLIC_KEY", pub)
    monkeypatch.setattr(cli, "Updater", partial(Updater, today=TODAY))
    capsys.readouterr()
    return root


def _update(tmp_path, store, auto_update, *extra):
    cfg = tmp_path / "data.yaml"
    cfg.write_text(f"auto_update: {'true' if auto_update else 'false'}\n"
                   "packs: [airports-conus]\ntrack_kinds: []\n")
    return cli.main(["--config", str(cfg), "--base-url", ORIGIN,
                     "--root", str(tmp_path / "pi"), "update", "--source", str(store),
                     *extra])


def _installed(tmp_path):
    return (tmp_path / "pi" / "navdata" / "2606" / "airports.sqlite").exists()


def test_scheduled_update_is_skipped_when_auto_update_is_off(tmp_path, store, capsys):
    assert _update(tmp_path, store, False, "--scheduled") == 0
    assert not _installed(tmp_path)
    assert "auto_update is off" in capsys.readouterr().out


def test_scheduled_update_runs_when_auto_update_is_on(tmp_path, store):
    assert _update(tmp_path, store, True, "--scheduled") == 0
    assert _installed(tmp_path)


def test_manual_update_runs_when_auto_update_is_off(tmp_path, store):
    assert _update(tmp_path, store, False) == 0
    assert _installed(tmp_path)


def test_timer_unit_passes_scheduled():
    unit = (Path(cli.__file__).parent / "systemd" / "pyefis-data.service").read_text()
    exec_start = [ln for ln in unit.splitlines() if ln.startswith("ExecStart=")]
    assert exec_start == ["ExecStart=%h/.local/bin/pyefis-data update --scheduled"]
