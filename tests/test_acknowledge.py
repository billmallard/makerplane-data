# SPDX-License-Identifier: Apache-2.0
"""Software-notice acknowledgment (BACKLOG L2): the blocking accept at
install/update, the append-only record, its schema, and its survival of a
config reset."""

import datetime as dt
import io
import json
import shutil
from functools import partial

import pytest

import pyefis_data
from pyefis_data import acknowledge as ack
from pyefis_data import cli
from pyefis_data.core import Updater
from tests.test_pi_updater import ORIGIN, TODAY, build_store

# Bill's approved text (2026-10-30), spelled out here independently of the
# module so an accidental edit to NOTICE_TEXT fails a test.
APPROVED = ("pyEfis is experimental software for Experimental / Amateur-Built "
            "aircraft. It is not certified. It is provided as-is with no warranty, "
            "and any use of it is at your own risk.")

RECORD_KEYS = {"schema", "event", "timestamp", "notice_id", "notice_sha256",
               "notice_text", "software", "packs", "user", "host", "via", "trigger"}


@pytest.fixture
def log(tmp_path, monkeypatch):
    """An EMPTY acknowledgment log (overrides the pre-seeded conftest one)."""
    p = tmp_path / "state" / "acknowledgments.jsonl"
    monkeypatch.setenv("MAKERPLANE_ACK_LOG", str(p))
    return p


class _Tty(io.StringIO):
    def isatty(self):
        return True


def _stdin(monkeypatch, text=None):
    """None = no terminal (timer, picker); a string = a terminal typing it."""
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("") if text is None else _Tty(text))


@pytest.fixture
def store(tmp_path, monkeypatch, capsys):
    root, pub = build_store(tmp_path)
    monkeypatch.setattr(cli, "PUBLIC_KEY", pub)
    monkeypatch.setattr(cli, "Updater", partial(Updater, today=TODAY))
    capsys.readouterr()
    return root


def _update(tmp_path, store, *extra):
    return cli.main(["--config", str(tmp_path / "data.yaml"), "--base-url", ORIGIN,
                     "--root", str(tmp_path / "pi"), "update", "--source", str(store),
                     *extra])


def _installed(tmp_path):
    return (tmp_path / "pi" / "navdata" / "2606" / "airports.sqlite").exists()


def _lines(path):
    return path.read_text().splitlines() if path.exists() else []


# ---- the record ----------------------------------------------------------

def test_notice_text_is_the_approved_text_verbatim():
    assert ack.NOTICE_TEXT == APPROVED


def test_record_schema():
    rec = ack.make_record(via="tty", trigger="update", packs={"airports-conus": "2606"},
                          now=dt.datetime(2026, 10, 31, 14, 5, 9, tzinfo=dt.timezone.utc),
                          user="pilot", host="pyefis")
    assert ack.validate_record(rec) == []
    assert set(rec) == RECORD_KEYS
    assert rec["schema"] == 1 and rec["event"] == "accept"
    assert rec["timestamp"] == "2026-10-31T14:05:09Z"
    assert rec["notice_text"] == APPROVED
    assert rec["software"]["pyefis-data"] == pyefis_data.__version__
    assert rec["packs"] == {"airports-conus": "2606"}
    assert (rec["user"], rec["host"], rec["via"], rec["trigger"]) == \
        ("pilot", "pyefis", "tty", "update")
    # round-trips through the on-disk form unchanged
    assert json.loads(json.dumps(rec)) == rec


@pytest.mark.parametrize("mutate, problem", [
    (lambda r: r.pop("host"), "missing host"),
    (lambda r: r.update(timestamp="2026-10-31 14:05"), "timestamp"),
    (lambda r: r.update(notice_text=r["notice_text"] + " "), "notice_sha256"),
    (lambda r: r.update(via="timer"), "via"),
    (lambda r: r.update(software={}), "software.pyefis-data"),
    (lambda r: r.update(packs={"x": 2606}), "strings"),
    (lambda r: r.update(schema=2), "schema"),
])
def test_validate_record_rejects(mutate, problem):
    rec = ack.make_record(via="tty", trigger="update")
    mutate(rec)
    assert any(problem in e for e in ack.validate_record(rec))


def test_append_only_never_rewrites_earlier_records(log):
    ack.append_record(ack.make_record(via="tty", trigger="install"), log)
    first = log.read_bytes()
    ack.append_record(ack.make_record(via="caller", trigger="update"), log)
    assert log.read_bytes().startswith(first)
    assert [r["trigger"] for r in ack.read_records(log)] == ["install", "update"]


def test_invalid_record_is_never_written(log):
    rec = ack.make_record(via="tty", trigger="update")
    rec["via"] = "timer"
    with pytest.raises(ValueError):
        ack.append_record(rec, log)
    assert not log.exists()


def test_reader_never_raises_and_skips_corrupt_lines(log, tmp_path):
    assert ack.read_records(log) == []                     # missing file
    assert ack.read_records(tmp_path) == []                # a directory
    ack.append_record(ack.make_record(via="tty", trigger="install"), log)
    with open(log, "a") as f:
        f.write("{not json\n")
        f.write(json.dumps({"schema": 1}) + "\n")
    assert len(ack.read_records(log)) == 1
    assert ack.find_acceptance(path=log) is not None


# ---- the path, and a config reset ----------------------------------------

def test_default_path_is_outside_the_config_trees(tmp_path, monkeypatch):
    monkeypatch.delenv("MAKERPLANE_ACK_LOG")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    p = ack.default_log_path()
    assert p == tmp_path / ".local" / "state" / "makerplane" / "acknowledgments.jsonl"
    for config_tree in (tmp_path / ".makerplane", tmp_path / "makerplane"):
        assert config_tree not in p.parents
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert ack.default_log_path() == tmp_path / "xdg" / "makerplane" / "acknowledgments.jsonl"


def test_acknowledgment_survives_a_config_reset(tmp_path, monkeypatch):
    monkeypatch.delenv("MAKERPLANE_ACK_LOG")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    _stdin(monkeypatch)
    cfg_dir = tmp_path / ".makerplane" / "pyefis"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "data.yaml").write_text("auto_update: true\n")
    pyefis_cfg = tmp_path / "makerplane" / "pyefis" / "config"
    pyefis_cfg.mkdir(parents=True)
    assert cli.main(["accept", "--yes"]) == 0
    before = ack.default_log_path().read_bytes()
    # a config reset: both config trees wiped
    shutil.rmtree(tmp_path / ".makerplane")
    shutil.rmtree(tmp_path / "makerplane")
    assert ack.default_log_path().read_bytes() == before
    assert ack.find_acceptance() is not None


# ---- the gate on update / import -----------------------------------------

def test_fresh_install_without_a_terminal_installs_nothing(tmp_path, store, log,
                                                           monkeypatch, capsys):
    _stdin(monkeypatch)
    rc = _update(tmp_path, store, "--only", "airports-conus", "--progress")
    assert rc == cli.NOTICE_RC == 3
    assert not _installed(tmp_path)
    assert not (tmp_path / "data.yaml").exists()           # selection not persisted
    assert not log.exists()
    out, err = capsys.readouterr()
    events = [json.loads(l) for l in out.splitlines() if l.startswith("{")]
    assert events == [{"event": "notice_required", "notice": APPROVED,
                       "notice_id": ack.NOTICE_ID}]
    assert APPROVED in err


def test_terminal_accept_records_then_installs(tmp_path, store, log, monkeypatch, capsys):
    _stdin(monkeypatch, "accept\n")
    assert _update(tmp_path, store) == 0
    assert _installed(tmp_path)
    assert APPROVED in capsys.readouterr().out             # the full text was shown
    [rec] = ack.read_records(log)
    assert (rec["via"], rec["trigger"]) == ("tty", "update")
    assert rec["packs"] == {}                              # fresh box: nothing installed yet


@pytest.mark.parametrize("answer", ["\n", "y\n", "yes\n", "no\n"])
def test_terminal_decline_installs_nothing(tmp_path, store, log, monkeypatch, answer):
    _stdin(monkeypatch, answer)
    assert _update(tmp_path, store) == cli.NOTICE_RC
    assert not _installed(tmp_path)
    assert not log.exists()


def test_caller_accept_flag_records_then_installs(tmp_path, store, log, monkeypatch):
    _stdin(monkeypatch)
    assert _update(tmp_path, store, "--accept-notice") == 0
    assert _installed(tmp_path)
    [rec] = ack.read_records(log)
    assert (rec["via"], rec["trigger"]) == ("caller", "update")


def test_caller_accept_can_name_other_software(tmp_path, store, log, monkeypatch):
    """The picker runs in the updater's environment, where pyEfis is not
    importable; --software lets it record the pyEfis version it is part of."""
    _stdin(monkeypatch)
    assert _update(tmp_path, store, "--accept-notice", "--software", "pyefis=2.2.1") == 0
    [rec] = ack.read_records(log)
    assert rec["software"]["pyefis"] == "2.2.1"
    assert rec["software"]["pyefis-data"] == pyefis_data.__version__


def test_malformed_software_installs_nothing(tmp_path, store, log, monkeypatch):
    _stdin(monkeypatch)
    assert _update(tmp_path, store, "--accept-notice", "--software", "pyefis") == 2
    assert not _installed(tmp_path)
    assert not log.exists()


def test_recorded_accept_lets_unattended_update_run(tmp_path, store, log, monkeypatch):
    _stdin(monkeypatch, "accept\n")
    assert _update(tmp_path, store, "--dry-run") == 0      # dry run: no gate, no record
    assert not log.exists()
    assert _update(tmp_path, store) == 0
    _stdin(monkeypatch)                                    # now the timer, no terminal
    assert _update(tmp_path, store) == 0
    assert len(_lines(log)) == 1                           # no second, fabricated accept


def test_new_software_version_requires_a_new_accept(tmp_path, store, log, monkeypatch):
    ack.append_record(ack.make_record(via="tty", trigger="install"), log)
    monkeypatch.setattr(ack, "__version__", "99.0.0")
    _stdin(monkeypatch)
    assert _update(tmp_path, store) == cli.NOTICE_RC
    assert not _installed(tmp_path)


def test_unrecordable_accept_installs_nothing(tmp_path, store, monkeypatch):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    monkeypatch.setenv("MAKERPLANE_ACK_LOG", str(blocker / "acknowledgments.jsonl"))
    _stdin(monkeypatch)
    assert _update(tmp_path, store, "--accept-notice") == 2
    assert not _installed(tmp_path)


def test_import_is_gated_too(tmp_path, store, log, monkeypatch):
    _stdin(monkeypatch)
    args = ["--base-url", ORIGIN, "--root", str(tmp_path / "pi"), "import", str(store)]
    assert cli.main(args) == cli.NOTICE_RC
    assert not _installed(tmp_path)
    assert cli.main(args + ["--accept-notice"]) == 0
    assert _installed(tmp_path)
    assert [r["trigger"] for r in ack.read_records(log)] == ["import"]


# ---- accept / notice commands --------------------------------------------

def test_accept_records_installed_packs_and_extra_software(tmp_path, store, log,
                                                           monkeypatch, capsys):
    _stdin(monkeypatch)
    assert _update(tmp_path, store, "--accept-notice") == 0
    assert cli.main(["--root", str(tmp_path / "pi"), "accept", "--yes",
                     "--software", "pyefis=2.1.0", "--trigger", "pyefis-install"]) == 0
    rec = ack.read_records(log)[-1]
    assert rec["software"]["pyefis"] == "2.1.0"
    assert rec["trigger"] == "pyefis-install"
    assert rec["packs"] == {"airports-conus": "2606", "obstacles-conus": "260611"}
    capsys.readouterr()
    assert cli.main(["notice", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["accepted"] is True and doc["notice"] == APPROVED
    assert len(doc["records"]) == 2 and doc["log"] == str(log)


def test_accept_without_terminal_or_yes_refuses(log, monkeypatch):
    _stdin(monkeypatch)
    assert cli.main(["accept"]) == cli.NOTICE_RC
    assert not log.exists()
    assert cli.main(["accept", "--software", "pyefis"]) == 2


def test_notice_reports_not_accepted_on_a_fresh_box(log, capsys):
    assert cli.main(["notice", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["accepted"] is False and doc["records"] == []


# ---- bench units (Bill's ruling, AER-4388) --------------------------------

def _bench(tmp_path, value="true"):
    (tmp_path / "data.yaml").write_text(f"bench_auto_accept: {value}\n")


def test_bench_setting_is_off_by_default(tmp_path):
    from pyefis_data.config import Config
    assert Config().bench_auto_accept is False
    assert Config.load(tmp_path / "missing.yaml").bench_auto_accept is False


@pytest.mark.parametrize("value", ["yes", "on", "True", "1", "'true'", "[true]"])
def test_bench_setting_honours_only_a_literal_true(tmp_path, value):
    from pyefis_data.config import Config
    _bench(tmp_path, value)
    assert Config.load(tmp_path / "data.yaml").bench_auto_accept is False


def test_installer_and_picker_never_write_the_bench_setting(tmp_path, store, log,
                                                             monkeypatch):
    """The picker persists its selection through write_config; the bench key
    must not appear, and nothing shipped to a device carries it."""
    _stdin(monkeypatch)
    assert _update(tmp_path, store, "--accept-notice", "--only", "airports-conus") == 0
    assert "bench" not in (tmp_path / "data.yaml").read_text()
    repo = pyefis_data.__file__.rsplit("/", 2)[0]
    from pathlib import Path
    shipped = [Path(repo, "site", "data.yaml.sample"),
               *Path(repo, "pyefis_data", "systemd").iterdir()]
    for f in shipped:
        assert "bench_auto_accept" not in f.read_text(), f


def test_bench_unit_auto_accepts_without_a_terminal(tmp_path, store, log, monkeypatch,
                                                     capsys):
    _bench(tmp_path)
    _stdin(monkeypatch)
    assert _update(tmp_path, store) == 0
    assert _installed(tmp_path)
    (rec,) = [json.loads(x) for x in _lines(log)]
    assert not ack.validate_record(rec)
    assert rec["via"] == "bench" and rec["trigger"] == "update"
    # Recorded as a bench accept, never as a pilot's.
    assert ack.find_acceptance(path=log) is None
    assert ack.find_acceptance(path=log, include_bench=True) == rec


def test_bench_unit_never_prompts_even_at_a_terminal(tmp_path, store, log, monkeypatch):
    _bench(tmp_path)
    _stdin(monkeypatch, "")        # a terminal that would decline if asked
    assert _update(tmp_path, store) == 0
    assert [json.loads(x)["via"] for x in _lines(log)] == ["bench"]


def test_bench_timer_keeps_installing_across_a_version_bump(tmp_path, store, log,
                                                            monkeypatch):
    _bench(tmp_path)
    _stdin(monkeypatch)
    assert _update(tmp_path, store) == 0
    assert _update(tmp_path, store) == 0          # same version: no second record
    assert len(_lines(log)) == 1
    monkeypatch.setattr(ack, "__version__", "99.0.0")
    assert _update(tmp_path, store) == 0          # bumped: accepts again, unattended
    recs = [json.loads(x) for x in _lines(log)]
    assert [r["via"] for r in recs] == ["bench", "bench"]
    assert recs[-1]["software"]["pyefis-data"] == "99.0.0"


def test_bench_accept_does_not_carry_over_to_a_real_install(tmp_path, store, log,
                                                            monkeypatch):
    """A box taken out of bench mode is asked like any real install."""
    _bench(tmp_path)
    _stdin(monkeypatch)
    assert _update(tmp_path, store) == 0
    (tmp_path / "data.yaml").write_text("")
    assert _update(tmp_path, store) == cli.NOTICE_RC
    assert len(_lines(log)) == 1


def test_real_install_keeps_the_blocking_accept(tmp_path, store, log, monkeypatch):
    _bench(tmp_path, "false")
    _stdin(monkeypatch)
    assert _update(tmp_path, store) == cli.NOTICE_RC
    assert not _installed(tmp_path) and not _lines(log)


def test_bench_setting_is_logged_at_startup_when_on(tmp_path, log, capsys):
    cfg = str(tmp_path / "data.yaml")
    assert cli.main(["--config", cfg, "notice"]) == 0
    assert "BENCH UNIT" not in capsys.readouterr().err
    _bench(tmp_path)
    assert cli.main(["--config", cfg, "notice"]) == 0
    err = capsys.readouterr().err
    assert "BENCH UNIT" in err and "bench_auto_accept is on" in err


def test_bench_banner_does_not_claim_an_explicit_accept_is_automatic(
        tmp_path, log, monkeypatch, capsys):
    # `accept` still prompts on a bench unit, so the banner must not tell
    # the person at the terminal it has already been accepted for them.
    _bench(tmp_path)
    _stdin(monkeypatch, "no\n")
    assert cli.main(["--config", str(tmp_path / "data.yaml"), "accept"]) == cli.NOTICE_RC
    assert not _lines(log)
    err = capsys.readouterr().err
    assert "BENCH UNIT" in err
    assert "the software notice is accepted automatically" not in err
    assert "an explicit `accept` still asks" in err


def test_notice_json_keeps_bench_out_of_accepted(tmp_path, store, log, monkeypatch, capsys):
    _bench(tmp_path)
    _stdin(monkeypatch)
    assert _update(tmp_path, store) == 0
    capsys.readouterr()
    assert cli.main(["--config", str(tmp_path / "data.yaml"), "notice", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["accepted"] is False and out["bench_auto_accept"] is True
    assert [r["via"] for r in out["records"]] == ["bench"]
