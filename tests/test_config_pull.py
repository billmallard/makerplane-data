# SPDX-License-Identifier: Apache-2.0
"""pyefis_data.config_pull -- the on-Pi panel install (single + multi-screen #72).

Exercises the pure-Python install/rollback logic against a fake pyEfis config
dir (no Qt, no Pi). The restart/verify helpers are systemd-bound and tested on
the device, not here.
"""
import textwrap

import yaml

from pyefis_data import config_pull


def _config_dir(tmp_path, default_screen="PFD_AI_ONLY", custom=None):
    """A minimal pyEfis config dir: a main/ with a defaultScreen and an existing
    preferences.yaml.custom (so the pristine-backup + merge paths are real)."""
    cd = tmp_path / "config"
    (cd / "main").mkdir(parents=True)
    (cd / "screens").mkdir()
    (cd / "buttons").mkdir()
    # Mirrors the real device file: a uniformly-indented mapping with no
    # column-0 key, so yaml.safe_load yields defaultScreen at the top level
    # (that's what _device_default_screen reads).
    (cd / "main" / "default.yaml").write_text(
        f"  nodeID: 1\n  defaultScreen: {default_screen}\n", encoding="utf-8")
    custom = custom if custom is not None else {
        "style": {"basic": True},
        "includes": {"SCREEN_PFD_AI_ONLY": "screens/stock.yaml"},
    }
    (cd / "preferences.yaml.custom").write_text(
        yaml.safe_dump(custom, sort_keys=False), encoding="utf-8")
    # Stock base preferences + screen list, so _stock_screen_tokens reads real
    # data (multi-screen keeps this full list loaded as GL-safe ballast).
    (cd / "preferences.yaml").write_text(
        "includes:\n  SCREENS_CONFIG: screens/default_list.yaml\n", encoding="utf-8")
    (cd / "screens" / "default_list.yaml").write_text(yaml.safe_dump({"include": [
        "SCREEN_DATA_STATUS", "SCREEN_SIXPACK", "SCREEN_PFD", "SCREEN_PFD_AI_ONLY",
        "SCREEN_RADIO", "SCREEN_EMS", "SCREEN_EMS2"]}, sort_keys=False), encoding="utf-8")
    return cd


def _doc(*screen_names, vfr_on=None):
    """A native editor config with the given screens; vfr_on names a screen that
    carries a virtual_vfr instrument."""
    screens = {}
    for n in screen_names:
        insts = [{"type": "altimeter_tape", "row": 0, "column": 184,
                  "span": {"rows": 110, "columns": 16}}]
        if n == vfr_on:
            insts.insert(0, {"type": "virtual_vfr", "row": 0, "column": 0,
                             "span": {"rows": 110, "columns": 200}})
        screens[n] = {"module": "pyefis.screens.screenbuilder", "title": n,
                      "layout": {"rows": 110, "columns": 200}, "instruments": insts}
    return {"main": {"defaultScreen": screen_names[0]}, "screens": screens}


def _read(cd, rel):
    return yaml.safe_load((cd / rel).read_text("utf-8"))


# --- single screen: proven full-stock-list path -------------------------------

def test_single_screen_keeps_stock_list(tmp_path):
    cd = _config_dir(tmp_path)
    doc = _doc("PANEL", vfr_on="PANEL")
    summary = config_pull.install_config(yaml.safe_dump(doc), cd=cd)

    assert summary["mode"] == "single"
    assert summary["boot_screen"] == "PFD_AI_ONLY"     # device default, not "PANEL"
    assert summary["screens"] == 1

    managed = _read(cd, "screens/managed.yaml")
    assert "PFD_AI_ONLY" in managed                     # keyed by the device default
    inc = _read(cd, "preferences.yaml.custom")["includes"]
    assert inc["SCREEN_PFD_AI_ONLY"] == "screens/managed.yaml"
    assert "SCREENS_CONFIG" not in inc                  # stock screen list kept
    assert not (cd / "screens" / "managed_list.yaml").exists()


def test_non_stock_boot_screen_is_woven_into_the_loaded_list(tmp_path):
    """If the device boots into a NON-stock screen name (a prior managed config or
    bench testing left defaultScreen there), the repurposed SCREEN_<boot> maps a
    file but is never in the loaded SCREENS_CONFIG -- so gui.showScreen KeyErrors
    on the DataStatus continue and the device crash-loops. Weave the boot token
    into the list so the screen loads."""
    cd = _config_dir(tmp_path, default_screen="MAP_CTL_TEST")   # non-stock boot
    summary = config_pull.install_config(yaml.safe_dump(_doc("PANEL")), cd=cd)

    assert summary["boot_screen"] == "MAP_CTL_TEST"
    inc = _read(cd, "preferences.yaml.custom")["includes"]
    assert inc["SCREEN_MAP_CTL_TEST"] == "screens/managed.yaml"
    # a managed_list override must now exist AND include the boot token
    assert inc["SCREENS_CONFIG"] == "screens/managed_list.yaml"
    loaded = _read(cd, "screens/managed_list.yaml")["include"]
    assert "SCREEN_MAP_CTL_TEST" in loaded               # <- would KeyError without this
    assert "SCREEN_PFD_AI_ONLY" in loaded                # stock ballast still kept


def test_single_screen_injects_svs_and_db(tmp_path):
    cd = _config_dir(tmp_path)
    config_pull.install_config(yaml.safe_dump(_doc("PANEL", vfr_on="PANEL")), cd=cd)
    screen = _read(cd, "screens/managed.yaml")["PFD_AI_ONLY"]
    vfr = next(i for i in screen["instruments"] if i["type"] == "virtual_vfr")
    assert vfr["options"]["svs"]["enabled"] is True
    assert "screens/virtualvfr_db.yaml" in screen["include"]


# --- multi screen: clean managed list + switch buttons (#72) ------------------

def test_multi_screen_keeps_stock_list(tmp_path):
    cd = _config_dir(tmp_path)
    doc = _doc("PANEL", "ROUND_DIALS", vfr_on="PANEL")
    summary = config_pull.install_config(yaml.safe_dump(doc), cd=cd)

    assert summary["mode"] == "multi"
    assert summary["screens"] == 2
    # default screen takes the device's defaultScreen name; the other keeps its own
    assert summary["screen_names"] == ["PFD_AI_ONLY", "ROUND_DIALS"]

    inc = _read(cd, "preferences.yaml.custom")["includes"]
    # the default editor screen repurposes the device's default slot...
    assert inc["SCREEN_PFD_AI_ONLY"] == "screens/managed_PFD_AI_ONLY.yaml"
    # ...the additional screen is a NEW token appended to the stock list
    assert inc["SCREEN_M_ROUND_DIALS"] == "screens/managed_ROUND_DIALS.yaml"
    assert inc["SCREENS_CONFIG"] == "screens/managed_list.yaml"

    lst = _read(cd, "screens/managed_list.yaml")["include"]
    # every stock screen is preserved (GL ballast + no broken nav), extra appended
    assert lst[:7] == ["SCREEN_DATA_STATUS", "SCREEN_SIXPACK", "SCREEN_PFD",
                       "SCREEN_PFD_AI_ONLY", "SCREEN_RADIO", "SCREEN_EMS", "SCREEN_EMS2"]
    assert lst[-1] == "SCREEN_M_ROUND_DIALS"
    assert len(lst) >= 7         # never shorter than stock (avoids the GL segfault)


def test_multi_screen_injects_switch_button_on_each(tmp_path):
    cd = _config_dir(tmp_path)
    config_pull.install_config(
        yaml.safe_dump(_doc("PANEL", "ROUND_DIALS", vfr_on="PANEL")), cd=cd)

    dbkeys = []
    # each screen's button does an explicit "show screen: <next editor screen>"
    for fname, key, btn, target in [
            ("managed_PFD_AI_ONLY.yaml", "PFD_AI_ONLY",
             "buttons/managed-next-PFD_AI_ONLY.yaml", "ROUND_DIALS"),
            ("managed_ROUND_DIALS.yaml", "ROUND_DIALS",
             "buttons/managed-next-ROUND_DIALS.yaml", "PFD_AI_ONLY")]:
        assert (cd / btn).exists()
        screen = _read(cd, f"screens/{fname}")[key]
        buttons = [i for i in screen["instruments"]
                   if i.get("type") == "button"
                   and i.get("options", {}).get("config") == btn]
        assert len(buttons) == 1, f"{fname} should have exactly one switch button"

        cfg = _read(cd, btn)
        actions = [a for c in cfg["conditions"] for a in c.get("actions", [])]
        assert {"show screen": target} in actions, f"{btn} should jump to {target}"
        dbkeys.append(cfg["dbkey"])

    # registered TSBTN range is 1..40; suffixes must be in range and DISTINCT so
    # the two screens' buttons don't share a key
    suffixes = [int(k.replace("TSBTN{id}", "")) for k in dbkeys]
    assert all(1 <= s <= 40 for s in suffixes), suffixes
    assert len(set(suffixes)) == len(suffixes), f"switch-button keys must differ: {dbkeys}"


def test_multi_then_single_clears_managed_list(tmp_path):
    """Re-deploying a 1-screen panel after a multi must drop the managed-list
    override so the stock screen set loads again."""
    cd = _config_dir(tmp_path)
    config_pull.install_config(
        yaml.safe_dump(_doc("PANEL", "ROUND_DIALS", vfr_on="PANEL")), cd=cd)
    config_pull.install_config(yaml.safe_dump(_doc("PANEL", vfr_on="PANEL")), cd=cd)

    inc = _read(cd, "preferences.yaml.custom")["includes"]
    assert "SCREENS_CONFIG" not in inc
    assert not any(k.startswith("SCREEN_M_") for k in inc)
    assert inc["SCREEN_PFD_AI_ONLY"] == "screens/managed.yaml"


# --- rollback -----------------------------------------------------------------

def test_rollback_restores_previous_panel(tmp_path):
    cd = _config_dir(tmp_path)
    # First install a single-screen panel (the "working" one), then a multi.
    config_pull.install_config(yaml.safe_dump(_doc("PANEL", vfr_on="PANEL")), cd=cd)
    good_custom = (cd / "preferences.yaml.custom").read_text("utf-8")
    good_managed = (cd / "screens" / "managed.yaml").read_text("utf-8")

    config_pull.install_config(
        yaml.safe_dump(_doc("PANEL", "ROUND_DIALS", vfr_on="PANEL")), cd=cd)
    # the multi install changed the override away from the single-screen one
    assert (cd / "preferences.yaml.custom").read_text("utf-8") != good_custom

    where = config_pull.rollback(cd=cd)
    assert where == "previous panel"
    assert (cd / "preferences.yaml.custom").read_text("utf-8") == good_custom
    assert (cd / "screens" / "managed.yaml").read_text("utf-8") == good_managed


def test_rollback_without_snapshot_uses_pristine(tmp_path):
    cd = _config_dir(tmp_path)
    pristine = (cd / "preferences.yaml.custom").read_text("utf-8")
    # Simulate the very first install having stamped a pristine backup, then a
    # bad override, with no usable snapshot manifest.
    (cd / "preferences.yaml.custom.prepanel").write_text(pristine, encoding="utf-8")
    (cd / "preferences.yaml.custom").write_text("includes: {bad: x}\n", encoding="utf-8")
    where = config_pull.rollback(cd=cd)
    assert where == "stock config"
    assert (cd / "preferences.yaml.custom").read_text("utf-8") == pristine


def test_rejects_non_native_config(tmp_path):
    cd = _config_dir(tmp_path)
    import pytest
    with pytest.raises(ValueError):
        config_pull.install_config(yaml.safe_dump({"design": {"instruments": []}}), cd=cd)


# --- config-pull --wait-online network-race handling -------------------------

def _cp_args(wait_online=0):
    import argparse
    return argparse.Namespace(config="/no/such/data.yaml", configurator_url=None,
                              no_restart=True, wait_online=wait_online)


def test_wait_online_retries_until_network_up(monkeypatch):
    """A transient DNS failure is retried until it clears (the boot race)."""
    from pyefis_data import cli, config_pull
    calls = {"n": 0}

    def fake_fetch(cfg):
        calls["n"] += 1
        if calls["n"] < 3:
            return ("error:<urlopen error [Errno -3] Temporary failure "
                    "in name resolution>", None, None)
        return ("up-to-date", 7, None)

    monkeypatch.setattr(config_pull, "fetch_config", fake_fetch)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    assert cli.cmd_config_pull(_cp_args(wait_online=30)) == 0
    assert calls["n"] == 3            # retried twice, then up-to-date


def test_wait_online_gives_up_clean_when_offline(monkeypatch):
    """Boot path: a parked aircraft with no network gives up gracefully after
    the window (exit 0, current panel kept) -- not a failed unit every boot."""
    from pyefis_data import cli, config_pull
    monkeypatch.setattr(config_pull, "fetch_config",
                        lambda cfg: ("error:name resolution", None, None))
    monkeypatch.setattr("time.sleep", lambda _s: None)
    clock = {"t": 0.0}
    monkeypatch.setattr("time.monotonic",
                        lambda: clock.__setitem__("t", clock["t"] + 5.0) or clock["t"])
    assert cli.cmd_config_pull(_cp_args(wait_online=10)) == 0


def test_manual_pull_fails_fast_without_wait(monkeypatch):
    """No --wait-online (manual run): a network error fails immediately."""
    from pyefis_data import cli, config_pull
    calls = {"n": 0}

    def fake_fetch(cfg):
        calls["n"] += 1
        return ("error:name resolution", None, None)

    monkeypatch.setattr(config_pull, "fetch_config", fake_fetch)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    assert cli.cmd_config_pull(_cp_args(wait_online=0)) == 2
    assert calls["n"] == 1            # one attempt, no retry loop


# --- restart scope detection + no false rollback (#23) -----------------------

def _fake_systemd(scope_with_unit="user", healthy=True, restartable=True):
    """A fake ``subprocess.run`` standing in for systemd. ``scope_with_unit`` is
    the scope ('user'/'system'/None) whose pyefis unit is loaded and (if
    ``restartable``) restartable; ``healthy`` controls whether the running unit
    verifies as up. Records the scope each restart used in ``.restarts``."""
    import subprocess as sp
    log = {"restarts": []}

    def run(argv, **kw):
        is_user = "--user" in argv
        scope = "user" if is_user else "system"
        def done(out=""):
            return sp.CompletedProcess(argv, 0, stdout=out, stderr="")
        if "show" in argv and "LoadState" in argv:
            return done("loaded" if scope == scope_with_unit else "not-found")
        if "restart" in argv:
            log["restarts"].append(scope)
            if scope == scope_with_unit and restartable:
                return done()
            raise sp.CalledProcessError(5, argv)   # no unit / unreachable here
        if "show" in argv and "MainPID" in argv:
            return done("4242" if healthy else "0")
        if "show" in argv and "ActiveState" in argv:
            return done("active" if healthy else "failed")
        if "status" in argv:
            return done("pyefis.service: running clean\nno errors here")
        return done()

    return run, log


def test_detect_prefers_user_then_system(monkeypatch):
    """Scope detection: user unit wins when present; otherwise system; else the
    historical 'user' default when neither scope answers."""
    from pyefis_data import config_pull
    run, _ = _fake_systemd(scope_with_unit="user")
    monkeypatch.setattr(config_pull.subprocess, "run", run)
    assert config_pull._detect_scope() == "user"

    run, _ = _fake_systemd(scope_with_unit="system")
    monkeypatch.setattr(config_pull.subprocess, "run", run)
    assert config_pull._detect_scope() == "system"

    run, _ = _fake_systemd(scope_with_unit=None)   # loaded nowhere
    monkeypatch.setattr(config_pull.subprocess, "run", run)
    assert config_pull._detect_scope() == "user"


def test_system_service_verifies_without_false_crash(monkeypatch):
    """The #23 case: pyEfis is a *system* service. restart_and_verify must detect
    the system scope, restart THAT unit, and report OK -- never a spurious crash
    just because `systemctl --user` has no pyefis unit."""
    from pyefis_data import config_pull
    run, log = _fake_systemd(scope_with_unit="system", healthy=True)
    monkeypatch.setattr(config_pull.subprocess, "run", run)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    assert config_pull.restart_and_verify(wait_s=0) == config_pull.RESTART_OK
    assert log["restarts"] == ["system"]        # detected + restarted the system unit


def test_explicit_scope_override_is_honoured(monkeypatch):
    """An explicit ``pyefis_unit_scope`` skips detection and restarts that scope."""
    from pyefis_data import config_pull
    run, log = _fake_systemd(scope_with_unit="system", healthy=True)
    monkeypatch.setattr(config_pull.subprocess, "run", run)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    assert config_pull.restart_and_verify(wait_s=0, scope="system") == config_pull.RESTART_OK
    assert log["restarts"] == ["system"]


def test_unreachable_service_is_restart_failed_not_crashed(monkeypatch):
    """A restart command that cannot run in ANY scope is RESTART_FAILED (broken
    mechanism), never RESTART_CRASHED (broken panel) -- so the caller won't roll
    back a good panel."""
    from pyefis_data import config_pull
    run, _ = _fake_systemd(scope_with_unit=None, restartable=False)
    monkeypatch.setattr(config_pull.subprocess, "run", run)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    assert config_pull.restart_and_verify(wait_s=0) == config_pull.RESTART_FAILED


def test_crashed_panel_still_reports_crashed(monkeypatch):
    """A restart that runs but leaves pyEfis down (PID 0 / inactive) is still
    RESTART_CRASHED so the crash-rollback path is preserved."""
    from pyefis_data import config_pull
    run, _ = _fake_systemd(scope_with_unit="user", healthy=False)
    monkeypatch.setattr(config_pull.subprocess, "run", run)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    assert config_pull.restart_and_verify(wait_s=0) == config_pull.RESTART_CRASHED


def test_traceback_in_log_reports_crashed(monkeypatch):
    """A stable, active PID whose log carries a Traceback is a screen-build
    exception, still RESTART_CRASHED (the lingering-FIX-thread false 'up')."""
    from pyefis_data import config_pull
    import subprocess as sp

    def run(argv, **kw):
        def done(out=""):
            return sp.CompletedProcess(argv, 0, stdout=out, stderr="")
        if "show" in argv and "LoadState" in argv:
            return done("loaded" if "--user" in argv else "not-found")
        if "restart" in argv:
            return done()
        if "show" in argv and "MainPID" in argv:
            return done("999")
        if "show" in argv and "ActiveState" in argv:
            return done("active")
        if "status" in argv:
            return done("python[999]: Traceback (most recent call last):\n"
                        "python[999]:   KeyError: Screen FOO Not Found")
        return done()

    monkeypatch.setattr(config_pull.subprocess, "run", run)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    assert config_pull.restart_and_verify(wait_s=0) == config_pull.RESTART_CRASHED


def test_cli_restart_failed_keeps_panel_and_does_not_roll_back(tmp_path, monkeypatch):
    """End-to-end #23 regression: install succeeds, the restart MECHANISM fails
    (unreachable service). The good panel must be KEPT (no rollback), exit 1 with
    a 'restart it manually' message -- not the old false 'crashed; rolled back'."""
    import argparse
    from pyefis_data import cli, config_pull

    cd = _config_dir(tmp_path)
    doc_text = yaml.safe_dump(_doc("PFD_AI_ONLY"))
    monkeypatch.setattr(config_pull, "config_dir", lambda: cd)
    monkeypatch.setattr(config_pull, "fetch_config",
                        lambda cfg: ("updated", 5, doc_text))
    monkeypatch.setattr(config_pull, "restart_and_verify",
                        lambda **kw: config_pull.RESTART_FAILED)
    rolled = {"called": False}
    monkeypatch.setattr(config_pull, "rollback",
                        lambda *a, **k: rolled.__setitem__("called", True) or "x")

    args = argparse.Namespace(config=str(tmp_path / "data.yaml"),
                              configurator_url=None, no_restart=False, wait_online=0)
    rc = cli.cmd_config_pull(args)
    assert rc == 1
    assert rolled["called"] is False                        # a good panel is kept
    assert (cd / "screens" / "managed.yaml").exists()       # the install stuck
