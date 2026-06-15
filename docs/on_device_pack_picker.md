# On-device pack picker — design / spec

**Status: planned, not built.** This is the agreed design for letting users
choose and install data packs directly from the pyEfis boot **Data Status**
screen, so end users never have to edit `data.yaml` by hand. Decisions below
are locked (Bill, 2026-06-15); phasing at the end.

## Goal

Tapping **Update** on the boot Data Status screen should let the user:
1. pick the source (Internet vs an inserted USB drive),
2. see every available pack from the verified catalog and check what they want,
3. choose where it's stored (with free-space awareness), and
4. install — persisting the choice so nightly auto-update keeps that exact set
   current.

The yaml becomes optional: the picker *is* the editor.

## What already exists (reuse, don't rebuild)

- **Update button** runs the updater via `QProcess` and refreshes `status.json`
  on completion (`pyEfis .../instruments/data_status/__init__.py`).
- **Trust + install plumbing is done:** the manifest is signature-verified
  against an embedded key before it's trusted or cached; offline it falls back
  to the last *verified* cached manifest; a pack downloads to `staging/`,
  sha256-verifies, then atomically swaps `current` — a failed/lost download can
  never disturb the data a running pyEfis is serving (`core.py`).
- **Two transports already abstracted:** `HttpRemote` (network) and
  `LocalDirRemote` (USB/local); `import_dir()` verifies a USB manifest with the
  same embedded key.

So the missing pieces are catalog listing, ad-hoc selection install, storage
selection, and the picker UI.

## Locked decisions

1. **Persist the selection to `data.yaml`** (`packs:`), don't one-shot. The
   picker becomes the yaml editor; nightly auto-update tracks the user's picks.
2. **Pre-check from the *resolved* tracked set**, not the literal `packs:` list.
   Default the checkboxes from `_tracked_ids(manifest)` (which unifies
   `packs` + `track_kinds` + `regions`). This shows the true current state even
   on a fresh Pi that tracks core navdata via `track_kinds` defaults with an
   empty `packs:`. On persist, write the resolved set out as an explicit
   `packs:` list — quietly migrating the user off the implicit defaults.
3. **Offer a source choice when a USB is present** ("Update from: Internet /
   USB"), default-highlight Internet but treat an inserted stick as likely
   intent. If only one source is available, use it silently.
4. **Show terrain in the picker with guards** (don't hide it). Terrain packs are
   8-30 GB each; show size prominently and gate on free space.
5. **Storage location is user-selectable**, enumerated from the device, and a
   **removable drive is allowed as the data root with a clear warning** (some
   builders run a dedicated USB SSD). Default hard to the fixed internal drive.

## Backend additions (pyefis_data)

- **`pyefis-data catalog --json [--source <dir>]`** — every pack in the verified
  manifest with what the picker needs: id, label, kind, size, currency/severity,
  regions, and `installed` / `tracked` flags. Generalizes today's `status`
  (which only reports the tracked subset) to the whole catalog. Works against
  network (`HttpRemote`) or a USB dir (`LocalDirRemote`).
- **Install an explicit selection** — `update --only id,id,…` that (a) writes the
  selection into `data.yaml` `packs:` and (b) installs. Needs a small config
  *writer* (config is load-only today): read the existing yaml dict, set `packs`
  (and `root` when changed), re-dump. Comment preservation is best-effort; a
  clean canonical rewrite is acceptable since the picker now owns the file.
- **`pyefis-data drives --json`** — enumerate writable mounted filesystems with
  free/total space and a removable/fixed flag, so the Linux mount-parsing lives
  in tested Python rather than the Qt layer. Filter out tmpfs/overlay/boot/
  system and read-only mounts, and anything too small for the smallest selected
  pack.
- **Progress events** — emit JSON-line progress on stdout
  (`{"pack":"terrain-us-west","pct":42}`) so the GUI's existing `QProcess`
  reader can drive a real progress bar (terrain is gigabytes). `fetch.download`
  gains a progress callback.

## UI additions (pyEfis data_status)

A picker view as a second "page" of the Data Status screen:
- source choice (Internet / USB) when both are present;
- scrollable, touch-friendly checkboxes grouped by kind (navigation / terrain /
  water / roads), each with size + currency badge;
- pre-checked from the resolved tracked set (decision 2);
- a **selection-size vs free-space** readout and a **storage-location** control
  ("Installing to /data — 346 GB free · Change…");
- progress view (parses backend progress events);
- result summary (installed / failed per pack), then back to status.

## Offline / no-network / no-USB handling

Detect **up front** (don't attempt-then-fail):
- **Neither available** → "No internet connection and no USB drive found. Your
  current data is unchanged." → back to status.
- **USB present but no/invalid data** → "The USB drive has no valid MakerPlane
  data."
- **Network chosen but server unreachable** → "Couldn't reach the update
  server" + offer USB if present.
- **Connection lost mid-download** → already safe (failed pack leaves `current`
  untouched; resumable download can resume). Show a per-pack succeeded/failed
  summary.

Non-negotiable: none of these ever block **Continue** or stall the boot — the
EFIS informs, never restricts.

## Storage-location caveats (why it's more than a dropdown)

- **Mount stability.** The fixed M.2 at `/data` is an fstab mount, stable across
  boots. An auto-mounted USB SSD lands at `/media/<user>/<LABEL>` and may move
  or not mount next boot, at which point terrain "disappears" (SVS degrades
  gracefully, but the user sees empty data). Allow removable **with a warning**;
  recommend a fixed/internal drive; keep plain USB sticks as the import path,
  not the permanent home.
- **No auto-migration (Phase 1).** Changing `root` doesn't move already-installed
  packs — they orphan at the old root and the new root starts empty. Phase 1:
  warn ("This won't move data you already downloaded"). Phase 2 could detect the
  old root and offer to relocate.
- **`status.json` is unaffected** — it lives at the fixed
  `~/.makerplane/pyefis/status.json`, not under `root`, so changing the data
  location never breaks the EFIS finding its status.

## Phasing

- **Phase 1** — catalog + selection (persisted to yaml, pre-checked from the
  resolved set) + source choice + offline/no-USB handling + simple
  "installing X of N" progress. Surface *current root + free space* read-only.
- **Phase 2** — drive enumeration + storage-location selection (allow-removable-
  with-warning) + free-space enforcement + byte-level progress bars + storage
  warnings. Optional: offer to relocate existing data when `root` changes.

## Related

- [data_manager_strategy.md](data_manager_strategy.md) — pack/manifest design
- the boot Data Status screen lives in pyEfis
  `src/pyefis/instruments/data_status/__init__.py`
- updater backend: `pyefis_data/{cli,core,config}.py`
