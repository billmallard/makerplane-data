# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-5812: print the editor's AP_FONT_METRICS table (public/editor.html) from
# the Qt metrics tools/autopilot_panel_oracle.py recorded in
# test/fixtures/autopilot_panel_pyefis.json. No Qt needed here.
#
#   python3 tools/autopilot_panel_metrics.py test/fixtures/autopilot_panel_pyefis.json
#
# fit_to_mask reads two Qt metrics: the mask's horizontalAdvance and its
# boundingRect height. Both are whole pixels, hinted and kerned, and depend
# only on the rounded pixel size. Each is stored as a base law plus one
# residual character per pixel size 1..400: the height's base is
# round(ascent * px) + round(descent * px), an advance's base is
# round(em * px) with em fitted over 300..400 px. The character is the
# residual + 2 as a digit. test/autopilot_panel_twin.test.mjs decodes the
# table and checks it against the fixture, value for value.

import json
import math
import sys

ASC, DESC = 1901 / 2048, 483 / 2048


def jsround(x):
    return math.floor(x + 0.5)


def main(path):
    m = json.load(open(path))["metrics"]
    cols = m["columns"]
    hi = cols.index("height")
    out = {}
    for fam, rows in m["families"].items():
        assert [r[0] for r in rows] == list(range(1, len(rows) + 1))
        ems, res = [], []
        base_h = [jsround(ASC * r[0]) + jsround(DESC * r[0]) for r in rows]
        res.append("".join(chr(48 + 2 + int(r[hi]) - b) for r, b in zip(rows, base_h)))
        for mask in m["masks"]:
            ci = cols.index(mask)
            big = [r for r in rows if r[0] >= 300]
            em = round(sum(r[ci] for r in big) / sum(r[0] for r in big), 5)
            ems.append(em)
            s = "".join(chr(48 + 2 + int(r[ci]) - jsround(em * r[0])) for r in rows)
            assert all("0" <= c <= "9" for c in s), (fam, mask)
            res.append(s)
        out[fam] = {"em": ems, "d": res}
    print("const AP_FONT_METRICS = " + json.dumps(
        {"masks": m["masks"], "families": out}, separators=(",", ":")) + ";")


if __name__ == "__main__":
    main(sys.argv[1])
