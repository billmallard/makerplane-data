// Pulls the real darkenToContrast()/SOURCE_TAB_LABELS output out of
// configurator/public/editor.html (not a re-guess) for
// render_evidence.py to draw. Run from the repo root:
//   node docs/images/aer-474/compute_values.mjs > /tmp/tab_values.json
import { readFileSync, writeFileSync } from "node:fs";

const src = readFileSync(new URL("../../../configurator/public/editor.html", import.meta.url), "utf8");

function extractFunction(name) {
  const start = src.indexOf(`function ${name}(`);
  let depth = 0;
  for (let i = src.indexOf("{", start); i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(start, i + 1);
  }
  throw new Error(`unbalanced braces in ${name}`);
}
function extractConst(name) {
  return src.match(new RegExp(`const ${name} = [^;]+;`))[0];
}

const FUNCS = ["normHex", "_hexToRgb", "_rgbToHex", "_srgbToLinear", "_relativeLuminance",
  "contrastRatio", "_rgbToHsv", "_hsvToRgb", "darkenToContrast"];
const body = extractConst("SOURCE_LABEL_MIN_CONTRAST") + "\n"
  + FUNCS.map(extractFunction).join("\n\n") + "\n"
  + extractConst("SOURCE_TAB_LABELS");
const { darkenToContrast, SOURCE_TAB_LABELS } = new Function(
  `${body}\nreturn { darkenToContrast, SOURCE_TAB_LABELS };`,
)();

const hpad = 6; // an arbitrary rr for illustration -- geometry isn't the point, colour/width-basis is
const out = {
  gpsFill: "#ff00ff",
  vlocFill: "#00ff00",
  gpsLabelAfter: darkenToContrast("#ff00ff"),
  vlocLabelAfter: darkenToContrast("#00ff00"),
  gpsWidthBefore: "GPS".length * 4.6 + hpad * 2,
  vlocWidthBefore: "VLOC1".length * 4.6 + hpad * 2,
  constTw: Math.max(...SOURCE_TAB_LABELS.map((l) => l.length * 4.6)) + hpad * 2,
};
writeFileSync("/tmp/tab_values.json", JSON.stringify(out));
console.log(JSON.stringify(out, null, 2));
