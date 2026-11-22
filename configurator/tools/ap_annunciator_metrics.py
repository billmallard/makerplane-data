# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-6286: print the editor's AP_STRIP_METRICS table (public/editor.html)
# from the Qt metrics tools/ap_annunciator_oracle.py recorded in
# test/fixtures/ap_annunciator_pyefis.json. No Qt needed here.
#
#   python3 tools/ap_annunciator_metrics.py test/fixtures/ap_annunciator_pyefis.json
#
# The strip fits its bold font by horizontalAdvance, which is the sum of the
# hinted glyph advances plus the font's kern pairs, each rounded to whole
# pixels. Per family the table holds each glyph's font units (u); per glyph,
# its residual against Math.round(u * px / 2048) at each pixel size 1..160,
# which Qt's hinting keeps to 0 or +1, as a bitfield in hex, px 1 in the top
# bit of the first digit (d); the family's ascent and descent in font units
# (au, du) and their residuals the same way (a, s); and the kern pairs over
# the table's characters, in font units (k). Units are Qt's advances at
# 2048 px, where they are the font's own. A regenerated fixture with any other
# residual stops this script rather than writing a table the twin misreads.
# test/ap_annunciator_twin.test.mjs decodes it and checks it against the
# fixture, value for value.

import json
import math
import sys


def jsround(x):
    return math.floor(x + 0.5)


def main(path):
    with open(path) as f:
        m = json.load(f)["metrics"]
    fams = {}
    for fam, t in m["families"].items():
        units = t["units"]

        def enc(vals):
            bits = ""
            for r in vals:
                if r not in (0, 1):
                    raise SystemExit("%s: residual %d is not 0 or 1" % (fam, r))
                bits += str(r)
            bits += "0" * (-len(bits) % 4)
            return "".join("%x" % int(bits[i:i + 4], 2)
                           for i in range(0, len(bits), 4))
        d = [enc(int(row[3 + j]) - jsround(units[j] * row[0] / 2048)
                 for row in t["rows"]) for j in range(len(m["chars"]))]
        au, du = t["ascent_units"], t["descent_units"]
        a = enc(int(r[1]) - jsround(au * r[0] / 2048) for r in t["rows"])
        s = enc(int(r[2]) - jsround(du * r[0] / 2048) for r in t["rows"])
        fams[fam] = {"u": units, "d": d, "au": au, "du": du, "a": a, "s": s,
                     "k": t["kern"]}
    print("const AP_STRIP_METRICS = %s;" % json.dumps(
        {"chars": m["chars"], "families": fams}, separators=(",", ":")))


if __name__ == "__main__":
    main(sys.argv[1])
