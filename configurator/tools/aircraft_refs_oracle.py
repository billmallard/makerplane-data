# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-5499 (FUEL1 F3): dump every `aircraft_ref` field the REAL pyEfis editor
# schema declares (pyEfis#461, editor/schema.py) as the fixture the resolver's
# reference table (AIRCRAFT_REFS in public/aircraft_refs.js) is tested
# against: which option on which instrument type, its source, and which
# options it resolves. The pyEfis source is imported by path from a checkout,
# never copied here.
#
#   PYEFIS=/path/to/pyEfis   # billmallard/pyEfis @ dev, PyQt6 installed
#   QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src \
#       python3 tools/aircraft_refs_oracle.py > test/fixtures/aircraft_refs_pyefis.json
#
# Or, where PyQt6 cannot load, from the schema.json pyEfis's editor-assets job
# generated and published (what the editor itself loads):
#
#   curl -sO https://pyefis-dev.aerocommons.org/assets/editor/schema.json
#   python3 tools/aircraft_refs_oracle.py schema.json > test/fixtures/aircraft_refs_pyefis.json

import hashlib
import json
import logging
import os
import subprocess
import sys

logging.disable(logging.WARNING)


def main():
    if len(sys.argv) > 1:
        raw = open(sys.argv[1], "rb").read()
        doc = json.loads(raw)
        origin = {"schema_json_sha256": hashlib.sha256(raw).hexdigest()}
    else:
        from pyefis.editor import schema
        doc = schema.build_schema()
        src = os.path.dirname(schema.__file__)
        origin = {"pyefis_commit": subprocess.run(
            ["git", "-C", src, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()}
    fields = []
    for itype, meta in sorted(doc["instruments"].items()):
        for name, opt in sorted((meta.get("options") or {}).items()):
            if opt.get("type") == "aircraft_ref":
                fields.append({"type": itype, "option": name, "source": opt["source"],
                               "multi": opt["multi"], "resolves": opt["resolves"]})
    print(json.dumps({**origin, "schema_version": doc["schema_version"],
                      "fields": fields}, indent=1))


if __name__ == "__main__":
    main()
