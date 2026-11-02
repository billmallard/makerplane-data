#!/usr/bin/env bash
# Terrain pyramid + water-mask build + publish. Idempotent: the mip and mask
# builds skip files already present (so both persist on the mounted NAS tree
# across runs), and each region upsert is independent + re-runnable (a region
# already published at $EDITION is skipped). Exit non-zero if the mask build
# or any region failed.
#
# Publish gate (TV3, AER-4101): make-terrain --upload writes each pack's
# pack-report/1 test record BEFORE the manifest is signed, and a pack whose
# blocking checks fail is not published -- its record goes to
# reports/rejected/ instead. The gate needs:
#   pyEfis's SVS reader importable (PyQt6 + libGL): ACC-001, the device
#                   acceptance probe, is blocking. Checked before the build.
#   build notes     only from a person, for this edition: the file
#                   $PACK_OUT/build-notes-$EDITION.txt (KEY=TEXT lines or a
#                   JSON object; KEY is CHECK or terrain-<region>:CHECK).
#                   A check that warns with no note refuses its pack.
#
# Stage mode (STAGE_ONLY=1, AER-4595): build every pack but upload nothing and
# sign nothing. The packs stay in $PACK_OUT/packs/, each is diffed against the
# published edition $PREV_EDITION (packtool wmask-diff), and the publish gate's
# checks are previewed (packtool report). No R2 or signing secret is read.
# A publish is a second run with STAGE_ONLY unset and the same EDITION.
# FORCE_WMASK=1 rebuilds every .wmask (build_water_masks.py --force); it is
# refused outside stage mode, so rebuilt masks are always seen before they ship.
set -euo pipefail

STAGE_ONLY="${STAGE_ONLY:-0}"
FORCE_WMASK="${FORCE_WMASK:-0}"
if [ "$FORCE_WMASK" = 1 ] && [ "$STAGE_ONLY" != 1 ]; then
  echo "ERROR: FORCE_WMASK=1 needs STAGE_ONLY=1: rebuild and verify the masks in a stage"
  echo "       run, then publish them with a second run that does not force."
  exit 2
fi

# ---- required (env / .env) ----
if [ "$STAGE_ONLY" != 1 ]; then
  : "${R2_ENDPOINT:?set R2_ENDPOINT}"
  : "${R2_ACCESS_KEY_ID:?set R2_ACCESS_KEY_ID}"
  : "${R2_SECRET_ACCESS_KEY:?set R2_SECRET_ACCESS_KEY}"
  : "${MINISIGN_SECRET_KEY:?set MINISIGN_SECRET_KEY (base64 signing blob = contents of keys/minisign.sec)}"
else
  # What the staged packs are diffed against: the edition now published.
  : "${PREV_EDITION:?set PREV_EDITION (the published terrain edition to diff against, e.g. 2024edr1)}"
  # Unset so no step below can reach R2 or sign, whatever .env holds.
  unset R2_ENDPOINT R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY MINISIGN_SECRET_KEY
fi
# No default: an edition id is published to the live manifest, so the caller
# names it every run. A default goes stale -- it pointed at 2024ed after that
# edition was retracted (AER-3350, AER-3528).
: "${EDITION:?set EDITION (terrain edition id to build + publish, e.g. 2024edr1)}"

# ---- optional (defaults) ----
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
# Stage mode: "region=TILE:LEVEL:EDGES" border spot checks (packtool wmask-diff
# --border); each runs on that region's staged pack only.
WMASK_BORDERS="${WMASK_BORDERS:-us-central=N27W097:5:SE}"
if [ "$STAGE_ONLY" = 1 ]; then
  UPLOAD_ARGS=()
  MODE="STAGE ONLY (no upload, no signing; diff against $PREV_EDITION)"
else
  export R2_ENDPOINT R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY MINISIGN_SECRET_KEY
  UPLOAD_ARGS=(--upload --bucket "$R2_BUCKET")
  MODE="publish"
fi
export R2_BUCKET

echo ">> terrain build: tile_root=$TILE_ROOT edition=$EDITION jobs=${JOBS} ref=$CODE_REF"
echo "   mode: $MODE"
if [ ! -d "$TILE_ROOT" ] || [ -z "$(ls -A "$TILE_ROOT" 2>/dev/null)" ]; then
  echo "ERROR: $TILE_ROOT missing/empty. On QNAP this almost always means the"
  echo "       volume was mounted with a symlink/UNC path instead of the native"
  echo "       /share/<POOL>/... path — see README (empty-volume gotcha)."
  exit 2
fi
# One packtools cloud job at a time on this /data: retro.sh (TV4) holds the
# same lock, and both rewrite the manifest.
LOCK="${CLOUD_LOCK:-/data/.packtools-cloud.lock}"
mkdir -p "$(dirname "$LOCK")"
exec 9>"$LOCK"
flock -n 9 || { echo "ERROR: $LOCK is held: another packtools cloud job (retro records?) is running."; exit 3; }

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

# ---- publish-gate preflight: the device acceptance probe (ACC-001, blocking)
#      opens each pack with pyEfis's own SVS reader, which needs PyQt6 and
#      libGL. Fail now, not after hours of building, if it cannot load. --
export PYEFIS_SRC_DIR=/opt/pyEfis/src
if ! PYTHONPATH="$PYEFIS_SRC_DIR" QT_QPA_PLATFORM=offscreen \
     python -c "import pyefis.instruments.ai.svs" 2>/tmp/svs-import.err; then
  echo "ERROR: pyEfis's SVS reader does not import here, so the publish gate's device"
  echo "       acceptance check (ACC-001, blocking) cannot run and would refuse every pack."
  echo "       Install PyQt6 (pip) and libGL/libEGL (apt: libgl1 libegl1). Import error:"
  tail -n 1 /tmp/svs-import.err
  exit 1
fi
NOTES_FILE="$PACK_OUT/build-notes-$EDITION.txt"
GATE_ARGS=()     # origin label: make_terrain.TERRAIN_ORIGIN (federal, AER-4101)
if [ -f "$NOTES_FILE" ]; then
  echo "   build notes: $NOTES_FILE"
  GATE_ARGS+=(--notes-file "$NOTES_FILE")
else
  echo "   build notes: none ($NOTES_FILE absent); a check that warns refuses its pack"
fi

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
#          pays the full cost; re-runs just fill gaps. FORCE_WMASK=1 (stage
#          mode only) rebuilds all of them: a mask-builder fix (AER-4594)
#          changes masks that already exist. An interrupted forced run leaves
#          old and new masks mixed, so re-run it forced, not plain. --
WATER_MASK_TOOL="/opt/pyEfis/tools/build_water_masks.py"
[ -f "$WATER_MASK_TOOL" ] || { echo "ERROR: $WATER_MASK_TOOL not found on pyEfis $PYEFIS_REF"; exit 1; }
MASK_ARGS=()
[ "$FORCE_WMASK" = 1 ] && MASK_ARGS+=(--force)
echo ">> [2b/5] building water masks (jobs=$JOBS${MASK_ARGS[@]+ ${MASK_ARGS[*]}})..."
PYTHONPATH=/opt/pyEfis/src python "$WATER_MASK_TOOL" "$TILE_ROOT" "$WATER_DB" -j "$JOBS" \
  ${MASK_ARGS[@]+"${MASK_ARGS[@]}"}

# Stage mode: diff one staged pack against its published edition and preview
# the publish gate's checks on it. Never fatal on its own (the run carries on
# to the next pack); a failure sets fail=1 and lands in the stage summary.
STAGE_SUMMARY="$PACK_OUT/stage-$EDITION-summary.txt"
stage_check() {   # stage_check <pack id> <region key or "mosaic">
  local id="$1" key="$2" pack="$PACK_OUT/packs/$1-$EDITION.pack"
  local log="$PACK_OUT/stage-$EDITION-$id.txt" borders=() spec ok=PASS
  for spec in $WMASK_BORDERS; do
    [ "${spec%%=*}" = "$key" ] && borders+=(--border "${spec#*=}")
  done
  {
    echo "== $id $EDITION: $(sha256sum "$pack" | cut -d' ' -f1)  $(stat -c %s "$pack") bytes"
    echo "-- packtool wmask-diff (against $URL_BASE/$id-$PREV_EDITION.pack)"
  } > "$log"
  PYTHONPATH=. python -m packtools.cli wmask-diff "$pack" \
      --against "$URL_BASE/$id-$PREV_EDITION.pack" ${borders[@]+"${borders[@]}"} \
      >> "$log" 2>&1 || ok=FAIL
  # The gate reads the previous edition's record; fetch the published one.
  local prev_rec="$PACK_OUT/prev-$id-$PREV_EDITION.report.json"
  echo "-- packtool report (publish-gate preview, previous = $PREV_EDITION record)" >> "$log"
  if curl -fsS -A makerplane-data-stage "${URL_BASE%/packs}/reports/$id/$PREV_EDITION/report.json" \
        -o "$prev_rec" >> "$log" 2>&1; then
    PYTHONPATH=. python -m packtools.cli report "$pack" --previous "$prev_rec" \
        --origin federal --out "$PACK_OUT/stage-$EDITION-$id.report.json" \
        ${GATE_ARGS[@]+"${GATE_ARGS[@]}"} >> "$log" 2>&1 || ok=FAIL
  else
    echo "!! no published record for $id $PREV_EDITION" >> "$log"; ok=FAIL
  fi
  cat "$log"
  echo "$ok  $id  ($log)" >> "$STAGE_SUMMARY"
  [ "$ok" = PASS ] || fail=1
}

# ---- 3. build + upload each region pack (re-signs the manifest per region) --
# Packs ship COMPRESSED (DEFLATE): ~50% smaller on R2 and on the wire, at the
# cost of build CPU + a one-time Pi decompress on pull. (Flipped from
# --no-compress per docs/terrain.md's pre-release footprint decision.)
cd "$DATA_DIR"
mkdir -p "$PACK_OUT"
fail=0
[ "$STAGE_ONLY" = 1 ] && : > "$STAGE_SUMMARY"
for r in $REGIONS; do
  echo ">> [3/5] region $r  $(date -u +%H:%M:%S)"
  if PYTHONPATH=. python -m packtools.cli make-terrain "$TILE_ROOT" \
        --edition "$EDITION" --only "$r" --out "$PACK_OUT" \
        --url-base "$URL_BASE" --require-wmask ${UPLOAD_ARGS[@]+"${UPLOAD_ARGS[@]}"} \
        ${GATE_ARGS[@]+"${GATE_ARGS[@]}"}; then
    if [ "$STAGE_ONLY" = 1 ]; then
      stage_check "terrain-$r" "$r"                             # staged packs are kept
    else
      rm -f "$PACK_OUT/packs/terrain-$r-$EDITION.pack"        # free scratch as we go
    fi
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
      --url-base "$URL_BASE" --require-wmask ${UPLOAD_ARGS[@]+"${UPLOAD_ARGS[@]}"} \
      ${GATE_ARGS[@]+"${GATE_ARGS[@]}"}; then
  if [ "$STAGE_ONLY" = 1 ]; then
    stage_check terrain-mosaic mosaic
  else
    rm -f "$PACK_OUT/packs/terrain-mosaic-$EDITION.pack"
  fi
else
  echo "!! FAILED: mosaic pack"; fail=1
fi

if [ "$STAGE_ONLY" = 1 ]; then
  echo ">> [5/5] STAGE ONLY: nothing uploaded, nothing signed. Packs: $PACK_OUT/packs/"
  [ -f "$STAGE_SUMMARY" ] && cat "$STAGE_SUMMARY"
  echo ">> DONE (exit $fail)"
  exit $fail
fi

# ---- 5. verify the published manifest (signature + that it parses). A
#         post-publish sanity check only: it runs after upload, so it cannot
#         block. The blocking checks are the publish gate inside step 3/4. --
echo ">> [5/5] verifying published manifest..."
curl -fsS "$MANIFEST_URL" -o /tmp/manifest.json
# verify reads the detached signature next to the manifest (<name>.minisig) --
# fetch it too, else verify throws FileNotFoundError and pipefail fails the run.
curl -fsS "${MANIFEST_URL}.minisig" -o /tmp/manifest.json.minisig
PYTHONPATH=. python -m packtools.cli verify /tmp/manifest.json --pub keys/minisign.pub | head -2

echo ">> DONE (exit $fail)"
exit $fail
