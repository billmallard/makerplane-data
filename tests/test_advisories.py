# SPDX-License-Identifier: Apache-2.0
"""The safety advisory feed (BACKLOG L5): schema, signed publish, and the
device side -- opt-in, verify, replay refusal, on-device matching, silence on
an empty feed, and nothing identifying ever leaving the device."""

import datetime as dt
import json
from functools import partial
from pathlib import Path

import pytest

from packtools import advisories as feed
from packtools import signing
from packtools.upload import LocalStore

from pyefis_data import __version__, advisories as adv, cli
from pyefis_data.config import Config
from pyefis_data.core import Inventory, Updater

ORIGIN = "https://test.local"
REPO = Path(__file__).resolve().parents[1]


def _adv(**kw):
    a = {"id": "MPA-2026-001", "issued": "2026-11-08", "severity": "warning",
         "title": "Obstacle heights wrong", "description": "Some heights are low.",
         "action": "Update obstacle data.",
         "affects": [{"pack": "obstacles-conus", "cycles": ["260611"]}]}
    a.update(kw)
    return a


def _feed(seq=1, advisories=()):
    return {"schema": feed.SCHEMA, "generated": "2026-11-08T00:00:00Z",
            "sequence": seq, "advisories": list(advisories)}


class DictRemote:
    """URL -> bytes; records every URL asked for. Missing = FileNotFoundError,
    like a USB stick without the file."""

    def __init__(self, objs=None):
        self.objs, self.urls = dict(objs or {}), []

    def get_bytes(self, url, timeout=30):
        self.urls.append(url)
        if url not in self.objs:
            raise FileNotFoundError(url)
        return self.objs[url]


@pytest.fixture
def keys():
    return signing.generate_keypair()


def _serve(remote, sk, obj, *, sig_for=None):
    raw = feed.to_bytes(obj)
    sig = signing.sign(feed.to_bytes(sig_for) if sig_for else raw, sk)
    remote.objs[f"{ORIGIN}/advisories.json"] = raw
    remote.objs[f"{ORIGIN}/advisories.json.minisig"] = sig.encode("ascii")


def _cfg(tmp_path, on=True):
    return Config(base_url=ORIGIN, root=tmp_path / "pi", advisories=on)


def _inv(tmp_path, packs=None):
    inv = Inventory(tmp_path / "pi" / "installed.json")
    for pid, cycle in (packs or {}).items():
        inv.set_current(pid, cycle, "0" * 64)
    return inv


# --------------------------------------------------------------------------
# versions and schema
# --------------------------------------------------------------------------

@pytest.mark.parametrize("version,want", [
    ("2.0.9", feed.NO), ("2.1", feed.YES), ("2.1.0", feed.YES),
    ("2.1.0rc1", feed.YES),        # a pre-release of the introducing release
    ("2.1.2", feed.YES), ("2.1.3rc1", feed.YES),   # not yet the fixed release
    ("2.1.3.dev0", feed.YES), ("2.1.3", feed.NO), ("2.1.3+g1a2b", feed.NO),
    ("2.2", feed.NO), ("garbage", feed.UNKNOWN), (None, feed.UNKNOWN),
])
def test_software_range(version, want):
    t = {"software": "pyefis", "introduced": "2.1.0", "fixed": "2.1.3"}
    assert feed.software_match(t, version) == want


def test_open_ended_ranges_and_exact_versions():
    assert feed.software_match({"software": "x", "fixed": "1.0"}, "0.1") == feed.YES
    assert feed.software_match({"software": "x", "introduced": "1.0"}, "9.9") == feed.YES
    assert feed.software_match({"software": "x"}, "0.0.1") == feed.YES
    t = {"software": "x", "versions": ["1.2.0"]}
    assert feed.software_match(t, "1.2") == feed.YES
    assert feed.software_match(t, "1.2.1") == feed.NO


def test_committed_feed_is_valid_and_canonical():
    path = REPO / "advisories" / "feed.json"
    obj = feed.load_file(path)
    assert path.read_bytes() == feed.to_bytes(obj)


@pytest.mark.parametrize("bad", [
    {"id": "ADV-1"}, {"severity": "urgent"}, {"action": "  "}, {"issued": "soon"},
    {"affects": []},
    {"affects": [{"software": "pyefis", "pack": "x", "cycles": ["1"]}]},
    {"affects": [{"software": "pyefis", "introduced": "2.1", "fixed": "2.0"}]},
    {"affects": [{"software": "pyefis", "fixed": "2.1rc1"}]},
    {"affects": [{"software": "pyefis", "versions": ["1"], "fixed": "2"}]},
    {"affects": [{"pack": "obstacles-conus", "cycles": []}]},
])
def test_feed_errors_reject_bad_advisories(bad):
    assert feed.feed_errors(_feed(advisories=[_adv(**bad)]))


def test_feed_errors_reject_bad_feeds():
    assert feed.feed_errors(_feed(advisories=[_adv(), _adv()]))       # duplicate id
    assert feed.feed_errors(dict(_feed(), sequence=0))
    assert feed.feed_errors(dict(_feed(), generated="2026-11-08"))
    assert feed.feed_errors(dict(_feed(), schema="advisory-feed/2"))
    assert not feed.feed_errors(_feed(advisories=[_adv()]))


# --------------------------------------------------------------------------
# publish (build side)
# --------------------------------------------------------------------------

def test_publish_signs_with_the_pack_key_and_refuses_lower_sequence(tmp_path, keys):
    sk, pub = keys
    store = LocalStore(tmp_path / "r2")
    assert feed.publish(store, sk, _feed(1), sign=signing.sign, log=lambda m: None) == "published"
    raw = store.get_bytes(feed.FEED_KEY)
    signing.verify(raw, store.get_bytes(feed.FEED_SIG_KEY).decode(), pub)
    assert feed.publish(store, sk, _feed(1), sign=signing.sign, log=lambda m: None) == "unchanged"
    with pytest.raises(feed.FeedError, match="not above"):
        feed.publish(store, sk, _feed(1, [_adv()]), sign=signing.sign, log=lambda m: None)
    assert feed.publish(store, sk, _feed(2, [_adv()]), sign=signing.sign,
                        dry_run=True, log=lambda m: None) == "dry-run"
    assert store.get_bytes(feed.FEED_KEY) == raw          # dry run wrote nothing
    feed.publish(store, sk, _feed(2, [_adv()]), sign=signing.sign, log=lambda m: None)
    assert json.loads(store.get_bytes(feed.FEED_KEY))["sequence"] == 2


def test_publish_refuses_over_an_unverifiable_live_feed(tmp_path, keys):
    sk, _ = keys
    other, _ = signing.generate_keypair()
    store = LocalStore(tmp_path / "r2")
    feed.publish(store, other, _feed(1), sign=signing.sign, log=lambda m: None)
    with pytest.raises(feed.FeedError, match="does not verify"):
        feed.publish(store, sk, _feed(2), sign=signing.sign, log=lambda m: None)


def test_site_deploy_cannot_overwrite_the_feed():
    from packtools import sitedeploy
    assert {feed.FEED_KEY, feed.FEED_SIG_KEY} <= sitedeploy._RESERVED_KEYS


def test_packtool_check_only(capsys):
    from packtools import cli as packcli
    assert packcli.main(["advisories", str(REPO / "advisories" / "feed.json"),
                         "--check-only"]) == 0
    assert "sequence 1" in capsys.readouterr().out


# --------------------------------------------------------------------------
# device: opt-in, verify, replay, silence
# --------------------------------------------------------------------------

def test_off_by_default_and_off_fetches_nothing(tmp_path, keys):
    assert Config().advisories is False
    remote = DictRemote()
    r = adv.check(_cfg(tmp_path, on=False), keys[1], remote)
    assert r["disabled"] and remote.urls == []
    assert adv.report(_cfg(tmp_path, on=False), keys[1], _inv(tmp_path)) == {"enabled": False}


def test_config_reads_the_setting(tmp_path):
    p = tmp_path / "data.yaml"
    p.write_text("advisories: true\n")
    assert Config.load(p).advisories is True
    p.write_text("advisories: maybe\n")
    assert Config.load(p).advisories is False


def test_empty_feed_is_silence(tmp_path, keys):
    sk, pub = keys
    remote = DictRemote()
    _serve(remote, sk, _feed(1))
    assert adv.check(_cfg(tmp_path), pub, remote)["ok"]
    block = adv.report(_cfg(tmp_path), pub, _inv(tmp_path))
    assert block["enabled"] and block["active"] == [] and block["error"] is None
    assert block["sequence"] == 1 and block["checked"]
    assert adv.lines(block) == []


def test_bad_signature_is_never_cached(tmp_path, keys):
    sk, pub = keys
    remote = DictRemote()
    _serve(remote, sk, _feed(1, [_adv()]), sig_for=_feed(1))
    r = adv.check(_cfg(tmp_path), pub, remote)
    assert not r["ok"] and "verified" in r["error"]
    assert not (tmp_path / "pi" / adv.CACHE).exists()
    block = adv.report(_cfg(tmp_path), pub, _inv(tmp_path, {"obstacles-conus": "260611"}))
    assert block["active"] == [] and block["error"]


def test_torn_pair_is_retried_once(tmp_path, keys):
    sk, pub = keys
    good = feed.to_bytes(_feed(1))

    class Torn(DictRemote):
        def get_bytes(self, url, timeout=30):
            if url.endswith(".minisig") and self.urls.count(url) == 0:
                self.urls.append(url)
                return signing.sign(b"older body", sk).encode()
            return super().get_bytes(url)
    remote = Torn({f"{ORIGIN}/advisories.json": good,
                   f"{ORIGIN}/advisories.json.minisig": signing.sign(good, sk).encode()})
    assert adv.check(_cfg(tmp_path), pub, remote)["ok"]


def test_replayed_lower_sequence_is_refused_and_newer_advisory_kept(tmp_path, keys):
    sk, pub = keys
    remote = DictRemote()
    _serve(remote, sk, _feed(2, [_adv()]))
    adv.check(_cfg(tmp_path), pub, remote)
    _serve(remote, sk, _feed(1))                    # validly signed, but older
    r = adv.check(_cfg(tmp_path), pub, remote)
    assert r["refused"] == {"offered": 1, "cached": 2}
    block = adv.report(_cfg(tmp_path), pub, _inv(tmp_path, {"obstacles-conus": "260611"}))
    assert [a["id"] for a in block["active"]] == ["MPA-2026-001"]
    assert "refused" in block["error"]


def test_same_sequence_with_different_content_is_refused(tmp_path, keys):
    sk, pub = keys
    remote = DictRemote()
    _serve(remote, sk, _feed(1, [_adv()]))
    adv.check(_cfg(tmp_path), pub, remote)
    _serve(remote, sk, _feed(1))
    assert adv.check(_cfg(tmp_path), pub, remote)["refused"]


def test_offline_keeps_the_last_verified_feed(tmp_path, keys):
    sk, pub = keys
    remote = DictRemote()
    _serve(remote, sk, _feed(1, [_adv()]))
    adv.check(_cfg(tmp_path), pub, remote)

    class Down:
        def get_bytes(self, url, timeout=30):
            raise ConnectionError("no route")
    r = adv.check(_cfg(tmp_path), pub, Down(), log=lambda m: None)
    assert "could not fetch" in r["error"]
    block = adv.report(_cfg(tmp_path), pub, _inv(tmp_path, {"obstacles-conus": "260611"}))
    assert block["active"][0]["applies"] == "yes"


def test_newer_feed_format_is_an_error_not_silence(tmp_path, keys):
    sk, pub = keys
    remote = DictRemote()
    _serve(remote, sk, dict(_feed(1), schema="advisory-feed/2"))
    r = adv.check(_cfg(tmp_path), pub, remote, log=lambda m: None)
    assert "update pyefis-data" in r["error"]
    assert "update pyefis-data" in adv.report(_cfg(tmp_path), pub, _inv(tmp_path))["error"]


# --------------------------------------------------------------------------
# device: matching
# --------------------------------------------------------------------------

def _active(tmp_path, keys, advisories, packs=None, software=None):
    sk, pub = keys
    remote = DictRemote()
    _serve(remote, sk, _feed(1, advisories))
    adv.check(_cfg(tmp_path), pub, remote)
    return adv.report(_cfg(tmp_path), pub, _inv(tmp_path, packs), software)["active"]


def test_pack_advisory_matches_installed_cycle_only(tmp_path, keys):
    hit = _active(tmp_path, keys, [_adv()], {"obstacles-conus": "260611"})
    assert hit[0]["applies"] == "yes" and hit[0]["affects"][0]["result"] == "yes"
    assert _active(tmp_path / "b", keys, [_adv()], {"obstacles-conus": "260709"}) == []
    assert _active(tmp_path / "c", keys, [_adv()], {}) == []


def test_staged_cycle_counts(tmp_path, keys):
    sk, pub = keys
    inv = _inv(tmp_path, {"obstacles-conus": "260514"})
    inv.set_staged("obstacles-conus", "260611", "0" * 64)
    remote = DictRemote()
    _serve(remote, sk, _feed(1, [_adv()]))
    adv.check(_cfg(tmp_path), pub, remote)
    assert adv.report(_cfg(tmp_path), pub, inv)["active"][0]["applies"] == "yes"


def test_updater_version_matches_on_device(tmp_path, keys):
    a = _adv(affects=[{"software": "pyefis-data", "versions": [__version__]}])
    assert _active(tmp_path, keys, [a])[0]["applies"] == "yes"


def test_unknown_pyefis_version_is_unknown_not_no(tmp_path, keys, monkeypatch):
    import importlib.metadata as md

    def _missing(name):
        raise md.PackageNotFoundError(name)
    monkeypatch.setattr(md, "version", _missing)
    a = _adv(affects=[{"software": "pyefis", "introduced": "2.1.0", "fixed": "2.1.3"}])
    got = _active(tmp_path, keys, [a])
    assert got[0]["applies"] == "unknown"
    assert any("pyefis" in ln for ln in adv.lines({"enabled": True, "active": got}))
    # Named by the caller, it resolves.
    assert _active(tmp_path / "b", keys, [a], software={"pyefis": "2.1.3"}) == []
    assert _active(tmp_path / "c", keys, [a], software={"pyefis": "2.1.1"})[0]["applies"] == "yes"


def test_unreadable_advisory_is_shown_as_unknown_not_dropped(tmp_path, keys):
    sk, pub = keys
    odd = _adv(severity="extreme", affects=[{"hardware": "gps-module", "models": ["x"]}])
    # The build side would refuse this; a newer feed revision could carry it.
    raw = feed.to_bytes(_feed(1, [odd]))
    remote = DictRemote({f"{ORIGIN}/advisories.json": raw,
                         f"{ORIGIN}/advisories.json.minisig": signing.sign(raw, sk).encode()})
    adv.check(_cfg(tmp_path), pub, remote)
    got = adv.report(_cfg(tmp_path), pub, _inv(tmp_path))["active"]
    assert got[0]["applies"] == "unknown" and got[0]["severity"] == "warning"


def test_ordering_yes_before_unknown_then_severity(tmp_path, keys):
    advs = [_adv(id="MPA-2026-003", severity="advisory"),
            _adv(id="MPA-2026-002", severity="warning",
                 affects=[{"software": "nonexistent-component", "fixed": "1.0"}]),
            _adv(id="MPA-2026-001", severity="caution")]
    got = _active(tmp_path, keys, advs, {"obstacles-conus": "260611"})
    assert [a["id"] for a in got] == ["MPA-2026-001", "MPA-2026-003", "MPA-2026-002"]


# --------------------------------------------------------------------------
# nothing identifying leaves the device
# --------------------------------------------------------------------------

def test_requests_carry_no_device_identifying_data(tmp_path, keys, monkeypatch):
    """Every HTTP request the advisory check makes, through the production
    transport and the CLI: only GETs of the two fixed feed URLs, no query,
    no body, no headers, no cookies. Any other requests call fails."""
    import requests
    sk, pub = keys
    raw = feed.to_bytes(_feed(1, [_adv()]))
    bodies = {f"{ORIGIN}/advisories.json": raw,
              f"{ORIGIN}/advisories.json.minisig": signing.sign(raw, sk).encode()}
    calls = []

    class Resp:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

    def fake_get(url, *a, **kw):
        calls.append((url, a, kw))
        return Resp(bodies[url])

    def forbidden(*a, **kw):
        raise AssertionError(f"unexpected request {a} {kw}")
    monkeypatch.setattr(requests, "get", fake_get)
    for name in ("post", "put", "head", "patch", "request"):
        monkeypatch.setattr(requests, name, forbidden)
    monkeypatch.setattr(requests.Session, "request", forbidden)
    monkeypatch.setattr(cli, "PUBLIC_KEY", pub)

    data_yaml = tmp_path / "data.yaml"
    data_yaml.write_text("advisories: true\n")
    inv = _inv(tmp_path, {"obstacles-conus": "260611"})
    inv.save()
    assert cli.main(["--config", str(data_yaml), "--base-url", ORIGIN, "--root",
                     str(tmp_path / "pi"), "advisories", "--check",
                     "--software", "pyefis=2.1.1"]) == 0

    assert [c[0] for c in calls] == list(bodies)
    for url, a, kw in calls:
        assert "?" not in url and "#" not in url
        assert a == () and set(kw) <= {"timeout"}
    # And the identifying values this device holds appear in none of them.
    import getpass
    import socket
    sent = json.dumps(calls, default=str)
    for secret in (socket.gethostname(), getpass.getuser(), "260611", "2.1.1",
                   __version__, "obstacles-conus", str(tmp_path)):
        assert secret not in sent


# --------------------------------------------------------------------------
# CLI and end-to-end through update
# --------------------------------------------------------------------------

def test_enable_disable_round_trip(tmp_path, keys, monkeypatch, capsys):
    sk, pub = keys
    monkeypatch.setattr(cli, "PUBLIC_KEY", pub)
    remote = DictRemote()
    _serve(remote, sk, _feed(1))
    monkeypatch.setattr("pyefis_data.core.HttpRemote", lambda: remote)
    data_yaml = tmp_path / "data.yaml"
    base = ["--config", str(data_yaml), "--base-url", ORIGIN, "--root", str(tmp_path / "pi")]

    assert cli.main(base + ["advisories"]) == 0
    assert "off" in capsys.readouterr().out and remote.urls == []

    assert cli.main(base + ["advisories", "--enable"]) == 0
    out = capsys.readouterr().out
    assert adv.DISCLOSURE in out and "no safety advisories apply" in out
    assert Config.load(data_yaml).advisories is True
    assert len(remote.urls) == 2

    assert cli.main(base + ["advisories", "--disable"]) == 0
    assert Config.load(data_yaml).advisories is False
    remote.urls.clear()
    assert cli.main(base + ["advisories", "--check", "--json"]) == 0
    assert json.loads(capsys.readouterr().out.split("\n", 1)[1]) == {"enabled": False}
    assert remote.urls == []


def test_notice_json_carries_the_disclosure(capsys):
    assert cli.main(["notice", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["advisory_disclosure"] == adv.DISCLOSURE


def _published_store(tmp_path, keys, advisories):
    from tests.test_pi_updater import build_store
    sk, pub = keys
    root, _ = build_store(tmp_path, keypair=keys)
    feed.publish(LocalStore(root), sk, _feed(1, advisories), sign=signing.sign,
                 log=lambda m: None)
    return root


@pytest.mark.parametrize("enabled", [True, False])
def test_update_reports_a_matching_advisory_end_to_end(tmp_path, keys, monkeypatch,
                                                       capsys, enabled):
    from tests.test_pi_updater import TODAY
    sk, pub = keys
    # The pipeline fixture installs obstacles-conus at a cycle we read back,
    # then publishes an advisory naming exactly that cycle.
    root = _published_store(tmp_path, keys, [])
    cycle = next(p["cycle"] for p in json.loads((root / "manifest.json").read_bytes())["packs"]
                 if p["id"] == "obstacles-conus")
    feed.publish(LocalStore(root), sk, _feed(2, [_adv(affects=[
        {"pack": "obstacles-conus", "cycles": [cycle]}])]), sign=signing.sign,
        log=lambda m: None)
    monkeypatch.setattr(cli, "PUBLIC_KEY", pub)
    monkeypatch.setattr(cli, "Updater", partial(Updater, today=TODAY))
    status = tmp_path / "status.json"
    monkeypatch.setattr("pyefis_data.config.default_status_path", lambda: status)
    data_yaml = tmp_path / "data.yaml"
    data_yaml.write_text(f"advisories: {'true' if enabled else 'false'}\n"
                         "packs: [airports-conus, obstacles-conus]\n")
    rc = cli.main(["--config", str(data_yaml), "--base-url", ORIGIN, "--root",
                   str(tmp_path / "pi"), "update", "--source", str(root)])
    assert rc == 0
    out = capsys.readouterr().out
    block = json.loads(status.read_text())["advisories"]
    if enabled:
        assert "ADVISORY MPA-2026-001 [warning]" in out
        assert block["active"][0]["applies"] == "yes"
    else:
        assert "ADVISORY" not in out and block == {"enabled": False}
        assert not (tmp_path / "pi" / adv.CACHE).exists()


def test_update_with_empty_feed_prints_nothing(tmp_path, keys, monkeypatch, capsys):
    from tests.test_pi_updater import TODAY
    root = _published_store(tmp_path, keys, [])
    monkeypatch.setattr(cli, "PUBLIC_KEY", keys[1])
    monkeypatch.setattr(cli, "Updater", partial(Updater, today=TODAY))
    status = tmp_path / "status.json"
    monkeypatch.setattr("pyefis_data.config.default_status_path", lambda: status)
    data_yaml = tmp_path / "data.yaml"
    data_yaml.write_text("advisories: true\npacks: [obstacles-conus]\n")
    rc = cli.main(["--config", str(data_yaml), "--base-url", ORIGIN, "--root",
                   str(tmp_path / "pi"), "update", "--source", str(root)])
    cap = capsys.readouterr()
    assert rc == 0 and "advis" not in (cap.out + cap.err).lower()
    block = json.loads(status.read_text())["advisories"]
    assert block["active"] == [] and block["error"] is None and block["sequence"] == 1


def test_a_broken_feed_never_fails_the_update(tmp_path, keys, monkeypatch, capsys):
    from tests.test_pi_updater import TODAY
    root = _published_store(tmp_path, keys, [])
    (root / "advisories.json").write_bytes(b"{tampered")
    monkeypatch.setattr(cli, "PUBLIC_KEY", keys[1])
    monkeypatch.setattr(cli, "Updater", partial(Updater, today=TODAY))
    monkeypatch.setattr("pyefis_data.config.default_status_path",
                        lambda: tmp_path / "status.json")
    data_yaml = tmp_path / "data.yaml"
    data_yaml.write_text("advisories: true\npacks: [obstacles-conus]\n")
    rc = cli.main(["--config", str(data_yaml), "--base-url", ORIGIN, "--root",
                   str(tmp_path / "pi"), "update", "--source", str(root)])
    assert rc == 0
    assert "could not fetch a verified advisory feed" in capsys.readouterr().err
    assert (tmp_path / "pi" / "obstacles").exists()
