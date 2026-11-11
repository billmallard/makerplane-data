<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# `auspex` section fixture

`water-na.auspex.json` is an `auspex` section of the shape QA's emitter
writes as `auspex.json`, built by hand from the schema in
[docs/pack_report.md](../../../docs/pack_report.md) (approved 2026-11-01,
AER-4581). It is not output from Auspex and was not derived from its code:
the finding is invented, against the synthetic `water-na` pack that
`tests/test_report.py::_water_pack` builds. Used by the `--auspex` tests in
`tests/test_report.py`.
