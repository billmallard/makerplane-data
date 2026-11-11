#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Renders the Aircraft Parameters Airspeeds card with the IAS.Vgnd setting
# (AER-4636) in demo mode, against a speeds-only schema carrying fix-gateway
# #117's aux_meta. Run from configurator/. CHROME = a chrome-headless-shell
# binary (LD_LIBRARY_PATH / FONTCONFIG_FILE set if the box lacks its libs).
set -eu
OUT=$(cd "$(dirname "$0")" && pwd)
SRV=$(mktemp -d)
mkdir -p "$SRV/assets/editor"
cat > "$SRV/assets/editor/aircraft_params_schema.json" <<'JSON'
{"schema_version": 1, "generated": "AER-4636 render", "groups": [
 {"id": "speeds", "label": "Airspeeds", "repeat": null, "keys": [
  {"key": "IAS", "label": "Indicated Airspeed", "units": "knots",
   "aux": ["Min","Max","V1","V2","Vne","Vfe","Vmc","Va","Vno","Vs","Vs0","Vx","Vy","Vgnd"],
   "band_aux": ["Min","Max"],
   "aux_meta": {"Vgnd": {"label": "Engine alarms: on-ground IAS limit", "min": 10, "max": 100, "default": 40,
    "help": "Oil pressure, oil temperature and volts alarms are silenced with the engine stopped only while IAS is below this. Set it below the slowest IAS the aircraft flies at (below the stall speed in landing configuration), never above it, or the alarms can go quiet in flight."}}}]}]}
JSON
PORT=${PORT:-8791}
(cd "$SRV" && timeout 120 python3 -m http.server $PORT >/dev/null 2>&1 &)
sleep 1
shot() {  # $1 name, $2 speed unit, $3 values JSON (knots)
  sed -e "s/speed_unit:\"kt\"/speed_unit:\"$2\"/" \
      -e "s/values: {} };/values: $3 };/" public/aircraft.html > "$SRV/aircraft.html"
  "$CHROME" --no-sandbox --disable-gpu --hide-scrollbars --window-size=1000,830 \
    --virtual-time-budget=4000 --screenshot="$OUT/$1.png" \
    "http://127.0.0.1:$PORT/aircraft.html?demo=1" >/dev/null 2>&1
}
# Blank Vgnd, Vs0 38 kt: the 40 kt default is not below Vs0 -> warning, Save allowed.
shot vgnd_kt_default_warning kt '{"IAS.Vs0":38,"IAS.Vs":44,"IAS.Vfe":70,"IAS.Vno":110,"IAS.Vne":130}'
# mph entry, Vgnd 120 kt (138.09 mph) -> outside 11.51-115.08 mph, Save disabled.
shot vgnd_mph_out_of_range mph '{"IAS.Vs0":45,"IAS.Vs":50,"IAS.Vfe":70,"IAS.Vno":110,"IAS.Vne":130,"IAS.Vgnd":120}'
# mph entry, Vgnd 35 kt (40.28 mph), below Vs0: clean.
shot vgnd_mph_ok mph '{"IAS.Vs0":45,"IAS.Vs":50,"IAS.Vfe":70,"IAS.Vno":110,"IAS.Vne":130,"IAS.Vgnd":35}'
rm -rf "$SRV"
