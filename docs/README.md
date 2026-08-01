# docs/ index

Routing file for this docs tree: what each document is and whether it is
**live reference** or a **historical record**. Plans that shipped are kept for
the record — check status here before acting on any individual doc.

Conventions match the pyEfis docs index: new docs carry a dated `Status:`
header; executed plans get marked `COMPLETE — historical` in place; when a
doc's guidance needs correcting twice, fix the doc (edit the source, not the
output); adding a doc means adding its line here. Live workstream state lives
in the workspace ledger (`makerplane/STATE.md`, local umbrella dir).

## Reference & runbooks (live — trust these)

| Doc | What |
|---|---|
| [cloudflare_setup.md](cloudflare_setup.md) | Reproduce-from-nothing Cloudflare runbook (R2, Worker, D1) |
| [ci_build_pipeline.md](ci_build_pipeline.md) | CI / daily cyclical build pipeline |
| [release_process.md](release_process.md) | **PROCESS** — versioned `dev→qa→main` releases + tag; the kind-on-main-before-publish rule (adopted 2026-08-01) |
| [panel_config_format.md](panel_config_format.md) | Panel/screen config format the configurator emits |
| [device_deployment.md](device_deployment.md) | Device pairing + config-pull deployment path |
| [on_device_pack_picker.md](on_device_pack_picker.md) | On-device pack picker (shipped) |
| [terrain.md](terrain.md) · [water.md](water.md) · [roads.md](roads.md) | Per-kind pack build notes |
| [LICENSE-AUDIT.md](LICENSE-AUDIT.md) | Component → license map (authoritative with per-file SPDX) |
| [AC-DP-001-architecture-disclosure.md](AC-DP-001-architecture-disclosure.md) | Defensive publication — awaiting Bill's TDCommons submission |

## Specs & plans (open)

| Doc | Status |
|---|---|
| [environments.md](environments.md) | SPEC, §9 locked 2026-07-08 — DEV/QA/PROD env split; **implementation not started** |
| [dev_navdata_environment.md](dev_navdata_environment.md) | DESIGN NOTE 2026-08-01 — a DEV pack origin (`dev.navdata`); fills the "navdata OUT" gap in environments.md; **open decisions for Bill** |
| [canfix_configurator.md](canfix_configurator.md) | SPEC — V-speed/gauge-band profiles → fixgw init_data; **awaiting Bill's review** |
| [control_bindings.md](control_bindings.md) | SPEC — control-bindings umbrella (Button shipped) |
| [knob_encoder_configurator.md](knob_encoder_configurator.md) | SPEC — knob/encoder bindings (awaiting hardware) |
| [soft_controls_configurator.md](soft_controls_configurator.md) | Soft-controls configurator notes |
| [nasr_2601_dpn_prep.md](nasr_2601_dpn_prep.md) | Prep for the NASR 26-01 DPN subscriber change (companion PDF here) |
| [regionalize_bulk_packs.md](regionalize_bulk_packs.md) | PLAN — extend the provider model to water/highways/obstacles |
| [canadian_airports.md](canadian_airports.md) | Airport provider model via OurAirports — prototype proven |
| [europe_coverage.md](europe_coverage.md) | Parking lot — "not started, future Bill" |
| [svs_runway_terrain.md](svs_runway_terrain.md) | Investigation complete; decision DEFERRED to real-flight validation |
| [aircraft_params_schema_env_prefix_fix.md](aircraft_params_schema_env_prefix_fix.md) | Env-prefix R2 gotcha note (untracked — should be committed) |

## Historical (executed — record only)

| Doc | Why kept |
|---|---|
| [data_manager_strategy.md](data_manager_strategy.md) | Strategy behind the shipped system (canonical copy; phases A–F live) |
| [data_manager_implementation.md](data_manager_implementation.md) | Phase plan A–G — execution record; A–F complete and live |
| [terrain_cloud_build_plan.md](terrain_cloud_build_plan.md) | Executed — terrain build is LIVE on the QNAP (EC2 remains the lift path) |
| [terrain_github_runner_plan.md](terrain_github_runner_plan.md) | PLAN, deferred — QNAP-as-runner variant, superseded by the direct QNAP build |
