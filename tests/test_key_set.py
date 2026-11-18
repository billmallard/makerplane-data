# SPDX-License-Identifier: Apache-2.0
"""CERT16 (AER-5994): the device trusts a small key set, current + next.

A manifest signed by any key in the set verifies; one signed by a key outside
it is refused before any crypto runs, and nothing installs. Rotation is "start
signing with next"; revocation is "drop the key from the set". Procedure:
docs/signing_key.md. Test keypairs are generated per run; no secret is
committed.
"""

import json
import re
from pathlib import Path

import pytest

from packtools import signing
from pyefis_data import cli
from tests.test_pi_updater import TODAY, build_store, make_updater

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def keys():
    """(current, next, unlisted) keypairs, each a (SecretKey, public text)."""
    return tuple(signing.generate_keypair() for _ in range(3))


# --- signing.verify over a key set -------------------------------------------

def test_signature_by_next_key_verifies_against_the_set(keys):
    (_cur_sk, cur), (nxt_sk, nxt), _ = keys
    sig = signing.sign(b"manifest", nxt_sk, trusted_comment="next")
    assert signing.verify(b"manifest", sig, (cur, nxt)) == "next"


def test_signature_by_current_key_still_verifies_against_the_set(keys):
    (cur_sk, cur), (_nxt_sk, nxt), _ = keys
    sig = signing.sign(b"manifest", cur_sk, trusted_comment="current")
    assert signing.verify(b"manifest", sig, (cur, nxt)) == "current"


def test_signature_by_unlisted_key_is_refused(keys):
    (_c, cur), (_n, nxt), (out_sk, _out) = keys
    sig = signing.sign(b"manifest", out_sk)
    with pytest.raises(ValueError, match="does not match any trusted public key"):
        signing.verify(b"manifest", sig, (cur, nxt))


def test_listed_key_id_does_not_rescue_a_bad_signature(keys):
    """Key-id selection picks the key; the signature must still check out
    against it. A forged blob naming the next key's id is not accepted."""
    import base64
    import nacl.exceptions
    (_c, cur), (nxt_sk, nxt), (out_sk, _o) = keys
    forged = signing.sign(b"manifest", out_sk).splitlines()
    blob = base64.b64decode(forged[1])
    forged[1] = base64.b64encode(blob[:2] + nxt_sk.key_id + blob[10:]).decode()
    with pytest.raises(nacl.exceptions.BadSignatureError):
        signing.verify(b"manifest", "\n".join(forged) + "\n", (cur, nxt))


def test_empty_key_set_trusts_nothing(keys):
    (cur_sk, _cur), _, _ = keys
    with pytest.raises(ValueError, match="no trusted public key"):
        signing.verify(b"m", signing.sign(b"m", cur_sk), ())


# --- the updater, end to end -------------------------------------------------

def test_updater_installs_a_manifest_signed_by_the_next_key(tmp_path, keys):
    (_c, cur), nxt, _ = keys
    root, _pub = build_store(tmp_path, keypair=nxt)
    up = make_updater(tmp_path, (cur, nxt[1]), remote_root=root)
    up.update()
    assert not up.errors
    assert (tmp_path / "pi" / "navdata" / "2606" / "airports.sqlite").exists()


def test_updater_refuses_a_manifest_signed_by_an_unlisted_key(tmp_path, keys):
    (_c, cur), (_n, nxt), out = keys
    root, _pub = build_store(tmp_path, keypair=out)
    up = make_updater(tmp_path, (cur, nxt), remote_root=root)
    with pytest.raises(ValueError, match="does not match any trusted public key"):
        up.update()
    assert not (tmp_path / "pi" / "navdata").exists()     # nothing installed
    assert not (tmp_path / "pi" / "manifest.json").exists()  # nor cached


def test_rotation_walks_from_a_cache_signed_by_current(tmp_path, keys):
    """The day signing switches to next: the device holds a cache signed by
    current, the new manifest is signed by next and is newer. It installs, and
    the anti-rollback baseline (CERT3) is the re-verified current-key cache."""
    cur, nxt, _ = keys
    pubs = (cur[1], nxt[1])
    root, _ = build_store(tmp_path / "a", keypair=cur)
    up = make_updater(tmp_path, pubs, remote_root=root)
    up.update()
    cached = json.loads((tmp_path / "pi" / "manifest.json").read_bytes())

    root2, _ = build_store(tmp_path / "b", keypair=nxt)
    raw = json.loads((root2 / "manifest.json").read_bytes())
    raw["generated"] = "2099-01-01T00:00:00Z"       # strictly newer than the cache
    body = (json.dumps(raw, indent=2, sort_keys=True) + "\n").encode()
    (root2 / "manifest.json").write_bytes(body)
    (root2 / "manifest.json.minisig").write_text(signing.sign(body, nxt[0]), "ascii")

    up2 = make_updater(tmp_path, pubs, remote_root=root2)
    m = up2.fetch_manifest()
    assert m.generated == "2099-01-01T00:00:00Z" != cached["generated"]
    assert up2.rollback_refused is None


def test_revoked_key_is_refused_once_dropped_from_the_set(tmp_path, keys):
    """Revocation: a device whose set no longer lists the old key refuses a
    manifest that key signed, however valid the signature is."""
    cur, nxt, _ = keys
    root, _ = build_store(tmp_path, keypair=cur)
    up = make_updater(tmp_path, (nxt[1],), remote_root=root)
    with pytest.raises(ValueError):
        up.update()
    assert not (tmp_path / "pi" / "navdata").exists()


def test_cli_verify_uses_the_key_set(tmp_path, monkeypatch, keys):
    (_c, cur), nxt, out = keys
    root, _ = build_store(tmp_path / "n", keypair=nxt)
    bad, _ = build_store(tmp_path / "o", keypair=out)
    monkeypatch.setattr(cli, "PUBLIC_KEYS", (cur, nxt[1]))
    assert cli.main(["verify", str(root / "manifest.json")]) == 0
    assert cli.main(["verify", str(bad / "manifest.json")]) == 2


# --- the production set ------------------------------------------------------

def _payload(text: str) -> str:
    return signing._payload_line(text)


def test_production_set_starts_with_the_committed_key():
    """keys/minisign.pub is the key CI signs with today; it is the first
    entry of the set the device ships."""
    committed = (REPO / "keys" / "minisign.pub").read_text("ascii")
    assert _payload(cli.PUBLIC_KEYS[0]) == _payload(committed)


def test_production_set_is_small_parses_and_has_distinct_key_ids():
    assert 1 <= len(cli.PUBLIC_KEYS) <= 2              # current, and next once made
    ids = [signing.PublicKey.from_text(k).key_id for k in cli.PUBLIC_KEYS]
    assert len(set(ids)) == len(ids)

