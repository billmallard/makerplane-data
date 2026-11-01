#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Retro records (TV4): write and bind a pack-report/1 `retro` record for every
# live manifest entry published without one, ONE pack at a time: download it
# to $SCRATCH, check its sha256 against the signed manifest, run the kind's
# catalogue on it, upload the record, re-sign the manifest with the binding,
# write schema/<kind>.json (SCH1), delete the pack, next. The largest live
# pack is ~7.7 GB, so $SCRATCH never holds more than that.
#
# What it finds is published as found. A failing check is listed in the run
# summary ($OUT/<stamp>/retro-summary.json and the log) for a person to
# decide on; nothing here retracts, re-publishes or edits a pack.
#
# Re-runnable: an entry already bound is not pending, so a re-run does only
# what is left. Holds the same lock as entrypoint.sh, so it never runs
# alongside a terrain build. Start it after the nightly cyclical publish
# (09:20 UTC) has finished: a publish that overlaps a bind can drop that
# binding (the run reports it; a re-run fixes it).
set -euo pipefail

: "${R2_ENDPOINT:?set R2_ENDPOINT}"
: "${R2_ACCESS_KEY_ID:?set R2_ACCESS_KEY_ID}"
: "${R2_SECRET_ACCESS_KEY:?set R2_SECRET_ACCESS_KEY}"
: "${MINISIGN_SECRET_KEY:?set MINISIGN_SECRET_KEY (base64 signing blob = contents of keys/minisign.sec)}"

SCRATCH="${SCRATCH:-/data/retro-scratch}"
OUT="${OUT:-/data/retro-out}"
ONLY="${ONLY:-}"                                   # comma list of pack ids; empty = all unbound
CODE_REF="${CODE_REF:-dev}"
PYEFIS_REF="${PYEFIS_REF:-dev}"
R2_BUCKET="${R2_BUCKET:-makerplane-data}"
MANIFEST_URL="${MANIFEST_URL:-https://navdata.aerocommons.org/manifest.json}"
LOCK="${CLOUD_LOCK:-/data/.packtools-cloud.lock}"
export R2_ENDPOINT R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY MINISIGN_SECRET_KEY R2_BUCKET

mkdir -p "$(dirname "$LOCK")"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "ERROR: $LOCK is held: another packtools cloud job (a terrain build?) is running."
  echo "       Retro records do not run alongside it. Try again when it has finished."
  exit 3
fi

echo ">> retro records: scratch=$SCRATCH out=$OUT ref=$CODE_REF only=${ONLY:-all}"
DATA_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
if [ -f "$DATA_DIR/packtools/cli.py" ] && [ -f "$DATA_DIR/keys/minisign.pub" ]; then
  echo "   makerplane-data: using this checkout ($DATA_DIR)"
else
  DATA_DIR=/opt/makerplane-data
  rm -rf "$DATA_DIR"
  git clone --depth 1 -b "$CODE_REF" https://github.com/billmallard/makerplane-data.git "$DATA_DIR"
fi
echo "   makerplane-data @ $(git -C "$DATA_DIR" rev-parse HEAD 2>/dev/null || echo '?')"
rm -rf /opt/pyEfis
git clone --depth 1 -b "$PYEFIS_REF" https://github.com/billmallard/pyEfis.git /opt/pyEfis
echo "   pyEfis: $PYEFIS_REF @ $(git -C /opt/pyEfis rev-parse --short HEAD 2>/dev/null || echo '?')"

# ACC-001 (device acceptance) opens each pack with pyEfis's own reader. A
# retro record with it not-run says less, so fail now if the reader cannot load.
export PYEFIS_SRC_DIR=/opt/pyEfis/src
if ! PYTHONPATH="$PYEFIS_SRC_DIR" QT_QPA_PLATFORM=offscreen \
     python -c "import pyefis.instruments.ai.svs" 2>/tmp/svs-import.err; then
  echo "ERROR: pyEfis's SVS reader does not import here (ACC-001 cannot run):"
  tail -n 1 /tmp/svs-import.err
  exit 1
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$SCRATCH" "$OUT/$STAMP"
cd "$DATA_DIR"
rc=0
PYTHONPATH=. QT_QPA_PLATFORM=offscreen python -m packtools.cli retro \
  --scratch "$SCRATCH" --out "$OUT/$STAMP" --bucket "$R2_BUCKET" \
  ${ONLY:+--only "$ONLY"} || rc=$?
rm -rf "$SCRATCH"

echo ">> verifying the published manifest..."
curl -fsS "$MANIFEST_URL" -o /tmp/manifest.json
curl -fsS "${MANIFEST_URL}.minisig" -o /tmp/manifest.json.minisig
PYTHONPATH=. python -m packtools.cli verify /tmp/manifest.json --pub keys/minisign.pub | head -2
echo ">> summary: $OUT/$STAMP/retro-summary.json"
echo ">> DONE (exit $rc)"
exit $rc
