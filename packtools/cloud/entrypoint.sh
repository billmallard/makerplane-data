#!/usr/bin/env bash
# Terrain pyramid build + publish. Idempotent: the mip build skips tiles already
# present (so the pyramid persists on the mounted NAS tree across runs), and each
# region upsert is independent + re-runnable. Exit non-zero if any region failed.
set -euo pipefail

# ---- required (env / .env) ----
: "${R2_ENDPOINT:?set R2_ENDPOINT}"
: "${R2_ACCESS_KEY_ID:?set R2_ACCESS_KEY_ID}"
: "${R2_SECRET_ACCESS_KEY:?set R2_SECRET_ACCESS_KEY}"
: "${MINISIGN_SECRET_KEY:?set MINISIGN_SECRET_KEY (base64 signing blob = contents of keys/minisign.sec)}"

# ---- optional (defaults) ----
EDITION="${EDITION:-2024ed}"
TILE_ROOT="${TILE_ROOT:-/data/glo30hgt}"
PACK_OUT="${PACK_OUT:-/data/pack-scratch}"
REGIONS="${REGIONS:-us-west us-central us-east us-south alaska canada-west canada-east mexico-central-america}"
JOBS="${JOBS:-0}"                                   # 0 = all cores
CODE_REF="${CODE_REF:-dev}"
URL_BASE="${URL_BASE:-https://navdata.aerocommons.org/packs}"
MANIFEST_URL="${MANIFEST_URL:-https://navdata.aerocommons.org/manifest.json}"
R2_BUCKET="${R2_BUCKET:-makerplane-data}"
export R2_ENDPOINT R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY MINISIGN_SECRET_KEY R2_BUCKET

echo ">> terrain build: tile_root=$TILE_ROOT edition=$EDITION jobs=${JOBS} ref=$CODE_REF"
if [ ! -d "$TILE_ROOT" ] || [ -z "$(ls -A "$TILE_ROOT" 2>/dev/null)" ]; then
  echo "ERROR: $TILE_ROOT missing/empty. On QNAP this almost always means the"
  echo "       volume was mounted with a symlink/UNC path instead of the native"
  echo "       /share/<POOL>/... path — see README (empty-volume gotcha)."
  exit 2
fi

# ---- code: fresh clone each run so map iterations are always current ----
for repo in pyEfis makerplane-data; do
  rm -rf "/opt/$repo"
  git clone --depth 1 -b "$CODE_REF" "https://github.com/billmallard/$repo.git" "/opt/$repo"
done

# ---- 1. mip pyramid (parallel; written into the mounted tree, persists) ----
echo ">> [1/3] building mip pyramid (jobs=$JOBS)..."
PYTHONPATH=/opt/pyEfis/src python /opt/pyEfis/tools/build_terrain_mips.py "$TILE_ROOT" -j "$JOBS"

# ---- 2. build + upload each region pack (re-signs the manifest per region) ----
cd /opt/makerplane-data
mkdir -p "$PACK_OUT"
fail=0
for r in $REGIONS; do
  echo ">> [2/3] region $r  $(date -u +%H:%M:%S)"
  if PYTHONPATH=. python -m packtools.cli make-terrain "$TILE_ROOT" \
        --edition "$EDITION" --only "$r" --out "$PACK_OUT" --no-compress \
        --url-base "$URL_BASE" --upload --bucket "$R2_BUCKET"; then
    rm -f "$PACK_OUT/packs/terrain-$r-$EDITION.pack"          # free scratch as we go
  else
    echo "!! FAILED: $r"; fail=1
  fi
done

# ---- 3. verify the published manifest (signature + that it parses) ----
echo ">> [3/3] verifying published manifest..."
curl -fsS "$MANIFEST_URL" -o /tmp/manifest.json
# verify reads the detached signature next to the manifest (<name>.minisig) --
# fetch it too, else verify throws FileNotFoundError and pipefail fails the run.
curl -fsS "${MANIFEST_URL}.minisig" -o /tmp/manifest.json.minisig
PYTHONPATH=. python -m packtools.cli verify /tmp/manifest.json --pub keys/minisign.pub | head -2

echo ">> DONE (exit $fail)"
exit $fail
