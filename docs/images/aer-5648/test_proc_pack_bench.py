# SPDX-License-Identifier: Apache-2.0
"""AER-5648 bench check: the deployed pyEfis PROC page lists and loads
procedures from a given procedures pack. Run off-screen on the bench with
PACK=<path to procedures.sqlite>; it opens nothing the running pyEfis holds."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.environ["PYEFIS_DIR"])
from conftest import fix  # noqa: E402,F401  (also installs the in-memory FIX mock)

import pytest  # noqa: E402
from pyefis.flightplan import fixbridge, proc_load  # noqa: E402
from pyefis.flightplan import model as fp_model  # noqa: E402
from pyefis.instruments import flight_plan  # noqa: E402
from tests.instruments.flight_plan.test_segment_rows import (  # noqa: E402
    _BLANK, _ENGINE, BEELINK, _define)

PACK = os.environ["PACK"]
OUT = os.environ.get("OUT", "/tmp")
TAG = os.environ.get("TAG", "pack")
AIRPORTS = {"KSBA": (34.42621, -119.84037), "KSSF": (29.33719, -98.47114)}


@pytest.fixture()
def page(fix, qtbot):
    for key, dtype, blank in fixbridge.route_key_specs():
        _define(fix, key, dtype, blank)
    for key, dtype in _ENGINE.items():
        _define(fix, key, dtype, _BLANK[dtype])
    _define(fix, "FPLLDSEQ", "int", 0)
    fix.db.set_value("MAGVAR", -12.0)
    w = flight_plan.FlightPlan(None)
    qtbot.addWidget(w)
    w.parent = SimpleNamespace(parent=BEELINK)
    w.resize(649, 993)
    w.procedures_db_path = PACK
    return w


@pytest.mark.parametrize("airport", sorted(AIRPORTS))
@pytest.mark.parametrize("kind", ["departure", "arrival", "approach"])
def test_proc_page_lists_and_loads(page, airport, kind):
    lat, lon = AIRPORTS[airport]
    apt = fp_model.Waypoint(airport, "airport", lat, lon)
    far = fp_model.Waypoint("XXXX", "user", lat + 1.0, lon + 1.0)
    page._plan = fp_model.FlightPlan(
        waypoints=[apt.copy(), far] if kind == "departure" else [far, apt.copy()])
    page._open_proc_page(kind)
    page._proc_set_airport(airport)
    assert page._ensure_procedure_index().ready, f"PROC index not ready on {PACK}"
    procs = page._proc_choices("proc")
    if not procs:
        pytest.skip(f"no {kind} at {airport}")
    page._proc_open_field("proc")
    assert page._proc_picker == "proc"
    page.grab().save(f"{OUT}/{TAG}_{airport}_{kind}_picker.png")
    loaded = 0
    failures = []
    for label, proc in procs:
        page._proc_pick("proc", proc)
        trans = [v for _, v in page._proc_choices("transition")] or [None]
        rwys = [v for _, v in page._proc_choices("runway")] or [None]
        try:
            proc_load.assemble(page._ensure_procedure_index(), proc, trans[0], rwys[0],
                               origin=apt if kind == "departure" else None)
            loaded += 1
        except Exception as e:  # noqa: BLE001
            failures.append(f"{label}: {e}")
    print(f"BENCH {TAG} {airport} {kind}: {len(procs)} listed, {loaded} assembled, "
          f"failures={failures}")
    assert loaded > 0
