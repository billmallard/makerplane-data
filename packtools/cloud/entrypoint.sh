#!/usr/bin/env bash
# Terrain pyramid + water-mask build + publish. Idempotent: the mip and mask
# builds skip files already present (so both persist on the mounted NAS tree
# across runs), and each region upsert is independent + re-runnable. Exit
# non-zero if the mask build or any region failed.
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
WATER_PACK_ID="${WATER_PACK_ID:-water-na}"         # water.sqlite source for step 2b
WATER_CACHE="${WATER_CACHE:-/data/water-cache}"    # persists; re-runs reuse a sha-matching copy
export R2_ENDPOINT R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY MINISIGN_SECRET_KEY R2_BUCKET

echo ">> terrain build: tile_root=$TILE_ROOT edition=$EDITION jobs=${JOBS} ref=$CODE_REF"
if [ ! -d "$TILE_ROOT" ] || [ -z "$(ls -A "$TILE_ROOT" 2>/dev/null)" ]; then
  echo "ERROR: $TILE_ROOT missing/empty. On QNAP this almost always means the"
  echo "       volume was mounted with a symlink/UNC path instead of the native"
  echo "       /share/<POOL>/... path — see README (empty-volume gotcha)."
  exit 2
fi

# ---- code: pyEfis is cloned fresh each run so map iterations are always
#      current. makerplane-data: when this script runs from inside a checkout
#      (the self-hosted runner path), use THAT checkout, so the packtools code
#      is the same revision as this script. In the Docker image the script is
#      copied alone, so clone it at $CODE_REF as before. --
DATA_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
if [ -f "$DATA_DIR/packtools/cli.py" ] && [ -f "$DATA_DIR/keys/minisign.pub" ]; then
  echo "   makerplane-data: using this checkout ($DATA_DIR, $(git -C "$DATA_DIR" rev-parse --short HEAD 2>/dev/null || echo '?'))"
  # CODE_REF here names the makerplane-data ref the caller checked out;
  # pyEfis has no `main`, so it takes its own ref.
  PYEFIS_REF="${PYEFIS_REF:-dev}"
else
  DATA_DIR=/opt/makerplane-data
  PYEFIS_REF="${PYEFIS_REF:-$CODE_REF}"
  rm -rf "$DATA_DIR"
  git clone --depth 1 -b "$CODE_REF" https://github.com/billmallard/makerplane-data.git "$DATA_DIR"
fi
rm -rf /opt/pyEfis
git clone --depth 1 -b "$PYEFIS_REF" https://github.com/billmallard/pyEfis.git /opt/pyEfis
echo "   pyEfis: $PYEFIS_REF @ $(git -C /opt/pyEfis rev-parse --short HEAD 2>/dev/null || echo '?')"

# ---- 0. water.sqlite for step 2b: the published water pack IS the sqlite
#         file. Fetched through the signed manifest (signature + sha256) and
#         cached on the mount. Up front so a missing source fails in seconds,
#         not after the pyramid build. --
echo ">> [0/5] fetching $WATER_PACK_ID (water.sqlite source for masks)..."
WATER_DB="$(cd "$DATA_DIR" && PYTHONPATH=. python -m packtools.cli fetch-pack \
  "$WATER_PACK_ID" --dest "$WATER_CACHE" --manifest-url "$MANIFEST_URL" \
  --pub keys/minisign.pub | tail -n 1)"
[ -s "$WATER_DB" ] || { echo "ERROR: fetch-pack gave no water_db path"; exit 1; }
echo "   water_db=$WATER_DB"

# ---- 1. mip pyramid (parallel; written into the mounted tree, persists) ----
echo ">> [1/5] building mip pyramid (jobs=$JOBS)..."
PYTHONPATH=/opt/pyEfis/src python /opt/pyEfis/tools/build_terrain_mips.py "$TILE_ROOT" -j "$JOBS"

# ---- 2. coarse mosaic: one whole-extent stitch per level from the mip tree.
#         Fast (seconds -- it stitches the already-built .mip tiles), written
#         into $TILE_ROOT/.mip/mosaic/, persists on the NAS like the pyramid.
#         Renderer TileCache.get_mosaic() reads it for constant-time wide zoom. --
echo ">> [2/5] stitching coarse terrain mosaic..."
PYTHONPATH=/opt/pyEfis/src python /opt/pyEfis/tools/build_terrain_mosaic.py "$TILE_ROOT" --levels 4 5 6

# ---- 2b. water masks (MP10a, pyEfis tools/build_water_masks.py): rasterize
#          water.sqlite onto the mip (L1-6) and mosaic (L4-6) grids and write
#          a `.wmask` sibling beside each `.hgt`. Runs after 1 and 2 because it
#          needs both grids. REQUIRED: a missing tool or a failed build fails
#          the run (set -e). It used to be best-effort, which turned "no
#          water_db argument" into a successful, signed, maskless edition
#          (AER-2287). Every tile is masked, whatever $REGIONS says: the
#          tool's closing pack_check scans the whole tree and ignores --only,
#          so a region-limited mask build would fail its own self-check.
#          Existing masks are skipped (no --force), so only the first run
#          pays the full cost; re-runs just fill gaps. --
WATER_MASK_TOOL="/opt/pyEfis/tools/build_water_masks.py"
[ -f "$WATER_MASK_TOOL" ] || { echo "ERROR: $WATER_MASK_TOOL not found on pyEfis $PYEFIS_REF"; exit 1; }
echo ">> [2b/5] building water masks (jobs=$JOBS)..."
PYTHONPATH=/opt/pyEfis/src python "$WATER_MASK_TOOL" "$TILE_ROOT" "$WATER_DB" -j "$JOBS"

# ---- 3. build + upload each region pack (re-signs the manifest per region) --
# Packs ship COMPRESSED (DEFLATE): ~50% smaller on R2 and on the wire, at the
# cost of build CPU + a one-time Pi decompress on pull. (Flipped from
# --no-compress per docs/terrain.md's pre-release footprint decision.)
cd "$DATA_DIR"
mkdir -p "$PACK_OUT"
fail=0
for r in $REGIONS; do
  echo ">> [3/5] region $r  $(date -u +%H:%M:%S)"
  if PYTHONPATH=. python -m packtools.cli make-terrain "$TILE_ROOT" \
        --edition "$EDITION" --only "$r" --out "$PACK_OUT" \
        --url-base "$URL_BASE" --require-wmask --upload --bucket "$R2_BUCKET"; then
    rm -f "$PACK_OUT/packs/terrain-$r-$EDITION.pack"          # free scratch as we go
  else
    echo "!! FAILED: $r"; fail=1
  fi
done

# ---- 4. build + upload the single national mosaic pack (compressed). Works for
#         any region combination, so it is one pack (region tag 'mosaic'); a
#         device opts in with `packs: [terrain-mosaic]`. --
echo ">> [4/5] mosaic pack  $(date -u +%H:%M:%S)"
if PYTHONPATH=. python -m packtools.cli make-terrain "$TILE_ROOT" \
      --edition "$EDITION" --mosaic --out "$PACK_OUT" \
      --url-base "$URL_BASE" --require-wmask --upload --bucket "$R2_BUCKET"; then
  rm -f "$PACK_OUT/packs/terrain-mosaic-$EDITION.pack"
else
  echo "!! FAILED: mosaic pack"; fail=1
fi

# ---- 5. verify the published manifest (signature + that it parses) ----
echo ">> [5/5] verifying published manifest..."
curl -fsS "$MANIFEST_URL" -o /tmp/manifest.json
# verify reads the detached signature next to the manifest (<name>.minisig) --
# fetch it too, else verify throws FileNotFoundError and pipefail fails the run.
curl -fsS "${MANIFEST_URL}.minisig" -o /tmp/manifest.json.minisig
PYTHONPATH=. python -m packtools.cli verify /tmp/manifest.json --pub keys/minisign.pub | head -2

echo ">> DONE (exit $fail)"
exit $fail
