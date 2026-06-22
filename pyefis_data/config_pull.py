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

The shipped config files are never modified. Before each install the current
panel state is snapshotted to ``.panel_backup/`` so a config that crashes pyEfis
is rolled back to the last working panel. Every write is atomic (temp +
``os.replace``).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

# A real UA: Cloudflare's edge 403s the default "Python-urllib/*".
_UA = "pyefis-data/0.1 (+https://github.com/makerplane/makerplane-data)"
_DEFAULT_PYEFIS_CONFIG = "~/makerplane/pyefis/config"

# Snapshot dir (under the config dir) holding the pre-install panel state, used
# by rollback() to restore the last working panel after a failed swap.
_BACKUP_DIR = ".panel_backup"

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


def fetch_config(cfg) -> tuple[str, int | None, str | None]:
    """GET <configurator>/device/config with the device token.

    Returns ``(status, version, yaml_text)`` where status is one of
    ``updated`` (new config in yaml_text), ``up-to-date`` (304), ``none`` (404),
    ``unpaired`` (no token), or ``error:<msg>``.
    """
    if not cfg.device_token:
        return ("unpaired", None, None)
    headers = {"Authorization": f"Bearer {cfg.device_token}", "User-Agent": _UA}
    if cfg.config_version:
        headers["If-None-Match"] = f'"v{cfg.config_version}"'
    url = f"{cfg.configurator_url.rstrip('/')}/device/config"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                    timeout=30) as resp:
            text = resp.read().decode("utf-8")
            ver = resp.headers.get("X-Config-Version")
            return ("updated", int(ver) if ver else None, text)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return ("up-to-date", cfg.config_version, None)
        if e.code == 404:
            return ("none", None, None)
        return (f"error:HTTP {e.code}", None, None)
    except Exception as e:                       # network / TLS / timeout
        return (f"error:{e}", None, None)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


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


def _prep_screen(screen_def: dict) -> dict:
    """Inject the device-side bits the editor can't know into one screen:

    * a ``virtual_vfr`` gets an ``svs`` options block (terrain/airport/water data
      paths) so it renders synthetic vision, and the stock
      ``screens/virtualvfr_db.yaml`` include (a screen-level ``dbpath`` -- without
      it the widget crashes on a ``None``).
    Returns a new screen dict (the input is not mutated)."""
    screen_def = dict(screen_def)
    has_vfr = False
    insts = []
    for inst in screen_def.get("instruments", []):
        if isinstance(inst, dict) and inst.get("type") == "virtual_vfr":
            has_vfr = True
            inst = dict(inst)
            opts = dict(inst.get("options") or {})
            opts.setdefault("svs", dict(_SVS_OPTS))   # keep an explicit svs if present
            inst["options"] = opts
        insts.append(inst)
    screen_def["instruments"] = insts
    if has_vfr:
        inc = list(screen_def.get("include") or [])
        if "screens/virtualvfr_db.yaml" not in inc:
            inc.append("screens/virtualvfr_db.yaml")
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
    rels = ["preferences.yaml.custom"]
    sd = cd / "screens"
    if sd.is_dir():
        rels += [f"screens/{p.name}" for p in sorted(sd.glob("managed*.yaml"))]
    bd = cd / "buttons"
    if bd.is_dir():
        rels += [f"buttons/{p.name}" for p in sorted(bd.glob("managed-next*.yaml"))]
    return rels


def _snapshot(cd: Path) -> None:
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
    # Keep a one-time PRISTINE override (pre-ANY-panel) as the ultimate fallback.
    custom = cd / "preferences.yaml.custom"
    prepanel = cd / "preferences.yaml.custom.prepanel"
    if custom.exists() and not prepanel.exists():
        _atomic_write(prepanel, custom.read_text("utf-8"))


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


def install_config(yaml_text: str, cd: Path | None = None) -> dict:
    """Install a pulled native config ({main, screens}) as the managed panel.

    Returns a summary dict (always with ``boot_screen`` + ``screens``). Raises
    ValueError if the config isn't native form (e.g. an old design blob), so the
    caller can tell the user to re-save.
    """
    import yaml
    cd = cd or config_dir()
    doc = yaml.safe_load(yaml_text)
    if not isinstance(doc, dict) or not isinstance(doc.get("screens"), dict) or not doc["screens"]:
        raise ValueError("config is not native pyEfis form (no 'screens:' block) -- "
                         "re-save the panel in the editor")
    screens = doc["screens"]
    default_name = (doc.get("main") or {}).get("defaultScreen")
    if default_name not in screens:
        default_name = next(iter(screens))
    boot = _device_default_screen(cd)

    _snapshot(cd)                       # capture the working state for rollback
    if len(screens) == 1:
        summary = _install_single(cd, screens[default_name], boot)
    else:
        summary = _install_multi(cd, screens, default_name, boot)
    summary["config_dir"] = str(cd)
    return summary


def _install_single(cd: Path, screen_def: dict, boot: str) -> dict:
    """Original proven path: one managed screen named after the device's
    defaultScreen, activated by overriding only that screen's include while
    KEEPING the device's stock screen list."""
    import yaml
    screen_def = _prep_screen(screen_def)
    _atomic_write(cd / "screens" / "managed.yaml",
                  yaml.safe_dump({boot: screen_def}, sort_keys=False))
    custom = _load_custom(cd)
    inc = custom.setdefault("includes", {})
    _clear_managed_includes(inc, boot)
    inc[f"SCREEN_{boot}"] = "screens/managed.yaml"
    _write_custom(cd, custom)
    return {"mode": "single", "boot_screen": boot, "screens": 1,
            "screen_names": [boot]}


_STOCK_SCREEN_LIST = "screens/default_list.yaml"
# Fallback stock screen list if the device's can't be read (the shipped default).
_STOCK_FALLBACK_TOKENS = [
    "SCREEN_DATA_STATUS", "SCREEN_SIXPACK", "SCREEN_PFD", "SCREEN_PFD_AI_ONLY",
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


def _install_multi(cd: Path, screens: dict, default_name: str, boot: str) -> dict:
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

        sdef = _prep_screen(screens[n])
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

    if extra_tokens:
        # extend (never shrink) the stock list with the additional editor screens
        merged = list(stock_tokens) + [t for t in extra_tokens if t not in stock_tokens]
        _atomic_write(cd / "screens" / "managed_list.yaml",
                      yaml.safe_dump({"include": merged}, sort_keys=False))
        inc["SCREENS_CONFIG"] = "screens/managed_list.yaml"
    # (no additional screens -> keep the stock SCREENS_CONFIG untouched)

    _write_custom(cd, custom)
    return {"mode": "multi", "boot_screen": boot, "screens": len(screens),
            "screen_names": on_names}


def restart_pyefis(timeout: int = 120) -> bool:
    """Restart the pyEfis user service. Returns True on success.

    The timeout exceeds systemd's stop timeout: an SVS/GL pyEfis can hang on
    SIGTERM for up to its TimeoutStopSec (~90s) before systemd SIGKILLs it, so a
    restart that has to stop a running GL panel can legitimately take that long.
    A too-short timeout would raise and look like a failed restart (a spurious
    rollback)."""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        subprocess.run(["systemctl", "--user", "restart", "pyefis"],
                       check=True, env=env, timeout=timeout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _systemctl_show(prop: str) -> str:
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        out = subprocess.run(["systemctl", "--user", "show", "pyefis", "-p", prop, "--value"],
                             env=env, capture_output=True, text=True, timeout=10)
        return out.stdout.strip()
    except Exception:
        return ""


def _status_log(pid: str = "") -> str:
    """Recent service log lines (via ``systemctl status`` -- the user journal
    isn't always reachable through ``journalctl`` here, but the manager surfaces
    the tail). Scoped to ``pid`` when given (lines are ``python[PID]: ...``)."""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        out = subprocess.run(
            ["systemctl", "--user", "status", "pyefis", "-n", "300", "--no-pager"],
            env=env, capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return ""
    if pid:
        return "\n".join(l for l in out.splitlines() if f"[{pid}]" in l)
    return out


def restart_and_verify(wait_s: int = 16) -> bool:
    """Restart pyEfis and confirm it actually came up with a working GUI.

    pyEfis is Type=simple + Restart=always, so a config that makes it *exit* on
    load shows up as a CHANGED Main PID (systemd respawns it). But a screen-build
    exception does NOT exit the process -- a non-daemon FIX thread keeps it alive
    with a stable PID while the GUI never shows. So PID stability alone is a false
    positive; we also reject a ``Traceback`` logged by the current PID. Returns
    True only if the PID is stable, active, AND its log is traceback-free."""
    if not restart_pyefis():
        return False
    p0 = _systemctl_show("MainPID")
    time.sleep(wait_s)
    p1 = _systemctl_show("MainPID")
    if not (p0 and p0 != "0" and p0 == p1 and _systemctl_show("ActiveState") == "active"):
        return False
    # A *segfault* kills the process, so a stable PID already rules it out; a
    # *screen-build exception* does not (a non-daemon FIX thread lingers), so also
    # reject any of these crash signatures logged by the current PID.
    log = _status_log(p1)
    return not any(sig in log for sig in (
        "Traceback (most recent call last)",
        "Fatal Python error",
        "Segmentation fault",
        "Unable to load module",
    ))


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
            return "previous panel"
    prepanel = cd / "preferences.yaml.custom.prepanel"
    if prepanel.exists():
        _atomic_write(cd / "preferences.yaml.custom", prepanel.read_text("utf-8"))
        return "stock config"
    return "nothing to restore"
