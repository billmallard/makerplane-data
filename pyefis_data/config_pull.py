"""Device panel-config pull + install (#65 P3).

A paired device fetches its latest panel config (native pyEfis YAML) from the
configuration manager (``pyefis.aerocommons.org``) and installs it into the
pyEfis config dir, then restarts pyEfis. Uses the device token stored by
``pyefis-data pair``.

Install strategy -- least-surgery and fully reversible. The pulled config is one
screenbuilder ``screen`` (plus a ``main`` block). We:

  * write it as a *managed* screen file, named after the device's EXISTING
    ``main/default.yaml`` ``defaultScreen`` -- so the boot screen flips to the
    panel without editing ``main/`` at all;
  * point ``SCREENS_CONFIG`` at a one-entry managed screen list, by MERGING two
    keys into ``preferences.yaml.custom`` (pyEfis's supported override that
    layers over ``preferences.yaml``).

The shipped config files are never modified. Uninstalling = drop the two
override keys (a ``preferences.yaml.custom.prepanel`` backup of the pristine
override is kept). Every write is atomic (temp + ``os.replace``).
"""
from __future__ import annotations

import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

# A real UA: Cloudflare's edge 403s the default "Python-urllib/*".
_UA = "pyefis-data/0.1 (+https://github.com/makerplane/makerplane-data)"
_DEFAULT_PYEFIS_CONFIG = "~/makerplane/pyefis/config"

# Standard on-device SVS data layout (the makerplane-data updater installs here).
# Injected into a virtual_vfr instrument so it renders terrain, mirroring the
# stock includes/ahrs/svs.yaml block. A device with a usable GPU + this data
# shows synthetic vision; without, SVS self-disables to sky/ground + an
# "SVS UNAVAIL" flag (never fatal). Needs pyEfis #71's AI redraw guard, and the
# boot-screen-override install below (a short screen list segfaults the GL).
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
    """The device's current ``defaultScreen`` (so the managed screen can take its
    name and the boot screen flips without touching ``main/``). Defaults to
    PANEL if unset/unreadable."""
    import yaml
    try:
        main = yaml.safe_load((cd / "main" / "default.yaml").read_text("utf-8")) or {}
        ds = main.get("defaultScreen")
        if isinstance(ds, str) and ds.strip():
            return ds.strip()
    except Exception:
        pass
    return "PANEL"


def install_config(yaml_text: str, cd: Path | None = None) -> dict:
    """Install a pulled native config ({main, screens}) as the managed panel.

    Returns a summary dict. Raises ValueError if the config isn't native form
    (e.g. an old design blob), so the caller can tell the user to re-save.
    """
    import yaml
    cd = cd or config_dir()
    doc = yaml.safe_load(yaml_text)
    if not isinstance(doc, dict) or not isinstance(doc.get("screens"), dict) or not doc["screens"]:
        raise ValueError("config is not native pyEfis form (no 'screens:' block) -- "
                         "re-save the panel in the editor")
    # Deploy the editor's chosen default screen (a panel may define several; full
    # multi-screen deploy + switching is a follow-up, #72). Fall back to the first.
    screens = doc["screens"]
    default_name = (doc.get("main") or {}).get("defaultScreen")
    screen_def = dict(screens[default_name] if default_name in screens
                      else next(iter(screens.values())))
    boot = _device_default_screen(cd)

    # virtual_vfr needs device-side config the editor can't know: a screen-level
    # dbpath (via the stock virtualvfr_db include, else it crashes on a None) and
    # an `svs` options block pointing at the on-device terrain/airport/water data
    # (else it only shows sky/ground). Both use the standard makerplane-data
    # layout. (Needs pyEfis #71's AI redraw guard, on gpu-required.)
    has_vfr = False
    insts = []
    for inst in screen_def.get("instruments", []):
        if isinstance(inst, dict) and inst.get("type") == "virtual_vfr":
            has_vfr = True
            inst = dict(inst)
            opts = dict(inst.get("options") or {})
            opts.setdefault("svs", dict(_SVS_OPTS))   # enable terrain; keep an explicit svs if present
            inst["options"] = opts
        insts.append(inst)
    screen_def["instruments"] = insts
    if has_vfr:
        inc = list(screen_def.get("include") or [])
        if "screens/virtualvfr_db.yaml" not in inc:
            inc.append("screens/virtualvfr_db.yaml")
        screen_def["include"] = inc

    # 1) the managed screen, named after the device's existing default screen.
    #    Keep the previous panel as .bak so a crash can roll back to it.
    managed = cd / "screens" / "managed.yaml"
    if managed.exists():
        _atomic_write(cd / "screens" / "managed.yaml.bak", managed.read_text("utf-8"))
    _atomic_write(managed, yaml.safe_dump({boot: screen_def}, sort_keys=False))

    # 2) activate by overriding ONLY the boot screen's include to point at the
    #    managed screen -- KEEP the device's stock screen list (SCREENS_CONFIG).
    #    Replacing the whole list with a short managed list segfaults the eglfs
    #    SVS-GL compositor (#71); keeping the full set avoids it. Merge into
    #    preferences.yaml.custom (don't clobber existing custom).
    custom_path = cd / "preferences.yaml.custom"
    try:
        custom = yaml.safe_load(custom_path.read_text("utf-8")) or {}
    except Exception:
        custom = {}
    if not isinstance(custom, dict):
        custom = {}
    # Back up the PRISTINE override once, for a clean uninstall/rollback.
    backup = cd / "preferences.yaml.custom.prepanel"
    if custom_path.exists() and not backup.exists():
        _atomic_write(backup, custom_path.read_text("utf-8"))
    inc = custom.setdefault("includes", {})
    # Undo any older-style managed_list override so the stock screen set loads.
    if inc.get("SCREENS_CONFIG") == "screens/managed_list.yaml":
        del inc["SCREENS_CONFIG"]
    inc.pop("SCREEN_MANAGED", None)
    inc[f"SCREEN_{boot}"] = "screens/managed.yaml"
    _atomic_write(custom_path, yaml.safe_dump(custom, sort_keys=False))
    return {"boot_screen": boot, "config_dir": str(cd)}


def restart_pyefis() -> bool:
    """Restart the pyEfis user service. Returns True on success."""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        subprocess.run(["systemctl", "--user", "restart", "pyefis"],
                       check=True, env=env, timeout=40,
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


def restart_and_verify(wait_s: int = 14) -> bool:
    """Restart pyEfis and confirm it STAYS up. pyEfis is Type=simple +
    Restart=always, so a config that crashes it on load shows up as a CHANGED
    Main PID a few seconds later (systemd respawns it). Returns True if healthy."""
    if not restart_pyefis():
        return False
    p0 = _systemctl_show("MainPID")
    time.sleep(wait_s)
    p1 = _systemctl_show("MainPID")
    return bool(p0) and p0 != "0" and p0 == p1 and _systemctl_show("ActiveState") == "active"


def rollback(cd: Path | None = None) -> str:
    """Revert to the last working config after a failed swap: restore the previous
    managed panel if there is one, else the pristine (stock) override. The caller
    restarts pyEfis afterwards."""
    cd = cd or config_dir()
    bak = cd / "screens" / "managed.yaml.bak"
    if bak.exists():
        _atomic_write(cd / "screens" / "managed.yaml", bak.read_text("utf-8"))
        return "previous panel"
    prepanel = cd / "preferences.yaml.custom.prepanel"
    if prepanel.exists():
        _atomic_write(cd / "preferences.yaml.custom", prepanel.read_text("utf-8"))
        return "stock config"
    return "nothing to restore"
