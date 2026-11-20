# SPDX-License-Identifier: Apache-2.0
"""Device panel-config pull + install (#65 P3, multi-screen #72).

A paired device fetches its latest panel config (native pyEfis YAML) from the
configuration manager (``pyefis.aerocommons.org``) and installs it into the
pyEfis config dir, then restarts pyEfis. Uses the device token stored by
``pyefis-data pair``.

The pulled config is one or more screenbuilder ``screen`` blocks (plus a ``main``
block). There are two install shapes, chosen by how many screens the panel has:

**Single screen** (least surgery, the original P3 path):
  * write the screen as a *managed* file named after the device's EXISTING
    ``main/default.yaml`` ``defaultScreen`` -- so the boot screen flips to the
    panel without editing ``main/`` at all;
  * activate by overriding ONLY that screen's include
    (``SCREEN_<defaultScreen>`` -> ``screens/managed.yaml``) and KEEPING the
    device's stock ``SCREENS_CONFIG`` screen list.

**Multiple screens** (#72):
  * write each editor screen as ``screens/managed_<name>.yaml``. The default
    screen takes the device's existing ``defaultScreen`` name (so boot still
    needs no ``main/`` edit); the rest keep their editor names;
  * write a clean ``screens/managed_list.yaml`` listing exactly those screens
    and override ``SCREENS_CONFIG`` to it -- the editor is the whole panel;
  * inject a small "SCREEN >" button (``buttons/managed-next.yaml`` ->
    ``show next screen``) onto each screen so a touchscreen-only panel can
    cycle between them (the encoder/key screen-switch bindings aren't assumed).

A short clean screen list used to segfault the eglfs ``QOpenGLCompositor`` with
SVS; that was the AI redraw-before-resize bug (pyEfis #274), now fixed, so a
clean editor-only list is safe (verified on the Pi 5). The single-screen path
still keeps the full stock list -- it's the proven, untouched original.

**Aircraft preferences** (#108): the served YAML may carry a top-level
``preferences:`` block derived from the project's aircraft profile, today only
``units: {Pressure: inHg|hPa}``. Its ``units`` are merged into
``preferences.yaml.custom`` (pyEfis starts every unit-switching instrument in
the group's unit), leaving every other key there alone. Which unit groups
config-pull set is recorded in ``.panel_prefs.yaml``, so when the profile stops
setting one (the block is absent) only a key config-pull itself wrote is
removed -- a hand-set unit is never touched. A malformed block changes nothing.

**fix-gateway settings** (P3f, AER-5458): the served YAML may also carry a
top-level ``fixgw:`` block, today only ``ownship_address: "<6 hex>"``, the
aircraft's own 24-bit ADS-B address. That one key is written as a top-level
``ownship_address:`` line in fix-gateway's ``preferences.yaml.custom``, where
the stratux traffic writer uses it when its connection's own value is blank.
The file is edited as text, so every other line and comment there is kept
byte-for-byte, and the edit is checked by re-parsing before it is written.
The value written is recorded in ``.panel_prefs.yaml``; a blank address
removes only a line config-pull wrote. fix-gateway's file is snapshotted with
the panel, and a crashed install restores it. fix-gateway is restarted only
when its file changed.

**aircraft.ini** (ACINI1, AER-5993; docs/canfix_configurator.md section 7):
the ``fixgw:`` block may carry ``aircraft_ini``, the aircraft profile's values
compiled into fix-gateway's init_data dialect, with ``aircraft_ini_sha256``.
The text is checked against its hash and line by line, then staged as
``init_data/aircraft.ini.pending``. Installing it is a fix-gateway change, so
it happens only in maintenance mode (``config-pull --maintenance``: a person
on the ground says so; Bill's ruling on AER-5973, roadmap AVI-CON-4). There
the staged file is dry-run through fix-gateway's own database and init-file
loader, since one key or value its database refuses stops fix-gateway at
startup, then written as ``init_data/aircraft.ini``, listed last under
``initialization files`` in ``default.yaml`` (idempotent, edited as text and
re-parsed, like the ownship line), and fix-gateway is restarted and checked.
If fix-gateway does not stay up, both files are put back. Outside maintenance
mode nothing fix-gateway reads changes; the staged file waits for the next
``config-pull --maintenance``. Both writes are fsynced: a ``default.yaml``
lost to a power cut stops fix-gateway.

**Tank count** (FUEL1h, AER-6135; docs/fuel_provider_model.md section 4):
fix-gateway has keys only for fuel tanks FUELQ1..FUELQ<t>, ``t`` from its
``database/variables.yaml``, and an aircraft.ini naming a higher slot stops
it at startup. So before the dry run, the install raises ``t`` to the
highest slot the file names. It never lowers ``t``, so a slot a served
config uses always exists. The file is edited as text (one number) and
re-parsed, snapshotted with the others, and put back on rollback.

Every fix-gateway restart, including the one the ownship address asks for,
happens only in maintenance mode.

The shipped config files are never modified. Before each install the current
panel state is snapshotted to ``.panel_backup/`` so a config that crashes pyEfis
is rolled back to the last working panel. Every write is atomic (temp +
``os.replace``).
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

# A real UA: Cloudflare's edge 403s the default "Python-urllib/*".
_UA = "pyefis-data/0.1 (+https://github.com/billmallard/makerplane-data)"
_DEFAULT_PYEFIS_CONFIG = "~/makerplane/pyefis/config"

# Snapshot dir (under the config dir) holding the pre-install panel state, used
# by rollback() to restore the last working panel after a failed swap.
_BACKUP_DIR = ".panel_backup"

# Record of the unit groups config-pull last wrote into preferences.yaml.custom
# (from the served preferences block), so a group the profile no longer sets is
# removed without touching one the user set by hand. Snapshotted with the panel.
_PREFS_RECORD = ".panel_prefs.yaml"

# fix-gateway's user config dir (its server copies the package config here and
# merges preferences.yaml.custom over preferences.yaml) and the one key
# config-pull writes there. The snapshot copy lives under .panel_backup/.
_DEFAULT_FIXGW_CONFIG = "~/makerplane/fixgw/config"
_FIXGW_CUSTOM = "preferences.yaml.custom"
_FIXGW_KEY = "ownship_address"
_FIXGW_BACKUP = "fixgw/preferences.yaml.custom"
_FIXGW_MARK = ("# ownship_address: set by pyefis-data config-pull from the "
               "configurator's aircraft profile.")

# aircraft.ini (ACINI1): where it goes, how default.yaml lists it, where it
# waits for maintenance mode, and its snapshot copies under .panel_backup/.
_FIXGW_MAIN = "default.yaml"
_INI_REL = "init_data/aircraft.ini"
_INI_PENDING_REL = "init_data/aircraft.ini.pending"
_INI_ENTRY = "{CONFIG}/init_data/aircraft.ini"
_INI_BACKUP = "fixgw/aircraft.ini"
_MAIN_BACKUP = "fixgw/default.yaml"
# The only lines the configurator's compiler writes (public/aircraft_ini.js):
# blank, '#' comment, or a plain FIX key (with aux) and a number.
_INI_LINE = re.compile(
    r"[A-Za-z0-9]+(\.[A-Za-z0-9]+)? = [-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?")

# fix-gateway's database variables (FUEL1h, brief F8): ``t`` is the tank
# count, and FUELQ1..FUELQ<t> are the only tank keys its database has. An
# aircraft.ini naming a higher slot is a KeyError that stops fix-gateway.
_VARS_REL = "database/variables.yaml"
_VARS_BACKUP = "fixgw/variables.yaml"
_INI_TANK = re.compile(r"FUELQ(\d+)(\.[A-Za-z0-9]+)? =")
_VARS_TANK = re.compile(r"([ \t]+t[ \t]*:[ \t]*)(\d+)([ \t]*(#.*)?)$")

# Standard on-device SVS data layout (the makerplane-data updater installs here).
# Injected into a virtual_vfr instrument so it renders terrain, mirroring the
# stock includes/ahrs/svs.yaml block. A device with a usable GPU + this data
# shows synthetic vision; without, SVS self-disables to sky/ground + an
# "SVS UNAVAIL" flag (never fatal). Needs pyEfis #71's AI redraw guard.
_SVS_OPTS = {
    "enabled": True,
    "range_nm": 30,
    "tile_path": "/data/makerplane-data/terrain/tiles",
    "nasr_db_path": "/data/makerplane-data/navdata/current/airports.sqlite",
    "airport_provider_dir": "/data/makerplane-data/airports",
    "dof_db_path": "/data/makerplane-data/obstacles/current/obstacles.sqlite",
    "water_db_path": "/data/makerplane-data/water/current/water.sqlite",
    "highway_db_path": "/data/makerplane-data/highways/current/highways.sqlite",
    "water_max_vertices": 1024,
}

# Touchscreen-button fixids are TSBTN<node><suffix>, PRE-REGISTERED in the FIX
# database from the template key `TSBTNns` over n=1..NODES, s=1..TS_BUTTONS (the
# fixgw database/variables.yaml ranges -- default n:5, s:40). A button's dbkey
# must name one that EXISTS: the button does get_item() WITHOUT create=True, so
# an out-of-range suffix is a fatal KeyError at screen-build time. The stock
# buttons use suffixes up to ~28, so the switch buttons take the TOP of the range
# (40, 39, ...) -- in range and clear of the stock set.
_TS_BUTTON_MAX = 40                 # fixgw variables.yaml `s:` (touchscreen buttons)


def _switch_button_cfg(suffix: int, target: str) -> str:
    """The screen-switch button definition (one per managed screen). A plain
    touchscreen button whose click fires the HMI "show screen" action to jump to
    ``target`` -- an EXPLICIT jump (not "show next screen") so it cycles only the
    editor's screens, skipping the stock screens we keep loaded as GL ballast.
    {id} -> node id at build time; ``suffix`` selects a distinct registered TSBTN
    slot so two screens' buttons don't share a key."""
    return (
        "# Managed-panel screen-switch button (written by pyefis-data config-pull).\n"
        "# Tapping it jumps to the next screen you designed in the editor, so a\n"
        "# touchscreen-only panel can move between them.\n"
        "type: simple\n"
        'text: "SCREEN >"\n'
        f"dbkey: TSBTN{{id}}{suffix}\n"
        "conditions:\n"
        '  - when: "True"\n'
        "    actions:\n"
        '      - set bg color: "#222b3ad0"\n'
        '      - set fg color: "#f0f0f0"\n'
        "    continue: true\n"
        '  - when: "CLICKED eq true"\n'
        "    actions:\n"
        f"      - show screen: {target}\n"
    )


def config_dir() -> Path:
    return Path(os.path.expanduser(
        os.environ.get("PYEFIS_CONFIG_DIR", _DEFAULT_PYEFIS_CONFIG)))


def fixgw_config_dir() -> Path:
    return Path(os.path.expanduser(
        os.environ.get("FIXGW_CONFIG_DIR", _DEFAULT_FIXGW_CONFIG)))


def fetch_config(cfg) -> tuple[str, int | None, str | None]:
    """GET <configurator>/device/config with the device token.

    Returns ``(status, version, yaml_text, etag)`` where status is one of
    ``updated`` (new config in yaml_text), ``up-to-date`` (304), ``none`` (404),
    ``unpaired`` (no token), or ``error:<msg>``. ``etag`` is the server's ETag
    for an ``updated`` response (else None); store it and it is sent back as
    If-None-Match. It covers the aircraft-preferences block as well as the panel
    version (#108), so a profile-only change is not a 304.
    """
    if not cfg.device_token:
        return ("unpaired", None, None, None)
    headers = {"Authorization": f"Bearer {cfg.device_token}", "User-Agent": _UA}
    if cfg.config_etag:
        headers["If-None-Match"] = cfg.config_etag
    elif cfg.config_version:                 # installed before ETags were stored
        headers["If-None-Match"] = f'"v{cfg.config_version}"'
    url = f"{cfg.configurator_url.rstrip('/')}/device/config"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                    timeout=30) as resp:
            text = resp.read().decode("utf-8")
            ver = resp.headers.get("X-Config-Version")
            return ("updated", int(ver) if ver else None, text,
                    resp.headers.get("ETag"))
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return ("up-to-date", cfg.config_version, None, None)
        if e.code == 404:
            return ("none", None, None, None)
        return (f"error:HTTP {e.code}", None, None, None)
    except Exception as e:                       # network / TLS / timeout
        return (f"error:{e}", None, None, None)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _durable_write(path: Path, text: str) -> None:
    """_atomic_write that also survives a power cut: fsync the temp file
    before the rename and the directory after it (docs: crash consistency).
    Used for the fix-gateway files, where a lost or empty default.yaml stops
    fix-gateway at the next start."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return                                # no directory fsync on this OS
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _device_default_screen(cd: Path) -> str:
    """The device's current ``defaultScreen`` (so the managed boot screen can
    take its name and the boot screen flips without touching ``main/``). Defaults
    to PANEL if unset/unreadable."""
    import yaml
    try:
        main = yaml.safe_load((cd / "main" / "default.yaml").read_text("utf-8")) or {}
        ds = main.get("defaultScreen")
        if isinstance(ds, str) and ds.strip():
            return ds.strip()
    except Exception:
        pass
    return "PANEL"


def _sanitize(name: str) -> str:
    """A screen name safe for a screenbuilder screen key / include token / file
    name (letters, digits, underscore; never leading-digit)."""
    s = re.sub(r"[^A-Za-z0-9_]", "_", (name or "").strip()) or "SCREEN"
    if s[0].isdigit():
        s = "S_" + s
    return s


# The legacy CIFP Virtual VFR overlay and the stock include carrying its
# screen-level ``dbpath``. Which pyEfis the device runs decides whether a
# ``virtual_vfr`` needs it (AER-5675), so config-pull reads the installed
# pyEfis rather than assuming its version:
#   * LEGACY_ALWAYS -- before pyEfis #349: the overlay is always built and its
#     first resize raises on a ``None`` dbpath, so every ``virtual_vfr`` needs
#     the include.
#   * LEGACY_SVS_SKIP -- #349 (CIFP1) to #457: the overlay is skipped when SVS
#     is on, so only an SVS-off ``virtual_vfr`` needs it.
#   * LEGACY_NONE -- #457 (VVFR1) on: the overlay is gone; nothing reads it.
_VVFR_DB_INCLUDE = "screens/virtualvfr_db.yaml"
LEGACY_ALWAYS = "always"
LEGACY_SVS_SKIP = "svs-skip"
LEGACY_NONE = "none"


def _svs_enabled(opts: dict) -> bool:
    """Whether a ``virtual_vfr``'s SVS is on, resolved the way pyEfis's
    ``screenbuilder_factory`` does: the nested ``svs:`` dict overlaid by the
    flat ``svs_*`` editor options, so an explicit ``svs_enabled: false`` wins
    over the injected block. A missing ``enabled`` counts as off."""
    svs = opts.get("svs")
    cfg = dict(svs) if isinstance(svs, dict) else {}
    cfg.update({k[4:]: v for k, v in opts.items() if k.startswith("svs_")})
    return bool(cfg.get("enabled"))


def _overlay_mode(pkg: Path | None) -> str | None:
    """Classify an installed ``pyefis`` package directory by its legacy
    overlay: LEGACY_NONE when ``VirtualVfr.py`` is gone (#457), LEGACY_SVS_SKIP
    when it carries the #349 SVS skip (``_svs_owns_view``), LEGACY_ALWAYS when
    it predates #349. None when the directory isn't a recognisable pyEfis."""
    if pkg is None:
        return None
    ai = pkg / "instruments" / "ai"
    if not ai.is_dir():
        return None
    vvfr = ai / "VirtualVfr.py"
    if not vvfr.is_file():
        return LEGACY_NONE
    try:
        text = vvfr.read_text("utf-8", errors="replace")
    except OSError:
        return None
    return LEGACY_SVS_SKIP if "_svs_owns_view" in text else LEGACY_ALWAYS


def _pyefis_package_dir(scope: str | None = None) -> Path | None:
    """The ``pyefis`` package directory the pyEfis service actually runs: ask
    the unit's own interpreter (``ExecStart`` ``path=``) where ``pyefis`` lives,
    from the unit's ``WorkingDirectory``, without importing it. Falls back to a
    source checkout's ``src/pyefis`` under that directory. None if neither
    answers. Never raises."""
    scope = _resolve_scope(scope)
    workdir = _systemctl_show("WorkingDirectory", scope) or None
    m = re.search(r"path=(\S+)", _systemctl_show("ExecStart", scope))
    if m:
        try:
            out = subprocess.run(
                [m.group(1), "-c",
                 "import importlib.util as u; s = u.find_spec('pyefis'); "
                 "print(list(s.submodule_search_locations)[0] if s else '')"],
                cwd=workdir, capture_output=True, text=True, timeout=30)
            loc = out.stdout.strip()
            if out.returncode == 0 and loc and Path(loc).is_dir():
                return Path(loc)
        except Exception:
            pass
    if workdir and (Path(workdir) / "src" / "pyefis").is_dir():
        return Path(workdir) / "src" / "pyefis"
    return None


def legacy_overlay(cd: Path, scope: str | None = None) -> str:
    """Which legacy-overlay case this device is in (see LEGACY_*), probed once
    per install. When the installed pyEfis can't be located or read, answer
    LEGACY_ALWAYS: the include is then added to every ``virtual_vfr``, which
    every pyEfis accepts (#457 keeps the file as an empty stub for exactly
    this). When the device's config dir has no such file, answer LEGACY_NONE:
    a missing include is a hard error, so it must never be written."""
    if not (cd / _VVFR_DB_INCLUDE).is_file():
        return LEGACY_NONE
    return _overlay_mode(_pyefis_package_dir(scope)) or LEGACY_ALWAYS


def _prep_screen(screen_def: dict, legacy: str = LEGACY_NONE) -> dict:
    """Inject the device-side bits the editor can't know into one screen:

    * a ``virtual_vfr`` gets an ``svs`` options block (terrain/airport/water data
      paths) so it renders synthetic vision, unless it already carries one.
    * a ``virtual_vfr`` that the device's pyEfis would build the legacy CIFP
      overlay for (``legacy``, from legacy_overlay()) gets the stock
      ``screens/virtualvfr_db.yaml`` include. That overlay raises on a ``None``
      dbpath on its first resize, which aborts pyEfis (AER-5675). On a pyEfis
      with #457 (VVFR1) nothing is added (AER-5421).

    An include a screen already lists is left alone. Returns a new screen dict
    (the input is not mutated)."""
    screen_def = dict(screen_def)
    needs_db = False
    insts = []
    for inst in screen_def.get("instruments", []):
        if isinstance(inst, dict) and inst.get("type") == "virtual_vfr":
            inst = dict(inst)
            opts = dict(inst.get("options") or {})
            opts.setdefault("svs", dict(_SVS_OPTS))   # keep an explicit svs if present
            inst["options"] = opts
            if legacy == LEGACY_ALWAYS or (
                    legacy == LEGACY_SVS_SKIP and not _svs_enabled(opts)):
                needs_db = True
        insts.append(inst)
    screen_def["instruments"] = insts
    if needs_db:
        inc = list(screen_def.get("include") or [])
        if _VVFR_DB_INCLUDE not in inc:
            inc.append(_VVFR_DB_INCLUDE)
        screen_def["include"] = inc
    return screen_def


def _switch_button(button_cfg: str) -> dict:
    """A small bottom-centre "next screen" button instrument referencing the
    given per-screen button config file. Bottom-centre (cols 88-112 of 200)
    clears the airspeed/altitude tapes that hug the screen edges on a PFD; a
    user can reposition it in the editor."""
    return {
        "type": "button",
        "row": 102,
        "column": 88,
        "span": {"rows": 6, "columns": 24},
        "options": {"config": button_cfg},
    }


def _managed_rel_paths(cd: Path) -> list[str]:
    """Config-relative paths of every file an install writes (the override + all
    managed screen/button files), for snapshot/restore."""
    rels = ["preferences.yaml.custom", _PREFS_RECORD]
    sd = cd / "screens"
    if sd.is_dir():
        rels += [f"screens/{p.name}" for p in sorted(sd.glob("managed*.yaml"))]
    bd = cd / "buttons"
    if bd.is_dir():
        rels += [f"buttons/{p.name}" for p in sorted(bd.glob("managed-next*.yaml"))]
    return rels


def _snapshot(cd: Path, fcd: Path | None = None) -> None:
    """Snapshot the current panel state to ``.panel_backup/`` so a failed swap
    can be rolled back to the last working panel. Taken before any install write,
    so it captures the PREVIOUS (working) config."""
    bk = cd / _BACKUP_DIR
    shutil.rmtree(bk, ignore_errors=True)
    bk.mkdir(parents=True, exist_ok=True)
    present = []
    for rel in _managed_rel_paths(cd):
        src = cd / rel
        if src.exists():
            dst = bk / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(src.read_text("utf-8"), encoding="utf-8")
            present.append(rel)
    (bk / "manifest.txt").write_text("\n".join(present), encoding="utf-8")
    _snapshot_fixgw(bk, fcd)
    # Keep a one-time PRISTINE override (pre-ANY-panel) as the ultimate fallback.
    custom = cd / "preferences.yaml.custom"
    prepanel = cd / "preferences.yaml.custom.prepanel"
    if custom.exists() and not prepanel.exists():
        _atomic_write(prepanel, custom.read_text("utf-8"))


def snapshot(cd: Path | None = None, fcd: Path | None = None) -> None:
    """Snapshot the working panel and fix-gateway files now, for a change
    made outside install_config (a staged aircraft.ini installed on a pull
    that found the panel up to date)."""
    _snapshot(cd or config_dir(), fcd or fixgw_config_dir())


def _snapshot_fixgw(bk: Path, fcd: Path | None) -> None:
    """Add the fix-gateway files config-pull writes (preferences.yaml.custom,
    init_data/aircraft.ini, default.yaml, database/variables.yaml) to the
    snapshot: each one's text, or an ``absent`` marker when there is none, so
    rollback can remove a file the failed install created. Nothing when
    fix-gateway has no config dir here."""
    if fcd is None or not fcd.is_dir():
        return
    for rel, saved in ((_FIXGW_CUSTOM, _FIXGW_BACKUP), (_INI_REL, _INI_BACKUP),
                       (_FIXGW_MAIN, _MAIN_BACKUP), (_VARS_REL, _VARS_BACKUP)):
        src = fcd / rel
        dst = bk / saved
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.exists():
            dst.write_text(src.read_text("utf-8"), encoding="utf-8")
        else:
            (bk / (saved + ".absent")).write_text("", encoding="utf-8")


def _load_custom(cd: Path) -> dict:
    import yaml
    p = cd / "preferences.yaml.custom"
    try:
        c = yaml.safe_load(p.read_text("utf-8")) or {}
    except Exception:
        c = {}
    return c if isinstance(c, dict) else {}


def _write_custom(cd: Path, custom: dict) -> None:
    import yaml
    _atomic_write(cd / "preferences.yaml.custom",
                  yaml.safe_dump(custom, sort_keys=False))


_MALFORMED = object()


def _served_units(prefs):
    """The ``units`` mapping of a served ``preferences:`` block.

    ``{}`` when the block is absent or sets no units (the profile does not
    manage any unit group); ``_MALFORMED`` when it is present but not a mapping
    of non-empty string group -> non-empty string unit. Never raises."""
    if prefs is None:
        return {}
    if not isinstance(prefs, dict):
        return _MALFORMED
    units = prefs.get("units")
    if units is None:
        return {}
    if not isinstance(units, dict):
        return _MALFORMED
    for g, u in units.items():
        if not (isinstance(g, str) and g.strip() and isinstance(u, str) and u.strip()):
            return _MALFORMED
    return {g.strip(): u.strip() for g, u in units.items()}


def _load_record(cd: Path) -> dict:
    import yaml
    try:
        rec = yaml.safe_load((cd / _PREFS_RECORD).read_text("utf-8")) or {}
    except Exception:
        return {}
    return rec if isinstance(rec, dict) else {}


def _update_record(cd: Path, key: str, value) -> None:
    """Set (or, for None, drop) one key of the record, keeping the others."""
    import yaml
    rec = _load_record(cd)
    if value is None:
        rec.pop(key, None)
    else:
        rec[key] = value
    _atomic_write(cd / _PREFS_RECORD, yaml.safe_dump(rec, sort_keys=False))


def _load_prefs_record(cd: Path) -> list[str]:
    groups = _load_record(cd).get("units")
    return [g for g in groups if isinstance(g, str)] if isinstance(groups, list) else []


def _apply_preferences(cd: Path, custom: dict, prefs) -> dict:
    """Merge the served preferences block's ``units`` into ``custom`` (the
    loaded preferences.yaml.custom, written by the caller) and update the
    record of the unit groups config-pull manages. Returns the units now
    managed (``{}`` if none). A malformed block changes nothing at all.

    Only ``units.<group>`` keys are written, and a group is only removed if
    the record says config-pull wrote it -- every other key in the file, and a
    unit the user set by hand, is left alone."""
    served = _served_units(prefs)
    if served is _MALFORMED:
        return {}
    previous = _load_prefs_record(cd)
    units = custom.get("units")
    if units is not None and not isinstance(units, dict):
        if not served and not previous:
            return {}                     # nothing to do; leave the user's value
        units = None                      # an unreadable units: is replaced
    units = dict(units or {})
    for g in previous:
        if g not in served:
            units.pop(g, None)
    units.update(served)
    if units:
        custom["units"] = units
    else:
        custom.pop("units", None)
    if served or previous:
        _update_record(cd, "units", sorted(served))
    return served


def _served_ownship(block):
    """The ``ownship_address`` of a served ``fixgw:`` block, upper case.

    None when the block is absent or sets no address (the profile does not
    manage it); ``_MALFORMED`` when it is present but not a mapping, or the
    address is not a quoted six-hex-digit string other than 000000. Never
    raises."""
    if block is None:
        return None
    if not isinstance(block, dict):
        return _MALFORMED
    addr = block.get(_FIXGW_KEY)
    if addr is None or addr == "":
        return None
    if not (isinstance(addr, str) and re.fullmatch(r"[0-9A-Fa-f]{6}", addr.strip())
            and int(addr, 16) != 0):
        return _MALFORMED
    return addr.strip().upper()


def _ownship_lines_removed(text: str) -> str:
    """``text`` without config-pull's marker comment and any top-level
    ``ownship_address:`` line."""
    out = [l for l in text.splitlines(keepends=True)
           if l.rstrip("\r\n") != _FIXGW_MARK
           and not re.match(r"ownship_address[ \t]*:", l)]
    return "".join(out)


def _apply_fixgw(cd: Path, fcd: Path | None, block) -> dict:
    """Write the served ``fixgw:`` block's ownship address into fix-gateway's
    preferences.yaml.custom, as one top-level line. Returns
    ``{"ownship_address": <now managed, or None>, "changed": bool}`` plus a
    ``skipped`` reason when nothing could be done.

    The file is edited as text, never re-dumped, so its comments and layout
    survive; the result must parse to exactly the old mapping with only this
    key changed, or nothing is written. A blank address removes the line only
    if it still holds the value config-pull recorded writing -- one the user
    changed by hand is left. A malformed block changes nothing."""
    import yaml
    served = _served_ownship(block)
    if served is _MALFORMED:
        return {"ownship_address": None, "changed": False, "skipped": "malformed fixgw block"}
    previous = _load_record(cd).get("fixgw_ownship_address")
    previous = previous if isinstance(previous, str) else None
    result = {"ownship_address": served, "changed": False}
    if served is None and previous is None:
        return result
    if fcd is None or not fcd.is_dir():
        result["skipped"] = "no fix-gateway config dir"
        return result
    path = fcd / _FIXGW_CUSTOM
    try:
        text = path.read_text("utf-8") if path.exists() else ""
        old = yaml.safe_load(text) if text.strip() else {}
    except Exception:
        old = None
    if not isinstance(old, dict):
        result["skipped"] = f"{path} is not a readable YAML mapping"
        return result
    current = old.get(_FIXGW_KEY)
    if served is None and current != previous:
        _update_record(cd, "fixgw_ownship_address", None)   # not ours any more
        return result
    new_text = _ownship_lines_removed(text)
    want = {k: v for k, v in old.items() if k != _FIXGW_KEY}
    if served is not None:
        if new_text and not new_text.endswith("\n"):
            new_text += "\n"
        new_text += f'{_FIXGW_MARK}\n{_FIXGW_KEY}: "{served}"\n'
        want[_FIXGW_KEY] = served
    try:
        ok = (yaml.safe_load(new_text) or {}) == want
    except Exception:
        ok = False
    if not ok:
        result["skipped"] = f"could not edit {path} safely"
        return result
    if new_text != text:
        _atomic_write(path, new_text)
        result["changed"] = True
    _update_record(cd, "fixgw_ownship_address", served)
    return result


def _served_ini(block):
    """The ``aircraft_ini`` text of a served ``fixgw:`` block.

    None when the block is absent or carries no file (the profile does not
    manage it); ``(_MALFORMED, reason)`` when the text is not a string, does
    not match ``aircraft_ini_sha256``, or holds a line the configurator's
    compiler never writes. Never raises."""
    if block is None:
        return None
    if not isinstance(block, dict):
        return (_MALFORMED, "fixgw block is not a mapping")
    text = block.get("aircraft_ini")
    if text is None:
        return None
    want = block.get("aircraft_ini_sha256")
    if not isinstance(text, str) or not isinstance(want, str):
        return (_MALFORMED, "aircraft_ini or its sha256 is not a string")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != want.strip().lower():
        return (_MALFORMED, "aircraft_ini does not match its sha256")
    bad = _ini_bad_line(text)
    if bad:
        return (_MALFORMED, f"aircraft_ini line not in the init_data dialect: {bad!r}")
    return text


def _ini_bad_line(text: str) -> str | None:
    """The first line of ``text`` that is not blank, a '#' comment, or a
    ``KEY[.aux] = <number>`` assignment; None when every line is one."""
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and not _INI_LINE.fullmatch(s):
            return line
    return None


def _ini_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ini_tanks(text: str) -> int:
    """The highest fuel tank slot ``text`` names (``FUELQ5.Max = ...`` -> 5);
    0 when it names none. FUELQT, the total, is not a slot."""
    slots = [int(m.group(1)) for line in text.splitlines()
             for m in [_INI_TANK.match(line.strip())] if m]
    return max(slots, default=0)


def _vars_tanks(text: str | None) -> int | None:
    """``variables: t`` of a fix-gateway variables.yaml, or None when there
    is no file, it does not parse, or ``t`` is not a whole number."""
    import yaml
    try:
        doc = yaml.safe_load(text) if text is not None else None
    except Exception:
        return None
    var = doc.get("variables") if isinstance(doc, dict) else None
    t = var.get("t") if isinstance(var, dict) else None
    return t if isinstance(t, int) and not isinstance(t, bool) else None


def _raise_tanks(text: str, need: int) -> str | None:
    """variables.yaml ``text`` with ``t`` at least ``need``: ``text`` itself
    when it already is (``t`` is never lowered), the edited text otherwise,
    or None when it cannot be edited safely. Only the number on the one
    ``t:`` line changes; the result must re-parse to the same mapping with
    only ``variables: t`` different."""
    import yaml
    have = _vars_tanks(text)
    if have is None:
        return None
    if have >= need:
        return text
    lines = text.splitlines(keepends=True)
    hits = [i for i, l in enumerate(lines) if _VARS_TANK.match(l.rstrip("\r\n"))]
    if len(hits) != 1:
        return None
    i = hits[0]
    body = lines[i].rstrip("\r\n")
    m = _VARS_TANK.match(body)
    lines[i] = m.group(1) + str(need) + m.group(3) + lines[i][len(body):]
    new = "".join(lines)
    want = yaml.safe_load(text)
    want["variables"] = dict(want["variables"], t=need)
    try:
        ok = yaml.safe_load(new) == want
    except Exception:
        ok = False
    return new if ok else None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _enrol_ini(text: str) -> str | None:
    """``default.yaml`` text with ``{CONFIG}/init_data/aircraft.ini`` listed
    LAST under ``initialization files`` -- last wins, so the profile
    overrides the shipped defaults and custom.ini without touching them.

    Returns ``text`` itself when it is already so (idempotent), the edited
    text otherwise, or None when the file cannot be edited safely. Edited as
    text so every other line and comment is kept; an entry already listed
    elsewhere is moved, and the result must re-parse to exactly the old
    mapping with only that list changed."""
    import yaml
    try:
        doc = yaml.safe_load(text) if text.strip() else {}
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    files = doc.get("initialization files")
    if files is None:
        files = []
    if not (isinstance(files, list) and all(isinstance(f, str) for f in files)):
        return None
    want_files = [f for f in files if f != _INI_ENTRY] + [_INI_ENTRY]
    if files == want_files:
        return text
    want = dict(doc)
    want["initialization files"] = want_files

    lines = text.splitlines(keepends=True)
    head = [i for i, l in enumerate(lines)
            if re.match(r"initialization files[ \t]*:[ \t]*(#.*)?$", l.rstrip("\r\n"))]
    entry = f'"{_INI_ENTRY}"'
    if not head:
        if "initialization files" in doc:
            return None                       # a form this edit does not know
        new = "".join(lines)
        if new and not new.endswith("\n"):
            new += "\n"
        new += f"initialization files:\n  - {entry}\n"
    else:
        h = head[0]
        items, end = [], h + 1
        while end < len(lines):
            s = lines[end].strip()
            if s and not s.startswith("#") and not re.match(r"[ \t]*- ", lines[end]):
                break
            if re.match(r"[ \t]*- ", lines[end]):
                items.append(end)
            end += 1
        ours = []
        for i in items:
            try:
                if yaml.safe_load(lines[i].strip()[2:]) == _INI_ENTRY:
                    ours.append(i)
            except Exception:
                return None
        indent = re.match(r"([ \t]*)- ", lines[items[0]]).group(1) if items else "  "
        keep = [i for i in items if i not in ours]
        at = (keep[-1] + 1) if keep else h + 1
        new_lines = list(lines)
        if not new_lines[at - 1].endswith("\n"):
            new_lines[at - 1] += "\n"
        new_lines.insert(at, f"{indent}- {entry}\n")
        for i in sorted(ours, reverse=True):
            new_lines.pop(i if i < at else i + 1)
        new = "".join(new_lines)
    try:
        ok = yaml.safe_load(new) == want
    except Exception:
        ok = False
    return new if ok else None


# Run by fix-gateway's own interpreter: build its database from its own
# config the way fixgw/server.py does at startup, then load every init file
# it lists, with the live aircraft.ini swapped for the staged one. Prints one
# line per assignment fix-gateway would refuse (each one would stop it).
_DRY_RUN = r"""
import os, sys, logging
logging.disable(logging.CRITICAL)
cfg_file, staged, live = sys.argv[1:4]
bad = 0
try:
    import yaml
    from fixgw import cfg, database
    path = os.path.dirname(cfg_file)
    def merge(a, b):
        for k, v in (b or {}).items():
            if isinstance(v, dict) and isinstance(a.get(k), dict):
                merge(a[k], v)
            else:
                a[k] = v
    with open(os.path.join(path, "preferences.yaml")) as f:
        prefs = yaml.safe_load(f) or {}
    custom = os.path.join(path, "preferences.yaml.custom")
    if os.path.exists(custom):
        with open(custom) as f:
            merge(prefs, yaml.safe_load(f))
    config = cfg.from_yaml(cfg_file, preferences=prefs)
    database.init(config["database file"].format(CONFIG=path))
    files = [f.format(CONFIG=path) for f in (config.get("initialization files") or [])]
    files = [f for f in files if os.path.realpath(f) != os.path.realpath(live)] + [staged]
    for fn in files:
        with open(fn) as f:
            for n, line in enumerate(f, 1):
                l = line.strip()
                if l and l[0] != "#":
                    x = l.split("=")
                    if len(x) >= 2:
                        try:
                            database.write(x[0].strip(), x[1].strip())
                        except Exception as e:
                            bad += 1
                            print("%s:%d: %s (%s: %s)" % (os.path.basename(fn), n, l,
                                                          type(e).__name__, e))
except Exception as e:
    print("dry run could not run: %s: %s" % (type(e).__name__, e))
    sys.stdout.flush()
    os._exit(2)
sys.stdout.flush()
os._exit(1 if bad else 0)
"""


def _fixgw_python(scope: str, unit: str) -> tuple[str, str | None] | None:
    """fix-gateway's interpreter (the shebang of the unit's ExecStart script,
    or the script itself when it is python) and the unit's WorkingDirectory.
    None when it cannot be found. Never raises."""
    show = lambda prop: _systemctl_show(prop, scope, unit)
    m = re.search(r"path=(\S+)", show("ExecStart"))
    if not m:
        return None
    exe = m.group(1)
    # systemd marks a missing-ok WorkingDirectory with a leading "!" (the Pi's
    # fixgw unit shows "!/home/wpballard"); "-" is the unit-file spelling.
    workdir = show("WorkingDirectory").lstrip("!-") or None
    if re.search(r"python[\d.]*$", Path(exe).name):
        return exe, workdir
    try:
        with open(exe, "rb") as f:
            first = f.readline().decode("utf-8", "replace").strip()
    except OSError:
        return None
    if not first.startswith("#!"):
        return None
    interp = first[2:].strip().split()[0]
    return (interp, workdir) if Path(interp).is_file() else None


def validate_ini(fcd: Path, staged: Path) -> tuple[bool | None, str]:
    """Dry-run ``staged`` through fix-gateway's own loader (see _DRY_RUN).

    ``(True, "")`` when fix-gateway would load it, ``(False, why)`` when it
    would refuse a line (and so stop at startup), ``(None, why)`` when the
    check could not run here -- no fix-gateway service, or its interpreter
    cannot be found. Never raises."""
    found = _fixgw_unit()
    if found is None:
        return (None, "no fix-gateway service found to check aircraft.ini against")
    py = _fixgw_python(found[1], found[0])
    if py is None:
        return (None, "could not find fix-gateway's python to check aircraft.ini")
    try:
        out = subprocess.run(
            [py[0], "-c", _DRY_RUN, str(fcd / _FIXGW_MAIN), str(staged), str(fcd / _INI_REL)],
            cwd=py[1], capture_output=True, text=True, timeout=120)
    except Exception as e:
        return (None, f"fix-gateway dry run failed to start: {e}")
    msg = " | ".join(l for l in out.stdout.splitlines() if l.strip())[-600:]
    if out.returncode == 0:
        return (True, "")
    if out.returncode == 1:
        return (False, "fix-gateway would refuse: " + msg)
    return (None, msg or f"fix-gateway dry run exited {out.returncode}")


def _stage_ini(cd: Path, fcd: Path, text: str) -> None:
    _durable_write(fcd / _INI_PENDING_REL, text)
    _update_record(cd, "fixgw_aircraft_ini_pending", _ini_sha(text))


def _drop_pending(cd: Path, fcd: Path) -> None:
    (fcd / _INI_PENDING_REL).unlink(missing_ok=True)
    if "fixgw_aircraft_ini_pending" in _load_record(cd):
        _update_record(cd, "fixgw_aircraft_ini_pending", None)


def pending_ini(cd: Path | None = None, fcd: Path | None = None) -> str | None:
    """The sha256 of the aircraft.ini staged for maintenance mode, or None.
    A staged file whose text no longer matches the recorded hash (a torn
    write) does not count. Never raises."""
    cd = cd or config_dir()
    fcd = fcd or fixgw_config_dir()
    want = _load_record(cd).get("fixgw_aircraft_ini_pending")
    text = _read_text(fcd / _INI_PENDING_REL)
    if isinstance(want, str) and text is not None and _ini_sha(text) == want:
        return want
    return None


def _apply_aircraft_ini(cd: Path, fcd: Path | None, block, maintenance: bool) -> dict:
    """Stage the served aircraft.ini, and in maintenance mode install it.
    Returns ``{"state": ..., "changed": bool}`` (plus ``sha256``, and a
    ``skipped`` reason when nothing could be done). ``state`` is None (not
    managed), ``unchanged``, ``pending`` (staged, waiting for maintenance
    mode) or ``installed``. ``changed`` means fix-gateway must restart."""
    served = _served_ini(block)
    if served is None:
        return {"state": None, "changed": False}
    if isinstance(served, tuple):
        return {"state": None, "changed": False, "skipped": served[1]}
    if fcd is None or not fcd.is_dir():
        return {"state": None, "changed": False, "skipped": "no fix-gateway config dir"}
    sha = _ini_sha(served)
    main = _read_text(fcd / _FIXGW_MAIN)
    need = _ini_tanks(served)
    tanks_ok = not need or (_vars_tanks(_read_text(fcd / _VARS_REL)) or 0) >= need
    if (_read_text(fcd / _INI_REL) == served and main is not None
            and _enrol_ini(main) == main and tanks_ok):
        _drop_pending(cd, fcd)
        return {"state": "unchanged", "changed": False, "sha256": sha}
    _stage_ini(cd, fcd, served)
    if not maintenance:
        return {"state": "pending", "changed": False, "sha256": sha}
    return install_pending_ini(cd, fcd)


def install_pending_ini(cd: Path | None = None, fcd: Path | None = None) -> dict:
    """Install the staged aircraft.ini (maintenance mode only -- the caller
    decides that). Same result shape as _apply_aircraft_ini, plus ``tanks``
    when fix-gateway's tank count was raised. The staged file is checked
    against its recorded hash; fix-gateway's ``t`` is raised to the highest
    FUELQ slot it names (never lowered); it is dry-run through fix-gateway's
    loader, which so sees the raised count; then written in place and listed
    in default.yaml, in that order (a default.yaml naming a missing file
    stops fix-gateway). A raised ``t`` alone loads with the old files, and is
    put back when the install goes no further. A file fix-gateway would
    refuse is dropped; one that could not be checked stays staged."""
    cd = cd or config_dir()
    fcd = fcd or fixgw_config_dir()
    sha = pending_ini(cd, fcd)
    if sha is None:
        return {"state": None, "changed": False}
    staged = fcd / _INI_PENDING_REL
    vars_path = fcd / _VARS_REL
    old_vars = _read_text(vars_path)
    need = _ini_tanks(staged.read_text("utf-8"))
    new_vars = old_vars
    if need:
        new_vars = _raise_tanks(old_vars, need) if old_vars is not None else None
        if new_vars is None:
            return {"state": "pending", "changed": False, "sha256": sha,
                    "skipped": f"aircraft.ini names fuel tank {need}; could not set "
                               f"t: {need} in {vars_path} safely"}
        if new_vars != old_vars:
            _durable_write(vars_path, new_vars)

    def stopped(res: dict) -> dict:
        if new_vars != old_vars:
            _durable_write(vars_path, old_vars)
        return res

    ok, why = validate_ini(fcd, staged)
    if ok is False:
        _drop_pending(cd, fcd)
        return stopped({"state": None, "changed": False, "sha256": sha, "skipped": why})
    if ok is None:
        return stopped({"state": "pending", "changed": False, "sha256": sha, "skipped": why})
    main = _read_text(fcd / _FIXGW_MAIN)
    new_main = _enrol_ini(main) if main is not None else None
    if new_main is None:
        return stopped({"state": "pending", "changed": False, "sha256": sha,
                        "skipped": f"could not list aircraft.ini in {fcd / _FIXGW_MAIN} safely"})
    _durable_write(fcd / _INI_REL, staged.read_text("utf-8"))
    if new_main != main:
        _durable_write(fcd / _FIXGW_MAIN, new_main)
    _drop_pending(cd, fcd)
    res = {"state": "installed", "changed": True, "sha256": sha}
    if new_vars != old_vars:
        res["tanks"] = need
    return res


def _clear_managed_includes(inc: dict, boot: str) -> None:
    """Drop any include keys a prior install (single OR multi) added, so the two
    shapes can switch cleanly and a shrinking screen set leaves no stale entries.
    Only removes OUR ``SCREENS_CONFIG`` (the managed list) -- never a user's."""
    if inc.get("SCREENS_CONFIG") == "screens/managed_list.yaml":
        del inc["SCREENS_CONFIG"]
    inc.pop("SCREEN_MANAGED", None)
    inc.pop(f"SCREEN_{boot}", None)
    for k in [k for k in inc if k.startswith("SCREEN_M_")]:
        del inc[k]


def install_config(yaml_text: str, cd: Path | None = None,
                   fcd: Path | None = None, scope: str | None = None,
                   maintenance: bool = False) -> dict:
    """Install a pulled native config ({main, screens}) as the managed panel.

    ``maintenance`` (the aircraft is on the ground and a person said so)
    lets a served aircraft.ini be installed; without it the file is only
    staged. Returns a summary dict (always with ``boot_screen`` + ``screens``,
    and ``aircraft_ini`` from _apply_aircraft_ini). Raises
    ValueError if the config isn't native form (e.g. an old design blob), so the
    caller can tell the user to re-save.
    """
    import yaml
    cd = cd or config_dir()
    fcd = fcd or fixgw_config_dir()
    doc = yaml.safe_load(yaml_text)
    if not isinstance(doc, dict) or not isinstance(doc.get("screens"), dict) or not doc["screens"]:
        raise ValueError("config is not native pyEfis form (no 'screens:' block) -- "
                         "re-save the panel in the editor")
    screens = doc["screens"]
    prefs = doc.get("preferences")
    default_name = (doc.get("main") or {}).get("defaultScreen")
    if default_name not in screens:
        default_name = next(iter(screens))
    boot = _device_default_screen(cd)
    legacy = legacy_overlay(cd, scope)  # which pyEfis: does virtual_vfr need dbpath?

    _snapshot(cd, fcd)                  # capture the working state for rollback
    if len(screens) == 1:
        summary = _install_single(cd, screens[default_name], boot, prefs, legacy)
    else:
        summary = _install_multi(cd, screens, default_name, boot, prefs, legacy)
    summary["fixgw"] = _apply_fixgw(cd, fcd, doc.get("fixgw"))
    summary["aircraft_ini"] = _apply_aircraft_ini(cd, fcd, doc.get("fixgw"), maintenance)
    summary["config_dir"] = str(cd)
    summary["legacy_overlay"] = legacy
    return summary


def _install_single(cd: Path, screen_def: dict, boot: str, prefs=None,
                    legacy: str = LEGACY_NONE) -> dict:
    """Original proven path: one managed screen named after the device's
    defaultScreen, activated by overriding only that screen's include while
    KEEPING the device's stock screen list."""
    import yaml
    screen_def = _prep_screen(screen_def, legacy)
    _atomic_write(cd / "screens" / "managed.yaml",
                  yaml.safe_dump({boot: screen_def}, sort_keys=False))
    custom = _load_custom(cd)
    inc = custom.setdefault("includes", {})
    _clear_managed_includes(inc, boot)
    inc[f"SCREEN_{boot}"] = "screens/managed.yaml"
    _ensure_boot_loaded(cd, inc, boot)   # boot may be a non-stock name
    units = _apply_preferences(cd, custom, prefs)
    _write_custom(cd, custom)
    return {"mode": "single", "boot_screen": boot, "screens": 1,
            "screen_names": [boot], "units": units}


_STOCK_SCREEN_LIST = "screens/default_list.yaml"
# Fallback stock screen list if the device's can't be read (the shipped default).
_STOCK_FALLBACK_TOKENS = [
    "SCREEN_DATA_STATUS", "SCREEN_PFD", "SCREEN_PFD_AI_ONLY",
    "SCREEN_RADIO", "SCREEN_EMS", "SCREEN_EMS2",
]


def _stock_screen_tokens(cd: Path) -> list[str]:
    """The device's STOCK screen-list tokens (from the BASE ``preferences.yaml``
    ``SCREENS_CONFIG``, not any custom override). Multi-screen keeps this full
    list loaded so the eglfs SVS-GL compositor doesn't segfault on a short list,
    and so every stock screen stays intact (no broken nav references)."""
    import yaml
    rel = _STOCK_SCREEN_LIST
    try:
        prefs = yaml.safe_load((cd / "preferences.yaml").read_text("utf-8")) or {}
        rel = (prefs.get("includes") or {}).get("SCREENS_CONFIG", rel)
    except Exception:
        pass
    try:
        lst = yaml.safe_load((cd / rel).read_text("utf-8")) or {}
        toks = [t for t in (lst.get("include") or []) if isinstance(t, str)]
        if toks:
            return toks
    except Exception:
        pass
    return list(_STOCK_FALLBACK_TOKENS)


def _ensure_boot_loaded(cd: Path, inc: dict, boot: str) -> None:
    """Guarantee ``SCREEN_<boot>`` is in the loaded ``SCREENS_CONFIG`` list.

    Both install paths repurpose the device's ``defaultScreen`` slot -- which is
    normally a STOCK screen already in the list. But if a prior managed config (or
    bench testing) left the device booting into a NON-stock screen name, the
    repurposed ``SCREEN_<boot>`` maps a file yet is never instantiated, and
    ``gui.showScreen`` raises ``KeyError: Screen <boot> Not Found`` on the
    DataStatus continue -> crash-loop. Weave the boot token into the list so the
    screen always loads."""
    import yaml
    boot_token = f"SCREEN_{boot}"
    stock = _stock_screen_tokens(cd)
    if boot_token in stock:
        return
    merged = list(stock) + [boot_token]
    _atomic_write(cd / "screens" / "managed_list.yaml",
                  yaml.safe_dump({"include": merged}, sort_keys=False))
    inc["SCREENS_CONFIG"] = "screens/managed_list.yaml"


def _install_multi(cd: Path, screens: dict, default_name: str, boot: str,
                   prefs=None, legacy: str = LEGACY_NONE) -> dict:
    """Multi-screen path (#72). The hard constraint: a SHORT screen list + the
    SVS GL widget segfaults the eglfs ``QOpenGLCompositor`` (a real eglfs bug that
    correlates with screen count, independent of pyEfis #274). So we KEEP the full
    stock screen list loaded -- it's GL-safe and proven -- and weave the editor's
    screens into it:

      * the DEFAULT editor screen takes the device's ``defaultScreen`` *name* by
        overriding ``SCREEN_<defaultScreen>`` (so boot needs no ``main/`` edit;
        that slot is the device's boot choice, not a nav-button target, so
        repurposing it breaks nothing);
      * each ADDITIONAL editor screen is APPENDED as a new ``SCREEN_M_<name>``
        token onto a copy of the stock list (``managed_list.yaml``), leaving every
        stock screen intact. The list only ever GROWS, so it stays GL-safe.

    Screens are keyed by their file's top-level name (gui.initialize), so the
    editor's own names are preserved for ``show screen`` navigation. Switching is
    an EXPLICIT ``show screen: <next editor screen>`` on each injected button, so
    it cycles only the editor's screens and never lands on the stock ballast."""
    import yaml
    ordered = [default_name] + [n for n in screens if n != default_name]
    used: set[str] = set()
    on_names: list[str] = []
    for n in ordered:
        on_name = boot if n == default_name else _sanitize(n)
        while on_name in used:          # keep on-device screen names unique
            on_name += "_2"
        used.add(on_name)
        on_names.append(on_name)

    custom = _load_custom(cd)
    inc = custom.setdefault("includes", {})
    _clear_managed_includes(inc, boot)

    stock_tokens = _stock_screen_tokens(cd)
    extra_tokens: list[str] = []
    for i, n in enumerate(ordered):
        on_name = on_names[i]
        # switch button -> the NEXT editor screen (wraps); explicit jump.
        nxt = on_names[(i + 1) % len(on_names)]
        suffix = _TS_BUTTON_MAX - (i % _TS_BUTTON_MAX)
        btn_cfg = f"buttons/managed-next-{on_name}.yaml"
        _atomic_write(cd / btn_cfg, _switch_button_cfg(suffix, nxt))

        sdef = _prep_screen(screens[n], legacy)
        sdef["instruments"] = list(sdef.get("instruments") or []) + [_switch_button(btn_cfg)]
        fname = f"managed_{on_name}.yaml"
        _atomic_write(cd / "screens" / fname,
                      yaml.safe_dump({on_name: sdef}, sort_keys=False))

        if i == 0:
            # repurpose the device's default screen slot (keeps the stock list)
            inc[f"SCREEN_{boot}"] = f"screens/{fname}"
        else:
            token = f"SCREEN_M_{on_name}"
            inc[token] = f"screens/{fname}"
            extra_tokens.append(token)

    boot_token = f"SCREEN_{boot}"
    need_boot = boot_token not in stock_tokens   # boot slot may be a non-stock name
    if extra_tokens or need_boot:
        # extend (never shrink) the stock list with the boot slot (if non-stock)
        # + the additional editor screens
        merged = list(stock_tokens)
        if need_boot:
            merged.append(boot_token)
        merged += [t for t in extra_tokens if t not in merged]
        _atomic_write(cd / "screens" / "managed_list.yaml",
                      yaml.safe_dump({"include": merged}, sort_keys=False))
        inc["SCREENS_CONFIG"] = "screens/managed_list.yaml"
    # (boot is stock + no additional screens -> keep the stock SCREENS_CONFIG)

    units = _apply_preferences(cd, custom, prefs)
    _write_custom(cd, custom)
    return {"mode": "multi", "boot_screen": boot, "screens": len(screens),
            "screen_names": on_names, "units": units}


# The systemd unit that runs pyEfis. The Raspberry Pi image runs it as a
# `systemctl --user` service; an x86 appliance (the Beelink) or a hand-rolled
# install may run it as a *system* service. Guessing the scope wrong used to
# look like a panel crash -- the `--user` restart failed, restart_and_verify
# returned False, and config-pull rolled back a perfectly good panel (#23). We
# detect the scope that actually has a loaded pyefis unit, overridable via
# `pyefis_unit_scope` in data.yaml.
_UNIT = "pyefis"

# restart_and_verify outcomes -- a tri-state so the caller can tell a broken
# restart *mechanism* (do not roll back) from a *crashed* panel (roll back).
RESTART_OK = "ok"
RESTART_CRASHED = "crashed"
RESTART_FAILED = "restart-failed"


def _systemctl_base(scope: str) -> tuple[list[str], dict]:
    """The ``systemctl`` argv prefix + environment for ``scope`` ('user' or
    'system'). A headless/boot user-scope invocation needs ``XDG_RUNTIME_DIR``
    to reach the per-user bus; a system-scope one does not (and must not assume
    a uid exists -- ``os.getuid`` is POSIX-only)."""
    env = dict(os.environ)
    if scope == "user":
        if hasattr(os, "getuid"):
            env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        return (["systemctl", "--user"], env)
    return (["systemctl"], env)


def _unit_loaded(scope: str, unit: str = _UNIT) -> bool:
    """True if ``unit`` (default pyefis) is loaded in ``scope`` (LoadState is
    'loaded', not 'not-found'/'masked'/empty). Never raises -- a missing
    systemctl or unreachable bus reads as 'not loaded here'."""
    base, env = _systemctl_base(scope)
    try:
        out = subprocess.run(base + ["show", unit, "-p", "LoadState", "--value"],
                             env=env, capture_output=True, text=True, timeout=10)
    except Exception:
        return False
    return out.stdout.strip() == "loaded"


def _detect_scope() -> str:
    """The systemctl scope that actually manages pyEfis. Prefer 'user' (the Pi
    default); fall back to 'user' if neither scope answers so behaviour on a
    normal Pi is unchanged when systemd probing is unavailable."""
    for scope in ("user", "system"):
        if _unit_loaded(scope):
            return scope
    return "user"


def _resolve_scope(override: str | None = None) -> str:
    """Honour an explicit ``pyefis_unit_scope`` from data.yaml; otherwise detect."""
    if override in ("user", "system"):
        return override
    return _detect_scope()


def restart_pyefis(timeout: int = 120, scope: str | None = None) -> bool:
    """Restart the pyEfis service in the managing systemd scope. Returns True
    iff the restart *command* ran successfully (exit 0) -- NOT whether pyEfis
    then stayed up (that is restart_and_verify's job). A False here means the
    restart *mechanism* failed (wrong scope, no bus, systemctl missing), which
    the caller must NOT treat as a crashed panel (#23).

    The timeout exceeds systemd's stop timeout: an SVS/GL pyEfis can hang on
    SIGTERM for up to its TimeoutStopSec (~90s) before systemd SIGKILLs it, so a
    restart that has to stop a running GL panel can legitimately take that long.
    A too-short timeout would raise and look like a failed restart (a spurious
    rollback)."""
    base, env = _systemctl_base(_resolve_scope(scope))
    try:
        subprocess.run(base + ["restart", _UNIT],
                       check=True, env=env, timeout=timeout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


# fix-gateway's unit: `fixgw` on the Pi image (a venv build), `fixgateway` as
# fix-gateway's own extras/fixgateway.service names it.
_FIXGW_UNITS = ("fixgw", "fixgateway")


def _fixgw_unit() -> tuple[str, str] | None:
    """``(unit, scope)`` of the loaded fix-gateway service, or None."""
    for unit in _FIXGW_UNITS:
        for scope in ("user", "system"):
            if _unit_loaded(scope, unit):
                return unit, scope
    return None


def restart_fixgw(timeout: int = 60) -> bool | None:
    """Restart fix-gateway so it re-reads its config. True on a successful
    restart command, False if it failed, None if no fix-gateway unit is
    loaded in either scope (nothing to restart here). Never raises.

    The caller restarts fix-gateway only in maintenance mode."""
    found = _fixgw_unit()
    if found is None:
        return None
    base, env = _systemctl_base(found[1])
    try:
        subprocess.run(base + ["restart", found[0]], check=True, env=env,
                       timeout=timeout, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def restart_fixgw_and_verify(wait_s: int = 10) -> str | None:
    """Restart fix-gateway and say whether it stayed up: RESTART_OK,
    RESTART_CRASHED (it exited and systemd respawned it, or it is not
    active -- an init file it refuses does exactly this), RESTART_FAILED (the
    restart command failed), or None (no fix-gateway service here)."""
    ok = restart_fixgw()
    if ok is not True:
        return None if ok is None else RESTART_FAILED
    unit, scope = _fixgw_unit() or (_FIXGW_UNITS[0], "user")
    p0 = _systemctl_show("MainPID", scope, unit)
    time.sleep(wait_s)
    p1 = _systemctl_show("MainPID", scope, unit)
    if p0 and p0 != "0" and p0 == p1 and _systemctl_show("ActiveState", scope, unit) == "active":
        return RESTART_OK
    return RESTART_CRASHED


def _systemctl_show(prop: str, scope: str, unit: str = _UNIT) -> str:
    base, env = _systemctl_base(scope)
    try:
        out = subprocess.run(base + ["show", unit, "-p", prop, "--value"],
                             env=env, capture_output=True, text=True, timeout=10)
        return out.stdout.strip()
    except Exception:
        return ""


def _status_log(scope: str, pid: str = "") -> str:
    """Recent service log lines (via ``systemctl status`` -- the user journal
    isn't always reachable through ``journalctl`` here, but the manager surfaces
    the tail). Scoped to ``pid`` when given (lines are ``python[PID]: ...``)."""
    base, env = _systemctl_base(scope)
    try:
        out = subprocess.run(
            base + ["status", _UNIT, "-n", "300", "--no-pager"],
            env=env, capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return ""
    if pid:
        return "\n".join(l for l in out.splitlines() if f"[{pid}]" in l)
    return out


def restart_and_verify(wait_s: int = 16, scope: str | None = None) -> str:
    """Restart pyEfis and classify the outcome as one of:

    * ``RESTART_OK`` -- restarted and the GUI is up (stable active PID, no
      traceback in the log).
    * ``RESTART_CRASHED`` -- the restart command ran, but the new config makes
      pyEfis exit or throw on load; the caller should roll the panel back.
    * ``RESTART_FAILED`` -- the restart *command* itself could not run (the
      pyEfis service is in a systemd scope we can't reach, no bus, or systemctl
      is missing). The panel installed fine; the caller must NOT roll back on
      this -- a broken restart mechanism is not a broken panel (#23).

    pyEfis is Type=simple + Restart=always, so a config that makes it *exit* on
    load shows up as a CHANGED Main PID (systemd respawns it). But a screen-build
    exception does NOT exit the process -- a non-daemon FIX thread keeps it alive
    with a stable PID while the GUI never shows. So PID stability alone is a false
    positive; we also reject a ``Traceback`` logged by the current PID. Returns
    ``RESTART_OK`` only if the PID is stable, active, AND its log is
    traceback-free."""
    scope = _resolve_scope(scope)
    if not restart_pyefis(scope=scope):
        return RESTART_FAILED
    p0 = _systemctl_show("MainPID", scope)
    time.sleep(wait_s)
    p1 = _systemctl_show("MainPID", scope)
    if not (p0 and p0 != "0" and p0 == p1 and _systemctl_show("ActiveState", scope) == "active"):
        return RESTART_CRASHED
    # A *segfault* kills the process, so a stable PID already rules it out; a
    # *screen-build exception* does not (a non-daemon FIX thread lingers), so also
    # reject any of these crash signatures logged by the current PID.
    log = _status_log(scope, p1)
    if any(sig in log for sig in (
        "Traceback (most recent call last)",
        "Fatal Python error",
        "Segmentation fault",
        "Unable to load module",
    )):
        return RESTART_CRASHED
    return RESTART_OK


def rollback(cd: Path | None = None) -> str:
    """Revert to the last working config after a failed swap: restore every file
    captured in the pre-install ``.panel_backup/`` snapshot (the override + all
    managed screen/button files). Falls back to the pristine (stock) override if
    there's no snapshot. The caller restarts pyEfis afterwards."""
    cd = cd or config_dir()
    manifest = cd / _BACKUP_DIR / "manifest.txt"
    if manifest.exists():
        rels = [r.strip() for r in manifest.read_text("utf-8").splitlines() if r.strip()]
        restored_any = False
        for rel in rels:
            src = cd / _BACKUP_DIR / rel
            if src.exists():
                _atomic_write(cd / rel, src.read_text("utf-8"))
                restored_any = True
        if restored_any:
            # A preferences record the failed install created (none before it)
            # would claim unit groups the restored override no longer has.
            if _PREFS_RECORD not in rels:
                (cd / _PREFS_RECORD).unlink(missing_ok=True)
            return "previous panel"
    prepanel = cd / "preferences.yaml.custom.prepanel"
    if prepanel.exists():
        _atomic_write(cd / "preferences.yaml.custom", prepanel.read_text("utf-8"))
        return "stock config"
    return "nothing to restore"


def rollback_fixgw(cd: Path | None = None, fcd: Path | None = None) -> bool:
    """Put the fix-gateway files config-pull writes back as the pre-install
    snapshot holds them (removing one the failed install created):
    default.yaml first, so it never lists an aircraft.ini that is then
    removed, then aircraft.ini, then variables.yaml, so the tank count only
    drops once no file names the higher slots, then preferences.yaml.custom.
    Returns True if any file changed, so the caller knows to restart
    fix-gateway."""
    cd = cd or config_dir()
    fcd = fcd or fixgw_config_dir()
    bk = cd / _BACKUP_DIR
    changed = False
    for rel, saved_rel, write in ((_FIXGW_MAIN, _MAIN_BACKUP, _durable_write),
                                  (_INI_REL, _INI_BACKUP, _durable_write),
                                  (_VARS_REL, _VARS_BACKUP, _durable_write),
                                  (_FIXGW_CUSTOM, _FIXGW_BACKUP, _atomic_write)):
        path = fcd / rel
        saved = bk / saved_rel
        if saved.exists():
            text = saved.read_text("utf-8")
            if not path.exists() or path.read_text("utf-8") != text:
                write(path, text)
                changed = True
        elif (bk / (saved_rel + ".absent")).exists() and path.exists() \
                and rel not in (_FIXGW_MAIN, _VARS_REL):   # config-pull never creates these
            path.unlink()
            changed = True
    return changed
