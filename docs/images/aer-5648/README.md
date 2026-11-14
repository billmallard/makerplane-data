<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AER-5648: a schema-4 2612 procedures pack on the bench PROC page

Evidence for item 3 of the 0.6.0 release (AER-5648): the deployed pyEfis
reads a `schema_version` 4 `procedures-conus` 2612 pack before 2612 is
retracted and rebuilt (AER-5612).

- **Pack:** built locally from `dev` at `952e29f` (#207, #208, #209 merged)
  from the FAA's `CIFP_261126.zip`, with `pack_meta` embedded the way
  `packtool build-pack` does it (`schema_version` 4). Unsigned and never
  uploaded. sha256 `0c779156...9ce1e`.
- **Bench:** the Pi 5 (`10.110.10.199`), pyEfis checkout `a2d53d7` (`dev`),
  its own venv. Run off-screen (`QT_QPA_PLATFORM=offscreen`) under the bench
  lock. The running `pyefis.service` was not restarted or touched.
- **Files:** `2612s4_KSSF_approach_picker.png` and
  `2612s4_KSBA_departure_picker.png` are the PROC page's procedure picker,
  rendered by that pyEfis from the schema-4 pack (1:1, the Beelink geometry
  the PROC page tests use).

## Commands

```bash
# build (makerplane-data dev checkout)
curl -sSfLO https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_261126.zip
mkdir cifp2612 && (cd cifp2612 && unzip ../CIFP_261126.zip FAACIFP18)
python3 -c "
from packtools.build.procedures import build_procedures
from packtools import packmeta
from packtools.packmeta import PackMeta
p = build_procedures('cifp2612', 'procedures-2612-s4.sqlite')
m = PackMeta(id='procedures-conus', kind='procedures', cycle='2612',
             effective='2026-11-26', expires='2026-12-24',
             attribution='FAA CIFP (public domain)')
m.schema_version = packmeta.detect_content_schema_version('procedures', p)
packmeta.embed_sqlite(p, m)"

# bench (pytest is installed to a /tmp target, not the pyEfis venv)
python -m pip install --target /tmp/aer5648/site pytest pytest-qt
cd /tmp/aer5648 && flock -w 900 /tmp/pyefis-bench.lock sh -c '
  TAG=2612s4 PACK=/tmp/aer5648/procedures-2612-s4.sqlite OUT=/tmp/aer5648/out \
  PYEFIS_DIR=$HOME/pyEfis PYTHONPATH=/tmp/aer5648/site:$HOME/pyEfis:$HOME/pyEfis/src \
  PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  $HOME/pyEfis/.venv/bin/python -m pytest -p no:cacheprovider -p no:warnings -q -s \
    --rootdir=/tmp/aer5648 test_proc_pack_bench.py
  PYTHONPATH=$HOME/pyEfis/src $HOME/pyEfis/.venv/bin/python sweep.py \
    /tmp/aer5648/procedures-2612-s4.sqlite 2612s4'
```

For the A/B baseline, run the same commands with
`PACK=/data/makerplane-data/procedures/current/procedures.sqlite` (the live
2611 pack, schema 3).
