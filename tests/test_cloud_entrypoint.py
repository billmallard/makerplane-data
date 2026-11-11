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


def test_compose_passes_stage_mode_into_the_container():
    # Compose forwards a shell variable only if `environment:` names it; a
    # dropped STAGE_ONLY would turn a stage run into a publish.
    compose = (ENTRYPOINT.parent / "docker-compose.yml").read_text()
    for var in ("STAGE_ONLY", "FORCE_WMASK", "PREV_EDITION", "WMASK_BORDERS"):
        assert f'{var}:' in compose and f'"${{{var}:-' in compose, var


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


# --- stage mode (AER-4595): build and verify, never upload or sign -----------

def _stage_env(tmp_path, **extra):
    env = {"PATH": os.environ.get("PATH", ""), "EDITION": "2024edr2",
           "TILE_ROOT": str(tmp_path / "no-tiles"), **extra}
    return subprocess.run(["bash", str(ENTRYPOINT)], env=env,
                          capture_output=True, text=True, timeout=30)


def test_force_wmask_is_refused_outside_stage_mode(tmp_path):
    r = _run(tmp_path, EDITION="2024edr2", FORCE_WMASK="1")
    assert r.returncode == 2
    assert "FORCE_WMASK=1 needs STAGE_ONLY=1" in r.stdout
    assert ">> terrain build" not in r.stdout


def test_stage_mode_needs_no_secrets_but_names_the_edition_it_diffs_against(tmp_path):
    r = _stage_env(tmp_path, STAGE_ONLY="1")
    assert r.returncode != 0 and "PREV_EDITION" in r.stderr
    r = _stage_env(tmp_path, STAGE_ONLY="1", FORCE_WMASK="1", PREV_EDITION="2024edr1")
    # Past every guard with no R2 or signing secret set: stops at TILE_ROOT.
    assert r.returncode == 2 and "missing/empty" in r.stdout
    assert "mode: STAGE ONLY (no upload, no signing; diff against 2024edr1)" in r.stdout


def test_publish_mode_still_needs_the_secrets(tmp_path):
    r = _stage_env(tmp_path, PREV_EDITION="2024edr1")
    assert r.returncode != 0 and "R2_ENDPOINT" in r.stderr


def _stage_check_src():
    text = ENTRYPOINT.read_text()
    start = text.index("stage_check() {")
    return text[start:text.index("\n}\n", start) + 3]


def test_stage_check_diffs_against_the_published_pack_and_logs_it(tmp_path):
    # Runs the real stage_check from entrypoint.sh on local packs: the
    # "published" side is a path, and curl is stubbed to copy the record.
    import sys
    from tests.test_wmask_diff import _open_water, _seam_band, _write

    out, pub = tmp_path / "out", tmp_path / "pub"
    (out / "packs").mkdir(parents=True)
    (pub / "reports" / "terrain-us-central" / "2024edr1").mkdir(parents=True)
    (pub / "packs").mkdir()
    _write(pub / "packs" / "terrain-us-central-2024edr1.pack", "2024edr1", _seam_band)
    _write(out / "packs" / "terrain-us-central-2024edr2.pack", "2024edr2", _open_water)
    from packtools import report
    prev = report.build_report(pub / "packs" / "terrain-us-central-2024edr1.pack", probe=False)
    (pub / "reports" / "terrain-us-central" / "2024edr1" / "report.json").write_bytes(
        report.to_bytes(prev))
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "python").symlink_to(sys.executable)
    (bin_ / "curl").write_text(          # curl -fsS -A UA <url> -o <dest>
        '#!/bin/sh\nfor a; do case "$prev" in -o) dest="$a";; esac; '
        'case "$a" in /*reports*) src="$a";; esac; prev="$a"; done\n'
        'cp "$src" "$dest"\n')
    (bin_ / "curl").chmod(0o755)
    script = (f"set -uo pipefail\nfail=0\nEDITION=2024edr2\nPREV_EDITION=2024edr1\n"
              f"PACK_OUT={out}\nURL_BASE={pub}/packs\nGATE_ARGS=()\n"
              f"WMASK_BORDERS='us-central=N27W097:5:SE us-west=N47W123:5:W'\n"
              f"STAGE_SUMMARY=$PACK_OUT/stage-$EDITION-summary.txt\n"
              f"{_stage_check_src()}\nstage_check terrain-us-central us-central\n"
              f"echo fail=$fail\n")
    root = ENTRYPOINT.parents[2]
    r = subprocess.run(["bash", "-c", script], cwd=root, capture_output=True, text=True,
                       timeout=120, env={"PATH": f"{bin_}:{os.environ['PATH']}",
                                         "PYEFIS_SRC_DIR": str(tmp_path / "no-pyefis")})
    log = (out / "stage-2024edr2-terrain-us-central.txt").read_text()
    assert "-- packtool wmask-diff" in log
    assert "border N27W097:5:SE" in log and "N47W123" not in log     # own region only
    assert "staged     N 113/113 S 113/113 E 113/113 W 113/113   OK" in log
    assert "OK: non-mask members identical" in log
    assert "-- packtool report" in log
    # No pyEfis reader here, so device acceptance blocks: the preview says so
    # and the pack is marked FAIL in the summary rather than passing quietly.
    assert "BLOCKS PUBLISH" in log
    summary = (out / "stage-2024edr2-summary.txt").read_text()
    assert summary.startswith("FAIL  terrain-us-central")
    assert "fail=1" in r.stdout
