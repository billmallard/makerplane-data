#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
# AER-6498: terrain-only SVS fallback frames at the four preview poses
# (tools/export_svs_preview_patch.py SCENES), pyEfis dev (SVSSHADE2,
# terrain_shading default relief), svs_capture --offscreen on eglfs alongside
# the live pyEfis (no restart), 1920x1080 like the served webps.
set -u
PY=$1; D=$2
W=$HOME/bench-scratch/aer6498
mkdir -p $W/out; cd $W
[ -d dev ] || git clone -q https://github.com/billmallard/pyEfis.git dev
git -C dev fetch -q origin && git -C dev checkout -q --detach origin/dev
echo "dev=$(git -C dev rev-parse HEAD)" > out/revs.txt
TILES=$D/terrain/tiles; NASR=$D/navdata/current/airports.sqlite
WATER=$D/water/current/water.sqlite
while read name lat lon alt head; do
  ( cd dev && QT_QPA_PLATFORM=eglfs QT_QPA_EGLFS_KMS_CONFIG=$HOME/eglfs_hdmi.json \
      PYTHONPATH=$W/dev/src $PY tools/svs_capture.py \
      --out $W/out/$name.png --lat $lat --lon $lon --alt $alt \
      --heading $head --tiles $TILES --nasr $NASR --water $WATER \
      --offscreen --width 1920 --height 1080 --range 15 --terrain-only ) \
      > out/$name.log 2>&1
  echo "$name rc=$?"
done <<SCENES
coastal 34.4275 -119.8546 2500 0
mountains 39.20 -106.85 12500 150
approach 37.96933 -107.98168 10500 105
final 34.42721 -119.87481 611 89
SCENES
echo DONE
