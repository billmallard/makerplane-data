# SPDX-License-Identifier: Apache-2.0
"""AER-5648: assemble every procedure in a pack through the deployed pyEfis
PROC code path (ProcedureIndex + proc_load, first transition/runway)."""
import collections, sqlite3, sys
from pyefis.flightplan.procedures import ProcedureIndex
from pyefis.flightplan import proc_load, model
pack, tag = sys.argv[1], sys.argv[2]
idx = ProcedureIndex(pack)
assert idx.ready
con = sqlite3.connect(pack)
aps = {r[0]: (r[1], r[2]) for r in con.execute(
    "select airport, avg(fix_lat), avg(fix_lon) from procedures p join transitions t on t.proc_id=p.id "
    "join legs l on l.transition_id=t.id where fix_lat is not null group by airport")}
n = ok = 0
errs = collections.Counter()
for ap in sorted(r[0] for r in con.execute("select distinct airport from procedures")):
    lat, lon = aps.get(ap, (0.0, 0.0))
    origin = model.Waypoint(ap, "airport", lat or 0.0, lon or 0.0)
    for p in idx.procedures_at(ap):
        n += 1
        tr = proc_load.transition_choices(idx, p) or [None]
        rw = proc_load.runway_choices(idx, p) if proc_load.needs_runway(idx, p) else [None]
        try:
            proc_load.assemble(idx, p, tr[0], rw[0], origin=origin if p.kind == "sid" else None)
            ok += 1
        except Exception as e:
            errs[f"{type(e).__name__}: {str(e)[:60]}"] += 1
print(f"SWEEP {tag}: {n} procedures, {ok} assembled, {n-ok} refused")
for k, v in errs.most_common(6):
    print(f"SWEEP {tag}   {v:6d}  {k}")
