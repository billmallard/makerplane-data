# SPDX-License-Identifier: Apache-2.0
"""packtools/cloud/entrypoint.sh requires EDITION. It used to default to
2024ed, which kept pointing a cloud build at that edition after it was
retracted (AER-3350, AER-3528). An unset EDITION must stop the run before it
clones, fetches or builds anything."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[1] / "packtools" / "cloud" / "entrypoint.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _run(tmp_path, **extra):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "R2_ENDPOINT": "https://r2.invalid",
        "R2_ACCESS_KEY_ID": "x",
        "R2_SECRET_ACCESS_KEY": "x",
        "MINISIGN_SECRET_KEY": "x",
        # Missing on purpose: a run that gets past the EDITION guard stops at
        # the TILE_ROOT guard (exit 2) before any clone or network call.
        "TILE_ROOT": str(tmp_path / "no-tiles"),
        **extra,
    }
    return subprocess.run(["bash", str(ENTRYPOINT)], env=env,
                          capture_output=True, text=True, timeout=30)


def test_unset_edition_fails_before_any_work(tmp_path):
    r = _run(tmp_path)
    assert r.returncode != 0
    assert "EDITION" in r.stderr
    assert ">> terrain build" not in r.stdout


def test_empty_edition_fails(tmp_path):
    r = _run(tmp_path, EDITION="")
    assert r.returncode != 0
    assert "EDITION" in r.stderr


def test_set_edition_passes_the_guard(tmp_path):
    # Positive control: with EDITION set the script reaches the TILE_ROOT
    # guard, so the failure above is the EDITION check and nothing earlier.
    r = _run(tmp_path, EDITION="2024edr1")
    assert r.returncode == 2
    assert "edition=2024edr1" in r.stdout
    assert "missing/empty" in r.stdout


def test_compose_has_no_edition_default():
    compose = (ENTRYPOINT.parent / "docker-compose.yml").read_text()
    line = next(l for l in compose.splitlines() if l.strip().startswith("EDITION:"))
    assert "${EDITION:?" in line


# --- retro.sh (TV4): one cloud job at a time, memory capped ------------------

RETRO = ENTRYPOINT.parent / "retro.sh"
_SECRETS = {"R2_ENDPOINT": "https://r2.invalid", "R2_ACCESS_KEY_ID": "x",
            "R2_SECRET_ACCESS_KEY": "x", "MINISIGN_SECRET_KEY": "x"}


def _held(path):
    import fcntl
    f = open(path, "w")
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return f


@pytest.mark.skipif(shutil.which("flock") is None, reason="needs flock")
def test_retro_does_not_start_while_a_terrain_build_holds_the_lock(tmp_path):
    lock = tmp_path / "cloud.lock"
    with _held(lock):
        r = subprocess.run(["bash", str(RETRO)], capture_output=True, text=True, timeout=30,
                           env={"PATH": os.environ.get("PATH", ""), "CLOUD_LOCK": str(lock),
                                **_SECRETS})
    assert r.returncode == 3, r.stdout + r.stderr
    assert "is held" in r.stdout and ">> retro records" not in r.stdout


@pytest.mark.skipif(shutil.which("flock") is None, reason="needs flock")
def test_terrain_build_does_not_start_while_retro_holds_the_lock(tmp_path):
    tiles = tmp_path / "tiles"
    tiles.mkdir()
    (tiles / "N39W105.hgt").write_bytes(b"\0")
    lock = tmp_path / "cloud.lock"
    with _held(lock):
        r = _run(tmp_path, EDITION="2024edr1", TILE_ROOT=str(tiles), CLOUD_LOCK=str(lock))
    assert r.returncode == 3 and "is held" in r.stdout


def test_retro_needs_the_signing_key(tmp_path):
    env = {"PATH": os.environ.get("PATH", ""), "CLOUD_LOCK": str(tmp_path / "l"), **_SECRETS}
    del env["MINISIGN_SECRET_KEY"]
    r = subprocess.run(["bash", str(RETRO)], capture_output=True, text=True, timeout=30, env=env)
    assert r.returncode != 0 and "MINISIGN_SECRET_KEY" in r.stderr


def test_retro_compose_caps_memory():
    compose = (ENTRYPOINT.parent / "docker-compose.retro.yml").read_text()
    assert 'mem_limit: "${RETRO_MEM:-4g}"' in compose
    assert 'memswap_limit: "${RETRO_MEM:-4g}"' in compose
    assert "retro.sh" in compose and "EDITION" not in compose.split("services:")[1]
