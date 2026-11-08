# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-5084 (S3, CAP-121): dump the REAL pyEfis colour-token layer (S1,
# screens/color_tokens.py + the REGISTRY's Prop.token mappings) as the fixture
# the editor's token twin (COLOR_TOKENS / COLOR_PROP_TOKENS /
# resolveColorTokens in public/editor.html) is tested against: the
# vocabulary, which token each colour prop reads, and what
# apply_theme_colors() actually does to an instrument's options for a table of
# option sets x preferences. The pyEfis source is imported by path from a
# checkout, never copied here.
#
#   PYEFIS=/path/to/pyEfis   # billmallard/pyEfis @ dev, PyQt6 installed
#   QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src \
#       python3 tools/color_tokens_oracle.py > test/fixtures/color_tokens_pyefis.json
#
# The preferences below are TEST INPUTS that exercise the resolution order
# (theme fill, style order, immutable and unknown tokens). They are not themes
# and nothing here proposes one; pyEfis ships no colors: block today.

import copy
import json
import logging
import os
import subprocess
import sys

import yaml

from pyefis.screens import color_tokens
from pyefis.screens import screenbuilder_factory

logging.disable(logging.WARNING)   # the warnings are the device's, not ours


def theme(*styles, **blocks):
    """preferences with `styles` enabled (in order) and `blocks` as their
    styles: colors: entries."""
    return {"style": {s: True for s in styles},
            "styles": {"colors": blocks}}


DARK = {"surface.base": "#0b0f14", "ink.primary": "#e6edf3",
        "accent": "#58a6ff", "surface.raised": "#161b22"}

PREFS = {
    "none": None,
    "empty": {},
    "no_colors": {"style": {"basic": True}, "styles": {}},
    "dark": theme("basic", basic=DARK),
    "disabled": {"style": {"basic": False}, "styles": {"colors": {"basic": DARK}}},
    "two_styles_later_wins": theme("basic", "night",
                                   basic=DARK, night={"ink.primary": "#ff8800"}),
    "immutable_and_unknown": theme("basic", basic={
        "nav.course": "#123456", "state.valid": "#654321",
        "ink.bogus": "#ffffff", "accent": "#00aa00"}),
    "null_value": theme("basic", basic={"ink.primary": None, "accent": "#00aa00"}),
    "block_not_dict": {"style": {"basic": True}, "styles": {"colors": {"basic": "red"}}},
    "colors_not_dict": {"style": {"basic": True}, "styles": {"colors": ["x"]}},
    "valueless_token_themed": theme("basic", basic={"line.major": "#777777"}),
}

# (type, options). Options are raw YAML values, as the editor saves them.
OPTIONS = [
    ("horizontal_situation_indicator", {}),
    ("horizontal_situation_indicator", {"fg_color": "ink.muted"}),
    ("horizontal_situation_indicator", {"fg_color": "#ff0000"}),
    ("horizontal_situation_indicator", {"course_color": "nav.course"}),
    ("horizontal_situation_indicator", {"course_color": "accent"}),
    ("horizontal_situation_indicator", {"bg_color": "line.major"}),
    ("horizontal_situation_indicator", {"fg_color": "state.caution", "bg_color": "red"}),
    ("button", {}),
    ("button", {"fg_color": "ink.primary", "bg_color": "surface.base"}),
    ("value_text", {}),
    ("value_text", {"value_color": "state.caution", "prefix_color": "ink.muted"}),
    ("value_text", {"border_color": "#80ffffff"}),
    ("checklist", {"text_color": "accent"}),
    ("moving_map", {}),
    ("moving_map", {"range_ring_color": "accent", "road_color": "map.water"}),
    ("moving_map", {"road_color": "map.terrain"}),
    ("heading_display", {"fg_color": "ink.primary"}),
    ("flight_plan", {"active_color": "nav.course"}),
    ("tab_section", {"fg_color": "accent"}),
    ("arc_gauge", {"some_color": "accent", "font_family": "accent"}),
    ("not_a_type", {"fg_colour": "ink.muted", "label": "ink.muted"}),
    ("value_text", {"value_color": 16777215}),
    ("value_text", {"value_color": "INK.PRIMARY"}),
]


def main():
    tokens = [{"name": n, "value": v, "description": d}
              for n, (v, d) in color_tokens.TOKENS.items()]
    prop_tokens = {}
    for itype, spec in sorted(screenbuilder_factory.REGISTRY.items()):
        mapping = color_tokens.prop_tokens(spec)
        if mapping:
            prop_tokens[itype] = mapping

    cases = []
    for pname, prefs in PREFS.items():
        for itype, options in OPTIONS:
            config = {"type": itype, "options": copy.deepcopy(options)}
            out = color_tokens.apply_theme_colors(
                config, copy.deepcopy(prefs),
                color_tokens.prop_tokens(screenbuilder_factory.REGISTRY.get(itype)))
            cases.append({
                "prefs": pname, "type": itype, "options": options,
                "out": out.get("options", {}),
            })

    try:
        rev = subprocess.run(
            ["git", "-C", sys.modules["pyefis"].__path__[0], "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        rev = "unknown"

    # The preferences pyEfis actually ships: the editor's "built-in" theme.
    with open(os.path.join(os.path.dirname(color_tokens.__file__), "..",
                           "config", "preferences.yaml")) as fh:
        shipped = yaml.safe_load(fh)

    json.dump({
        "pyefis_commit": rev,
        "shipped_theme_colors": color_tokens.theme_colors(shipped),
        "tokens": tokens,
        "immutable_groups": list(color_tokens.IMMUTABLE_GROUPS),
        "colors_key": color_tokens.COLORS_KEY,
        "prop_tokens": prop_tokens,
        "preferences": PREFS,
        "theme_colors": {p: color_tokens.theme_colors(copy.deepcopy(v))
                         for p, v in PREFS.items()},
        "cases": cases,
    }, sys.stdout, indent=1, sort_keys=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
