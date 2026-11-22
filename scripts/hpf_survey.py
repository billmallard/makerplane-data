# SPDX-License-Identifier: Apache-2.0
"""Survey of the NASR holding-pattern file (HPF) for HOLDFIX3 (AER-6323).

Prints every number quoted in docs/hpf_holds.md: field fill rates and
vocabularies, the altitude shapes, how many holds join to a fix or navaid
of the same cycle, the enroute and arrival subset, and a whole-population
comparison against the hold legs (HM/HA/HF) in the same cycle's CIFP.

CIFP is the same agency's product, so agreement shows the two are
consistent, not that either is right; errors made upstream of both pass.

    python3 scripts/hpf_survey.py --hpf 29_Oct_2026_HPF_CSV.zip \\
        --fix 29_Oct_2026_FIX_CSV.zip --nav 29_Oct_2026_NAV_CSV.zip \\
        [--cifp FAACIFP18]

The zips come from nfdc.faa.gov/webContent/28DaySub/extra/; FAACIFP18 is
the member of the cycle's CIFP_<YYMMDD>.zip.
"""

from __future__ import annotations

import argparse
import collections
import csv
import io
import re
import zipfile

# Charts on which a hold is enroute or arrival rather than approach or
# departure (HPF_CHRT.CHARTING_TYPE_DESC).
ENROUTE_CHARTS = frozenset({"ENROUTE LOW", "ENROUTE HIGH", "STAR", "AREA",
                            "SPECIAL ENROUTE", "MILITARY STAR"})


def read(zpath: str, member: str) -> list[dict[str, str]]:
    with zipfile.ZipFile(zpath) as z, z.open(member) as fh:
        return list(csv.DictReader(io.TextIOWrapper(fh, encoding="latin-1")))


def key(r: dict[str, str]) -> tuple[str, str]:
    return r["HP_NAME"], r["HP_NO"]


def survey(args) -> None:
    base = read(args.hpf, "HPF_BASE.csv")
    spd = read(args.hpf, "HPF_SPD_ALT.csv")
    charts = collections.defaultdict(set)
    for r in read(args.hpf, "HPF_CHRT.csv"):
        charts[key(r)].add(r["CHARTING_TYPE_DESC"])

    print(f"holds (HPF_BASE rows): {len(base)}")
    for col in ("AZIMUTH", "TURN_DIRECTION", "HOLD_DIRECTION", "NAV_TYPE"):
        print(f"  {col}: {collections.Counter(r[col] for r in base).most_common()}")
    dist = sum(1 for r in base if r["LEG_LENGTH_DIST"].strip())
    print(f"  LEG_LENGTH_DIST filled {dist}, empty {len(base) - dist}")
    print(f"  at a fix {sum(1 for r in base if r['FIX_ID'])}, "
          f"at a navaid only {sum(1 for r in base if not r['FIX_ID'])}")
    print(f"  no HPF_CHRT row: {sum(1 for r in base if not charts[key(r)])}")

    shapes = collections.Counter(re.sub(r"\d+", "9", r["ALTITUDE"]) for r in spd)
    print(f"HPF_SPD_ALT rows: {len(spd)}, ALTITUDE shapes {shapes.most_common()}")
    print(f"  SPEED_RANGE: {collections.Counter(r['SPEED_RANGE'] for r in spd).most_common()}")
    odd = [r for r in spd if not re.fullmatch(r"\d+/\d+", r["ALTITUDE"])]
    print(f"  not MIN/MAX: {len(odd)} "
          f"({collections.Counter(r['ALTITUDE'] for r in odd).most_common()})")

    fixes = {(r["FIX_ID"], r["ICAO_REGION_CODE"]) for r in read(args.fix, "FIX_BASE.csv")}
    navs = {(r["NAV_ID"], r["NAV_TYPE"], r["STATE_CODE"], r["COUNTRY_CODE"])
            for r in read(args.nav, "NAV_BASE.csv")}
    miss = collections.Counter()
    for r in base:
        if r["FIX_ID"]:
            if (r["FIX_ID"], r["ICAO_REGION_CODE"]) not in fixes:
                miss["fix " + r["ICAO_REGION_CODE"]] += 1
        elif (r["NAV_ID"], r["NAV_TYPE"], r["STATE_CODE"], r["COUNTRY_CODE"]) not in navs:
            miss["navaid " + r["NAV_TYPE"]] += 1
    print(f"join to FIX_BASE (id, region) / NAV_BASE (id, type, state, country): "
          f"{len(base) - sum(miss.values())} of {len(base)}; misses {dict(miss)}")

    enr = [r for r in base if charts[key(r)] & ENROUTE_CHARTS]
    d = sum(1 for r in enr if r["LEG_LENGTH_DIST"].strip())
    print(f"enroute/arrival subset: {len(enr)} (distance {d}, no distance {len(enr) - d})")

    if args.cifp:
        cifp_compare(args.cifp, base)


def cifp_compare(path: str, base: list[dict[str, str]]) -> None:
    from packtools import arinc424
    idx = arinc424.build_fix_index(path)
    legs = [leg for leg in arinc424.iter_procedure_legs(path, idx)
            if leg.path_term in ("HM", "HA", "HF")]
    by_fix = collections.defaultdict(list)
    for r in base:
        by_fix[r["FIX_ID"] or r["NAV_ID"]].append(r)
    seen, st = set(), collections.Counter()
    for leg in legs:
        k = (leg.fix_id, leg.course, leg.turn_dir, leg.dist_nm, leg.time_min)
        if k in seen or leg.course is None:
            continue
        seen.add(k)
        cands = by_fix.get(leg.fix_id)
        if not cands:
            st["no HPF hold at the fix"] += 1
            continue

        def off(r):
            c = int(r["COURSE_INBOUND_DEG"])
            return min((c - leg.course) % 360, (leg.course - c) % 360)
        best = min(cands, key=off)
        if off(best) > 2:
            st["inbound course differs > 2 deg"] += 1
            continue
        st["inbound course within 2 deg"] += 1
        st["turn " + ("agrees" if best["TURN_DIRECTION"] == leg.turn_dir else "differs")] += 1
        hd = best["LEG_LENGTH_DIST"].strip()
        if leg.dist_nm:
            st["CIFP distance, HPF " + ("same" if hd and float(hd) == leg.dist_nm
                                        else "empty" if not hd else "differs")] += 1
        elif leg.time_min:
            st[f"CIFP {leg.time_min} min, HPF " + ("empty" if not hd else "has a distance")] += 1
    print(f"CIFP hold legs: {len(legs)}, distinct holds {len(seen)}")
    for k, v in sorted(st.items()):
        print(f"  {k}: {v}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hpf", required=True)
    ap.add_argument("--fix", required=True)
    ap.add_argument("--nav", required=True)
    ap.add_argument("--cifp")
    survey(ap.parse_args())


if __name__ == "__main__":
    main()
