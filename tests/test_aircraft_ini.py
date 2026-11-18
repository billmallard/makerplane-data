# SPDX-License-Identifier: Apache-2.0
"""ACINI1 (AER-5993): config-pull delivers aircraft.ini to fix-gateway.

The served ``fixgw:`` block's ``aircraft_ini`` is hash- and line-checked,
staged, and only in maintenance mode dry-run through fix-gateway's loader,
installed as ``init_data/aircraft.ini`` and listed last in ``default.yaml``.
Pure file logic against fake config dirs; the dry run is exercised against a
stand-in ``fixgw`` package so the subprocess path is real.
"""
import argparse
import hashlib
import sys
import textwrap

import pytest
import yaml

from pyefis_data import config_pull

# The bench Pi's default.yaml head as it stands (2026-11-18): a 3-space list
# with comment lines between and after the items.
_MAIN = textwrap.dedent('''\
    # This is the main configuration file for the FIX-Gateway Server

    database file: "{CONFIG}/database.yaml"

    # Set to false if you do not want to auto-start
    auto start: true

    # If there are duplicate assignments in these files, the last
    # file listed will overwrite data in previous files
    initialization files:
       - "{CONFIG}/init_data/default.ini"
       - "{CONFIG}/init_data/tires.ini"
      # - "{CONFIG}/init_data/airplanes/cessna/c170b.ini"
      #- "{CONFIG}/init_data/engines/rotax/582.ini"
    # Make your edits in this file:
       - "{CONFIG}/init_data/custom.ini"
    # If you create a file for your engine, airplane or equipment
    # and want to share it please make a pull request on github.

    connections:
      include:
      - QUORUM_CONFIG
    ''')
_ENTRY = '   - "{CONFIG}/init_data/aircraft.ini"\n'
_INI = ("# aircraft.ini -- compiled by the pyEfis configurator\n\n"
        "IAS.Vne = 160\nIAS.Vs0 = 40\n\nOILT1.Max = 245\n")


def _block(text=_INI, sha=None):
    return {"aircraft_ini": text,
            "aircraft_ini_sha256": sha or hashlib.sha256(text.encode()).hexdigest()}


def _served(text=_INI):
    sha = hashlib.sha256(text.encode()).hexdigest()
    body = "".join("    " + l if l else "" for l in text.splitlines(keepends=True))
    return ("main:\n  defaultScreen: PFD\nscreens:\n  PFD:\n    instruments: []\n"
            f'fixgw:\n  aircraft_ini_sha256: "{sha}"\n  aircraft_ini: |\n{body}')


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    cd = tmp_path / "pyefis"
    (cd / "main").mkdir(parents=True)
    (cd / "screens").mkdir()
    (cd / "main" / "default.yaml").write_text("defaultScreen: PFD\n")
    (cd / "preferences.yaml.custom").write_text("includes: {}\n")
    fcd = tmp_path / "fixgw"
    (fcd / "init_data").mkdir(parents=True)
    (fcd / "default.yaml").write_text(_MAIN)
    (fcd / "init_data" / "custom.ini").write_text("TACH1.Max = 3000\n")
    monkeypatch.setenv("FIXGW_CONFIG_DIR", str(fcd))
    monkeypatch.setenv("PYEFIS_CONFIG_DIR", str(cd))
    monkeypatch.setattr(config_pull, "legacy_overlay", lambda cd, scope=None: config_pull.LEGACY_NONE)
    return cd, fcd


@pytest.fixture
def valid(monkeypatch):
    seen = []
    monkeypatch.setattr(config_pull, "validate_ini",
                        lambda fcd, staged: seen.append(staged.read_text()) or (True, ""))
    return seen


# ---- the served block -------------------------------------------------------

def test_served_block_parses_back_to_the_exact_text():
    doc = yaml.safe_load(_served())
    assert config_pull._served_ini(doc["fixgw"]) == _INI


@pytest.mark.parametrize("block, why", [
    (_block(sha="0" * 64), "sha256"),
    ({"aircraft_ini": _INI}, "not a string"),
    ({"aircraft_ini": 5, "aircraft_ini_sha256": "x"}, "not a string"),
    (_block("IAS.Vne = 160\n; project: a = b\n"), "dialect"),
    (_block("IAS.Vne = fast\n"), "dialect"),
    (_block("IAS.Vne=160\nX\n"), "dialect"),
    ("fixgw", "mapping"),
])
def test_a_bad_block_is_refused_with_a_reason(block, why):
    res = config_pull._served_ini(block)
    assert isinstance(res, tuple) and res[0] is config_pull._MALFORMED
    assert why in res[1]


def test_no_file_means_not_managed():
    assert config_pull._served_ini(None) is None
    assert config_pull._served_ini({"ownship_address": "AB4549"}) is None


# ---- the default.yaml enrolment ---------------------------------------------

def test_enrol_lists_it_last_and_keeps_every_other_line():
    new = config_pull._enrol_ini(_MAIN)
    assert new.replace(_ENTRY, "", 1) == _MAIN
    assert new.index(_ENTRY) == new.index("custom.ini") + len('custom.ini"\n')
    assert yaml.safe_load(new)["initialization files"][-1] == "{CONFIG}/init_data/aircraft.ini"
    assert config_pull._enrol_ini(new) == new                    # idempotent


def test_enrol_moves_an_entry_that_is_not_last():
    early = _MAIN.replace('   - "{CONFIG}/init_data/tires.ini"\n',
                          '   - "{CONFIG}/init_data/tires.ini"\n' + _ENTRY)
    new = config_pull._enrol_ini(early)
    files = yaml.safe_load(new)["initialization files"]
    assert files.count("{CONFIG}/init_data/aircraft.ini") == 1
    assert files[-1] == "{CONFIG}/init_data/aircraft.ini"
    assert new == config_pull._enrol_ini(_MAIN)


def test_enrol_adds_the_key_when_there_is_none():
    new = config_pull._enrol_ini('database file: "{CONFIG}/database.yaml"\n')
    assert yaml.safe_load(new)["initialization files"] == ["{CONFIG}/init_data/aircraft.ini"]


@pytest.mark.parametrize("text", [
    "initialization files: []\n",                    # a flow form the edit does not know
    "initialization files: nope\n",
    "- a list\n",
    "key: [unclosed\n",
])
def test_enrol_refuses_what_it_cannot_edit_safely(text):
    assert config_pull._enrol_ini(text) is None


# ---- stage, then install in maintenance mode --------------------------------

def test_outside_maintenance_it_is_only_staged(dirs, valid):
    cd, fcd = dirs
    s = config_pull.install_config(_served(), cd=cd, fcd=fcd)
    assert s["aircraft_ini"]["state"] == "pending"
    assert s["aircraft_ini"]["changed"] is False
    assert not (fcd / "init_data" / "aircraft.ini").exists()
    assert (fcd / "default.yaml").read_text() == _MAIN            # fix-gateway reads nothing new
    assert config_pull.pending_ini(cd, fcd) == hashlib.sha256(_INI.encode()).hexdigest()
    assert valid == []                                            # no dry run either


def test_maintenance_installs_enrols_and_asks_for_a_restart(dirs, valid):
    cd, fcd = dirs
    s = config_pull.install_config(_served(), cd=cd, fcd=fcd, maintenance=True)
    assert s["aircraft_ini"]["state"] == "installed" and s["aircraft_ini"]["changed"]
    assert valid == [_INI]                                        # dry-run before the swap
    assert (fcd / "init_data" / "aircraft.ini").read_text() == _INI
    assert (fcd / "default.yaml").read_text() == config_pull._enrol_ini(_MAIN)
    assert config_pull.pending_ini(cd, fcd) is None
    assert not (fcd / "init_data" / "aircraft.ini.pending").exists()
    # The same file again: nothing changes, so no fix-gateway restart.
    again = config_pull.install_config(_served(), cd=cd, fcd=fcd, maintenance=True)
    assert again["aircraft_ini"] == {"state": "unchanged", "changed": False,
                                     "sha256": s["aircraft_ini"]["sha256"]}
    assert valid == [_INI]


def test_a_staged_file_installs_on_a_later_maintenance_run(dirs, valid):
    cd, fcd = dirs
    config_pull.install_config(_served(), cd=cd, fcd=fcd)
    res = config_pull.install_pending_ini(cd, fcd)
    assert res["state"] == "installed"
    assert (fcd / "init_data" / "aircraft.ini").read_text() == _INI


def test_a_file_fixgw_would_refuse_is_dropped_and_nothing_installed(dirs, monkeypatch):
    cd, fcd = dirs
    monkeypatch.setattr(config_pull, "validate_ini",
                        lambda fcd, staged: (False, "fix-gateway would refuse: FUELQ4.Max"))
    s = config_pull.install_config(_served(), cd=cd, fcd=fcd, maintenance=True)
    assert s["aircraft_ini"]["changed"] is False
    assert "FUELQ4" in s["aircraft_ini"]["skipped"]
    assert not (fcd / "init_data" / "aircraft.ini").exists()
    assert (fcd / "default.yaml").read_text() == _MAIN
    assert config_pull.pending_ini(cd, fcd) is None


def test_a_file_that_could_not_be_checked_stays_staged(dirs, monkeypatch):
    cd, fcd = dirs
    monkeypatch.setattr(config_pull, "validate_ini",
                        lambda fcd, staged: (None, "no fix-gateway service found"))
    s = config_pull.install_config(_served(), cd=cd, fcd=fcd, maintenance=True)
    assert s["aircraft_ini"]["state"] == "pending"
    assert not (fcd / "init_data" / "aircraft.ini").exists()
    assert config_pull.pending_ini(cd, fcd) is not None


def test_a_torn_staged_file_does_not_count(dirs, valid):
    cd, fcd = dirs
    config_pull.install_config(_served(), cd=cd, fcd=fcd)
    (fcd / "init_data" / "aircraft.ini.pending").write_text("IAS.Vne = 16")
    assert config_pull.pending_ini(cd, fcd) is None
    assert config_pull.install_pending_ini(cd, fcd)["state"] is None


def test_an_unlistable_default_yaml_installs_nothing(dirs, valid):
    cd, fcd = dirs
    (fcd / "default.yaml").write_text("initialization files: []\n")
    s = config_pull.install_config(_served(), cd=cd, fcd=fcd, maintenance=True)
    assert s["aircraft_ini"]["state"] == "pending" and "safely" in s["aircraft_ini"]["skipped"]
    assert not (fcd / "init_data" / "aircraft.ini").exists()


def test_rollback_puts_both_files_back(dirs, valid):
    cd, fcd = dirs
    config_pull.install_config(_served(), cd=cd, fcd=fcd, maintenance=True)
    assert config_pull.rollback_fixgw(cd=cd, fcd=fcd) is True
    assert not (fcd / "init_data" / "aircraft.ini").exists()
    assert (fcd / "default.yaml").read_text() == _MAIN
    assert config_pull.rollback_fixgw(cd=cd, fcd=fcd) is False


def test_rollback_restores_the_previous_file_over_a_new_one(dirs, valid):
    cd, fcd = dirs
    config_pull.install_config(_served(), cd=cd, fcd=fcd, maintenance=True)
    newer = _INI.replace("160", "165")
    config_pull.install_config(_served(newer), cd=cd, fcd=fcd, maintenance=True)
    assert (fcd / "init_data" / "aircraft.ini").read_text() == newer
    assert config_pull.rollback_fixgw(cd=cd, fcd=fcd) is True
    assert (fcd / "init_data" / "aircraft.ini").read_text() == _INI
    assert (fcd / "default.yaml").read_text() == config_pull._enrol_ini(_MAIN)


def test_rollback_never_deletes_default_yaml(dirs):
    cd, fcd = dirs
    (fcd / "default.yaml").unlink()
    config_pull.snapshot(cd, fcd)
    (fcd / "default.yaml").write_text(_MAIN)                     # appeared since
    config_pull.rollback_fixgw(cd=cd, fcd=fcd)
    assert (fcd / "default.yaml").read_text() == _MAIN


# ---- the dry run, against a stand-in fixgw package --------------------------

_FAKE_FIXGW = {
    "__init__.py": "",
    "cfg.py": textwrap.dedent('''\
        import yaml
        def from_yaml(fs, preferences=None):
            with open(fs) as f:
                return yaml.safe_load(f)
        '''),
    # Mirrors fixgw.database.write: an unknown key or aux is a KeyError, a
    # value the type refuses is a ValueError -- both stop fix-gateway.
    "database.py": textwrap.dedent('''\
        _db = {}
        def init(f):
            with open(f) as fh:
                for line in fh:
                    if line.strip():
                        k, kind = line.split()
                        _db[k] = kind
        def write(key, value):
            kind = _db[key]
            {"int": int, "float": float}[kind](value)
        '''),
}


@pytest.fixture
def fake_fixgw(tmp_path, monkeypatch, dirs):
    cd, fcd = dirs
    pkg = tmp_path / "site" / "fixgw"
    pkg.mkdir(parents=True)
    for name, src in _FAKE_FIXGW.items():
        (pkg / name).write_text(src)
    (fcd / "preferences.yaml").write_text("enabled: {}\n")
    (fcd / "database.yaml").write_text("IAS.Vne float\nIAS.Vs0 float\nOILT1.Max float\n"
                                       "TACH1.Max int\n")
    for f in ("default.ini", "tires.ini"):
        (fcd / "init_data" / f).write_text("")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "site"))
    monkeypatch.setattr(config_pull, "_fixgw_unit", lambda: ("fixgw", "user"))
    monkeypatch.setattr(config_pull, "_fixgw_python", lambda scope, unit: (sys.executable, None))
    return fcd


def test_dry_run_accepts_what_fixgw_would_load(fake_fixgw, tmp_path):
    staged = tmp_path / "ok.ini"
    staged.write_text(_INI)
    assert config_pull.validate_ini(fake_fixgw, staged) == (True, "")


def test_dry_run_names_each_line_fixgw_would_refuse(fake_fixgw, tmp_path):
    staged = tmp_path / "bad.ini"
    staged.write_text("IAS.Vne = 160\nFUELQ4.Max = 9\nTACH1.Max = 2700.5\n")
    ok, why = config_pull.validate_ini(fake_fixgw, staged)
    assert ok is False
    assert "bad.ini:2: FUELQ4.Max = 9 (KeyError" in why
    assert "bad.ini:3: TACH1.Max = 2700.5 (ValueError" in why


def test_dry_run_replaces_the_live_file_with_the_staged_one(fake_fixgw, tmp_path):
    (fake_fixgw / "init_data" / "aircraft.ini").write_text("NOPE.Max = 1\n")
    (fake_fixgw / "default.yaml").write_text(config_pull._enrol_ini(_MAIN))
    staged = tmp_path / "ok.ini"
    staged.write_text(_INI)
    assert config_pull.validate_ini(fake_fixgw, staged) == (True, "")


def test_dry_run_that_cannot_run_is_not_a_refusal(fake_fixgw, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "nowhere"))
    staged = tmp_path / "ok.ini"
    staged.write_text(_INI)
    ok, why = config_pull.validate_ini(fake_fixgw, staged)
    assert ok is None and "could not run" in why
    monkeypatch.setattr(config_pull, "_fixgw_unit", lambda: None)
    assert config_pull.validate_ini(fake_fixgw, staged)[0] is None


def test_fixgw_python_reads_the_scripts_shebang(tmp_path, monkeypatch):
    script = tmp_path / "fixgw"
    script.write_text(f"#!{sys.executable}\nimport fixgw\n")
    monkeypatch.setattr(config_pull, "_systemctl_show", lambda prop, scope, unit="pyefis": {
        "ExecStart": f"{{ path={script} ; argv[]={script} --config-file x }}",
        "WorkingDirectory": "/w"}[prop])
    assert config_pull._fixgw_python("user", "fixgw") == (sys.executable, "/w")


def test_fixgw_python_strips_systemds_missing_ok_marker(tmp_path, monkeypatch):
    # What the bench Pi's fixgw unit shows (2026-11-18); passed through as a
    # cwd it made the dry run fail to start.
    script = tmp_path / "fixgw"
    script.write_text(f"#!{sys.executable}\n")
    monkeypatch.setattr(config_pull, "_systemctl_show", lambda prop, scope, unit="pyefis": {
        "ExecStart": f"{{ path={script} ; argv[]={script} }}",
        "WorkingDirectory": "!/home/wpballard"}[prop])
    assert config_pull._fixgw_python("user", "fixgw") == (sys.executable, "/home/wpballard")


# ---- the CLI: the fix-gateway restart is maintenance mode only --------------

def test_cli_stages_on_boot_and_installs_and_restarts_in_maintenance(dirs, valid, tmp_path,
                                                                      monkeypatch, capsys):
    from pyefis_data import cli
    from pyefis_data.config import write_config
    cd, fcd = dirs
    data = tmp_path / "data.yaml"
    write_config(data, {"device_token": "t", "config_version": 4})
    monkeypatch.setattr(config_pull, "config_dir", lambda: cd)
    restarts = []
    monkeypatch.setattr(config_pull, "restart_fixgw_and_verify",
                        lambda: restarts.append((fcd / "default.yaml").read_text()) or config_pull.RESTART_OK)
    monkeypatch.setattr(config_pull, "restart_fixgw", lambda: restarts.append("plain") or True)
    monkeypatch.setattr(config_pull, "restart_and_verify", lambda **kw: config_pull.RESTART_OK)
    monkeypatch.setattr(cli, "_record_panel_change", lambda write: None)

    def args(maintenance=False):
        return argparse.Namespace(config=str(data), configurator_url=None, no_restart=False,
                                  wait_online=0, maintenance=maintenance)

    # The boot pull: staged, fix-gateway untouched.
    monkeypatch.setattr(config_pull, "fetch_config", lambda cfg: ("updated", 5, _served(), '"v5-p1"'))
    assert cli.cmd_config_pull(args()) == 0
    assert restarts == []
    assert not (fcd / "init_data" / "aircraft.ini").exists()
    assert "--maintenance" in capsys.readouterr().err
    # Next pull is a 304; still not maintenance: nothing.
    monkeypatch.setattr(config_pull, "fetch_config", lambda cfg: ("up-to-date", 5, None, None))
    assert cli.cmd_config_pull(args()) == 0
    assert restarts == []
    # On the ground, by hand: installed, listed, then fix-gateway restarted.
    assert cli.cmd_config_pull(args(maintenance=True)) == 0
    assert restarts == [config_pull._enrol_ini(_MAIN)]
    assert (fcd / "init_data" / "aircraft.ini").read_text() == _INI
    # Again: nothing pending, nothing restarted.
    assert cli.cmd_config_pull(args(maintenance=True)) == 0
    assert len(restarts) == 1


def test_cli_puts_fixgw_files_back_when_fixgw_does_not_stay_up(dirs, valid, tmp_path,
                                                              monkeypatch, capsys):
    from pyefis_data import cli
    from pyefis_data.config import write_config
    cd, fcd = dirs
    data = tmp_path / "data.yaml"
    write_config(data, {"device_token": "t", "config_version": 4})
    monkeypatch.setattr(config_pull, "config_dir", lambda: cd)
    outcomes = [config_pull.RESTART_CRASHED, config_pull.RESTART_OK]
    monkeypatch.setattr(config_pull, "restart_fixgw_and_verify", lambda: outcomes.pop(0))
    monkeypatch.setattr(config_pull, "restart_and_verify", lambda **kw: config_pull.RESTART_OK)
    monkeypatch.setattr(cli, "_record_panel_change", lambda write: None)
    monkeypatch.setattr(config_pull, "fetch_config", lambda cfg: ("updated", 5, _served(), '"v5-p1"'))
    a = argparse.Namespace(config=str(data), configurator_url=None, no_restart=False,
                           wait_online=0, maintenance=True)
    assert cli.cmd_config_pull(a) == 1
    assert outcomes == []                                         # restarted on the old files
    assert not (fcd / "init_data" / "aircraft.ini").exists()
    assert (fcd / "default.yaml").read_text() == _MAIN
    assert "previous config" in capsys.readouterr().err
