// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// Shared "extract a real function out of editor.html by brace matching"
// helper -- used by tools/twin_proto.mjs (screenshotting one instrument twin)
// and test/*.test.mjs (round-tripping the save/load logic). The point, both
// places: run the ACTUAL editor.html source, not a reimplementation that can
// drift from it silently.

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
export const EDITOR_PATH = resolve(HERE, "..", "..", "public", "editor.html");

export function editorSource() {
  return readFileSync(EDITOR_PATH, "utf8");
}

/** Source text of a top-level `function name(...) { ... }`, by brace matching. */
export function extractFunction(src, name) {
  const start = src.indexOf(`function ${name}(`);
  if (start === -1) throw new Error(`function ${name} not found in editor.html`);
  let depth = 0;
  for (let i = src.indexOf("{", start); i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(start, i + 1);
  }
  throw new Error(`unbalanced braces in ${name}`);
}
