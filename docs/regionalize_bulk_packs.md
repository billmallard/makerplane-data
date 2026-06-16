# Regionalize the bulk packs (provider model for water / highways / obstacles)

Extend the airport provider model (already shipped — see
[canadian_airports.md](canadian_airports.md)) to the other major SVS data
elements, so they become region/source packs the SVS auto-discovers and merges.

## Two flavors of "provider"

The provider model is one mechanism with two uses; which applies decides the value:

1. **Multi-source MERGE** — different authoritative sources per region, queried
   together. Airports (FAA US + OurAirports Canada + …). Value = *coverage*
   beyond a single source.
2. **Region-split from one global source** — same source, smaller per-region
   packs (the terrain model). Value = *download size*, not coverage.

## Per-element recommendation

| Element | Source(s) | Recommendation | Rationale |
|---|---|---|---|
| **Water** | OSM (one) | **Do it** | The pack is **~3.9 GB for all of US+Canada**. Region-split (`water-us-east`, `water-canada-east`, …) means a pilot downloads only their region — the same win terrain already gives. Strongest case. |
| **Highways** | OSM (one) | Do it alongside water | Same OSM pipeline; cheap. But only ~108 MB total, so low urgency on its own. |
| **Obstacles** | FAA DOF (US only) | Make provider-*capable*; defer data | Merge code mirrors airports, but **no free second source exists** (NAV CANADA paywalled; OSM towers aren't aviation-grade). Within-US region-split of one 75 MB national pack adds little. Wire a provider when a source appears. |
| **Terrain** | Copernicus (one) | Already correct | Region packs from one global source. Leave it. |

## The mechanism is proven (airports)

Applying it to each element mirrors the airport work almost verbatim:

1. **SVS reader** reads a provider directory and merges spatial queries
   (`_MultiAirportDB` → `_MultiWaterDB` / `_MultiHighwayDB` / `_MultiObstacleDB`).
   Each reader already does single-DB R-tree/bbox queries; the wrapper opens the
   installed region DBs and chains them (only in-range regions return rows).
2. **Updater**: the per-pack-id `airports` install kind generalizes — add
   `water` / `highways` / `obstacles` provider kinds (or one shared scheme) so
   several region packs of a kind coexist under `<kind>/<pack_id>/current/`.
3. **Build**: region-split builds (water already builds per-region from OSM
   Geofabrik; just emit per-region packs instead of one `water-na`).

Effort: roughly a day each (reader + region builds + redeploy), lower-risk
because it's the same shape as airports.

## Sequencing

Land the current PR first (it carries the airport provider model as the
template), then do **water + highways** as a focused "regionalize" phase.
Obstacles waits on a data source.

## Acceptance

- Water/highways install as region packs selectable in the on-device picker; a
  pilot downloads only their region(s); the SVS merges all installed region DBs
  and renders identically to the single-pack version where regions overlap.
- The 3.9 GB `water-na` monolith is replaced by region packs (with a migration
  note for installed units).
