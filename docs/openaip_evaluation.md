# openAIP evaluation — license posture, format fit, refresh cadence

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

**Status: evaluation and design only. Nothing built, nothing published.**
Answers `billmallard/makerplane-data#41`. Airspace is the first candidate
because we ship nothing for that layer today; everything we ship is
FAA-sourced and therefore CONUS-only. openAIP is worldwide, community-run
aeronautical data — the obvious way to extend past that boundary.

Companion reading: [europe_coverage.md](europe_coverage.md) already parked
openAIP as a "verify current terms before relying on it" TODO — this doc is
that re-verification, done. [data_manager_strategy.md](data_manager_strategy.md)
and [water.md](water.md)/[roads.md](roads.md) are the precedent for carrying a
non-public-domain, attribution-required source through this pipeline (OSM
ODbL). **#42 (OpenAIR v1/v2 ingest) is a separate, independent issue** — this
document does not touch OpenAIR parsing.

## Verdict up front

**The license path works**, on a narrower basis than the issue's carve-out
reading needed: under CC's own "NonCommercial" definition, a free,
no-revenue distribution isn't commercial use in the first place, so the
"don't exclusively sell" clause openAIP describes for paid apps likely
doesn't even apply to us. **Carry, don't relicense** — a pack declares
openAIP's license and required attribution in the manifest; makerplane-data
stays Apache-2.0 for code. The mechanism is a small, additive, backward-compatible
schema extension (`license` / `license_url` alongside the existing
`attribution` field). Format: GeoJSON in, SQLite out, following the existing
water/roads shape. Cadence: static per-country exports as the primary pull,
matching how every other source in `packtools/sources.py` works today.

Recommend Bill still send the direct-confirmation email (§ License path,
"Contacting openAIP" below) — it's cheap and converts a careful reading into
an answer, and it's also the natural moment to raise the donation.

## License path

### What the license actually is, verified today

The issue's reading (CC BY-NC 4.0, Garrecht Avionik GmbH) is correct, and I
confirmed it independently against openAIP's own live API metadata rather
than secondary pages, because secondary sources disagree with each other —
several current search results describe openAIP as **CC BY-NC-SA**
(ShareAlike), which is a materially different license (it would require any
derivative pack to carry the *same* license forward, not just attribution).
`europe_coverage.md`'s existing TODO flagged exactly this risk: "verify
current terms before relying on it."

Pulled `https://api.core.openaip.net/api/system/specs/v1/schema.json`
(openAIP's own OpenAPI document, served live, 2026-08-23):

```json
"license": {
  "name": "Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)",
  "url": "https://creativecommons.org/licenses/by-nc/4.0/"
}
```

Plain **CC BY-NC 4.0**, no ShareAlike. That schema document also carries their
attribution requirement verbatim: *"Please add a proper attribution link to
OpenAIP (https://www.openaip.net) as data source within your application!"*
— consistent with a simple attribution string, the same shape we already
carry for FAA/OSM/Copernicus.

I could not independently re-verify the "don't exclusively sell the data"
carve-out wording the issue quotes (openAIP's marketing/FAQ pages are behind
a JS app shell that returns 403 to a scripted fetch; I did not attempt to
bypass that). It doesn't matter for the recommendation below, but it's worth
noting as an unverified secondary claim if it's ever load-bearing on its own.

### Why "carry, don't relicense" is the correct mechanism regardless

CC BY-NC 4.0 requires attribution, a link to the license, and a note of any
changes made; it has **no linking/copyleft language at all** (unlike GPL) and
**no ShareAlike** (unlike -SA variants) — it does not touch the license of
software that merely reads the data, and it does not require our own code to
carry any particular license. It does forbid us from layering **additional
restrictions** on top that would block a recipient's own right to share the
material under the same terms (CC BY-NC 4.0 §2(a)(5)(A)) — meaning: whatever
we do here, the pack's download/verify/atomic-swap mechanics are a delivery
detail, not a legal restriction, so they don't collide with this. The one
concrete design implication is that we cannot wrap an openAIP-derived pack in
terms that forbid a pilot from re-sharing that file — nothing in the current
pipeline does that (packs are just downloaded files), so there's no conflict.
This is exactly why per-pack metadata, not a repo-wide relicense, is the
right shape: the code stays Apache-2.0, the data pack carries CC BY-NC 4.0
on its own terms.

### Is our distribution "commercial" under the license?

**No, on the license's own definition**, which is narrower than the informal
policy language in the issue. CC's official definition of NonCommercial is
use "not primarily intended for or directed towards commercial advantage or
monetary compensation." `navdata.aerocommons.org` distributes for free, with
no revenue intent; a donation to openAIP to help cover *their* costs is not
monetary compensation flowing to *us*. That satisfies the license's actual
NC test directly — we likely don't even need to lean on the "don't
exclusively sell" carve-out openAIP describes for paid third-party apps,
because we aren't commercial in the first place.

This reasoning assumes there's no other revenue-generating activity inside
the aerocommons.org/makerplane-data umbrella that a court or openAIP could
point to as "commercial advantage" flowing from the data (e.g., a paid
configurator tier bundled with data access). Nothing in this repo or
`configurator/CLAUDE.md` suggests that today. Flagging the assumption rather
than asserting it, since it's the one thing that would flip this analysis.

### pyEfis (GPLv2) consumption

Not a problem, and more comfortable than our existing precedent. pyEfis reads
pack files at runtime the same way it already reads the ODbL water/roads
packs and the public-domain FAA packs — file interchange, not linking
(`docs/LICENSE-AUDIT.md`'s existing boundary analysis covers this pattern).
CC BY-NC 4.0 doesn't even have GPL-style linking language to worry about, so
this case is strictly easier than the ODbL case we already ship.

### Contacting openAIP directly

Bill has said this is fair game and turns an interpretation into an answer.
I did not send anything — outbound communication representing the project to
a third party is a decision for Bill, not something to do autonomously from
here. Suggested content for that email, for Bill to send or to hand off:

- State plainly: makerplane-data is free, non-commercial, distributes signed
  read-only reference packs to GA pilots; no paid tier, no ads, no data
  resale.
- Confirm our reading: a per-pack `license`/`attribution` field in our
  manifest, an unmodified attribution link to openaip.net, and no additional
  restrictions on redistribution, satisfy CC BY-NC 4.0 as they intend it.
- Ask whether a periodic donation (one-time or recurring, framed as covering
  their infrastructure costs, not a data-purchase) is welcome, and how they'd
  prefer to receive it.
- Ask about their bulk/API refresh cadence expectations for an unattended
  daily-cron consumer (see § Refresh cadence) so we don't build something
  that surprises their rate limits.

## Manifest metadata design (the mechanism)

The manifest already carries `attribution` (`packtools/manifest.py`
`PackEntry`, `packtools/packmeta.py` `PackMeta`) — a free-text notice string,
e.g. `"FAA NASR (public domain)"`, `"OpenStreetMap contributors (ODbL)"`.
There is no machine-readable `license` field today; every current source
happens to be either public domain or a license we've already decided is
compatible, so nobody has needed to encode *which* license programmatically.
openAIP is the first source where the license itself — not just the
attribution string — needs to travel with the pack and be checkable in code
(by CI, by the updater, eventually by the on-device picker).

Proposed additive fields, shown as a diff against the current dataclasses
(**not applied in this evaluation** — this is the shape for whoever picks up
implementation):

```python
# packtools/packmeta.py — PackMeta
@dataclass
class PackMeta:
    id: str
    kind: str
    cycle: str
    effective: str | None = None
    expires: str | None = None
    attribution: str = ""
    license: str = ""            # NEW: SPDX id, "LicenseRef-*", or a short
                                  #      free-text tag for non-SPDX cases
                                  #      (e.g. "us-public-domain"). "" means
                                  #      "unspecified" — today's implicit
                                  #      default for every existing source.
    license_url: str | None = None   # NEW: canonical URL to the full text
    schema_version: int = SCHEMA_VERSION
```

```python
# packtools/manifest.py — PackEntry (superset of PackMeta, same shape)
@dataclass
class PackEntry:
    id: str
    kind: str
    cycle: str
    bytes: int
    sha256: str
    url: str
    effective: str | None = None
    expires: str | None = None
    regions: list[str] = field(default_factory=list)
    attribution: str = ""
    license: str = ""            # NEW — carried through from_pack()
    license_url: str | None = None   # NEW
    min_pyefis: str | None = None
    tiles_bbox: list[float] | None = None
```

Notes on the design:

- **Additive and optional** — same precedent as `attribution`, `min_pyefis`,
  `tiles_bbox` when each was added. Every existing pack round-trips unchanged
  with `license=""`; no `MANIFEST_VERSION` bump needed.
- **Two fields, not one**, because `attribution` is presentation text (what
  to *show* a pilot) and `license` is an identifier (what to *check* in
  code) — conflating them is what makes today's manifest unable to answer
  "is this pack's license compatible with X" without parsing prose.
  `license_url` is separate from `license` because plenty of real licenses
  here don't have a clean SPDX id (US Government "public domain" isn't
  actually a license at all — no copyright attaches — which is different
  from a CC0 waiver; a `LicenseRef-*`-style free-text tag plus the
  URL/citation is more honest than forcing an SPDX id that doesn't fit).
- **Validation** (`packtools/manifest.py` `validate()`): recommend one new
  invariant, in the same fail-loud spirit as the existing checks — a pack
  entry whose `license` is set to anything other than a small
  public-domain/permissive allowlist must have a non-empty `attribution`.
  Catches "someone set a restrictive license and forgot the human-readable
  notice" at build time, not after publish.
- **On-device surfacing**: `pyefis_data/core.py` `catalog()` already builds a
  per-pack row dict that includes `attribution` (around
  `pyefis_data/core.py:546`) for the on-device picker; `site/index.html`
  already renders `p.attribution` in the pack list. Both are the natural,
  already-existing spot to add `license`/`license_url` alongside it — no new
  plumbing, just two more keys riding the same path.
- **Backfill, as a separate follow-up**: once adopted, existing
  `packtools/sources.py` entries would get an explicit `license=` (e.g.
  FAA sources: a `LicenseRef-us-public-domain` tag; OSM water/roads:
  `ODbL-1.0`; Copernicus terrain: whatever its actual terms resolve to).
  Not required for openAIP specifically, but the mechanism is more honest if
  it's not *only* applied to the one source that needed it.

This is deliberately the whole mechanism requested in the issue: it makes
mixed-license packs an explicit, checkable fact instead of something buried
in a prose string, while keeping the code itself Apache-2.0 throughout.

## Format recommendation

Matches the issue's own expectation and the existing pipeline shape.

| Format | Verdict | Why |
|---|---|---|
| **GeoJSON / NDJSON** | **Recommended** | `packtools` already builds SQLite from geo source data (water/roads from OSM); this is the same shape, not a new pipeline pattern. |
| OpenAIR v1/v2 | Out of scope here | De-facto airspace format, but tracked independently in the OpenAIR issue (#42). Not evaluated in this document. |
| SeeYou CUP/CUPX | Skip | Glider waypoint/task format, not airspace geometry. |
| openAIP legacy AIP (XML) | Skip | Their own legacy format; GeoJSON via the current API/exports supersedes it. |

Confirmed the shape directly against openAIP's live schema
(`api.core.openaip.net/api/schemas/response/airspace/airspace-schema.json`,
2026-08-23): each airspace record is a GeoJSON `Polygon` geometry plus
structured fields — `type` (enum: CTR/TMA/Restricted/Danger/Prohibited/FIR/
.../36 values), `icaoClass` (A–G, or unclassified/SUA), `country` (ISO
alpha-2), `upperLimit`/`lowerLimit` (+ `upperLimitMax`/`lowerLimitMin`),
`activity`, `onDemand`/`onRequest`/`byNotam`, `frequencies`,
`hoursOfOperation`, `activeFrom`/`activeUntil`, `remarks`.

Sketch of a SQLite pack shape (starting point only, not a committed schema —
whoever implements this should design it alongside the actual builder, the
way `build_water_db` was designed alongside its own ingestion):

```
airspaces(
  id            TEXT PRIMARY KEY,   -- openAIP _id, stable across updates
  name          TEXT,
  type          INTEGER,            -- openAIP type enum, carried through as-is
  icao_class    INTEGER,
  country       TEXT,               -- ISO 3166-1 alpha-2
  lower_limit   TEXT,  lower_limit_unit TEXT,  -- e.g. "SFC", "3500", "FT"/"FL"
  upper_limit   TEXT,  upper_limit_unit TEXT,
  activity      INTEGER,
  hours_of_operation TEXT,          -- freeform / structured, TBD at build time
  remarks       TEXT,
  geometry      BLOB,               -- WKB polygon (matches how terrain/water
                                     -- already store geometry) or GeoJSON text
  source_updated_at TEXT            -- openAIP's own updatedAt, for diffing
)
```

`regions` for airspace should key on **ISO alpha-2 country code**, not the
existing 8 N-America bboxes in `regions.yaml` — that scheme was built for
bulk-static terrain tiling by latitude band and doesn't fit a
per-country-published source. This is a new region axis, not a fit into the
old one; flagging it now so it isn't discovered mid-implementation.

## Refresh cadence

Two mechanisms are on the table, and they don't need to be exclusive:

1. **Static per-country exports** (the ~7,257-file catalog the issue
   describes). Matches every existing source in `packtools/sources.py`
   exactly: a URL, fetched on a schedule, no credentials. Simplest to slot
   into `run_cyclical.py`'s existing daily-orchestrator shape. Open question
   I could not resolve without access to the actual `/data` export listing
   (blocked by Cloudflare bot protection on a scripted fetch, not attempted
   to bypass): what change-notification exists for "which countries changed
   since last pull" — without that, a naive daily pull re-downloads all
   ~7,257 files to detect deltas, which is wasteful and would need a
   HEAD/ETag or last-modified check per file at minimum.
2. **REST API** (`api.core.openaip.net`, confirmed live 2026-08-23):
   requires a registered account + API key (a new secret to manage, unlike
   every current FAA/OSM/Copernicus source), enforces rate limits ("please
   consider caching responses on your side"), but exposes an `updatedAfter`
   query parameter on `/airspaces` — genuine incremental sync, not a full
   re-pull.

**Recommendation**: static per-country exports as the primary path — it's
the zero-new-secrets option and fits the pipeline's existing shape exactly.
Treat the API's `updatedAfter` as a lower-frequency reconciliation pass (or
a v2 optimization) once the static path is working, not a prerequisite.

Airspace doesn't have NASR/CIFP's AIRAC hard-expiry model — there's no
regulatory "this airspace definition expires on date X" the way charts do.
Recommend treating it like water/highways: **non-cyclical**
(`effective`/`expires` = `None`), re-issued periodically (`r1`, `r2`, ...)
rather than on a 28-day AIRAC clock. `packtools/sources.py`'s `Source.cadence`
field currently only has `'airac'`/`'dof'` values; airspace would need a
third cadence family (e.g. `'periodic'`) with its own re-issue interval —
monthly is a reasonable starting guess given how infrequently controlled
airspace boundaries actually change, but should be validated against
openAIP's actual `updatedAt` churn rate before committing to a number.

## Constraints respected

- **No pack built or published.** Nothing in `packtools/sources.py`,
  `packmeta.KINDS`, or R2 was touched. The schema sketches above are
  documentation, not code.
- **#42 (OpenAIR ingest) untouched.** This evaluation stayed on GeoJSON/API
  the whole way through; no OpenAIR parsing was needed to reach these
  conclusions.
- **House rule preserved for whoever implements this later**: an `airspace`
  pack kind must merge to `main` (added to `packmeta.KINDS` + the updater)
  *before* its first R2 publish — the same rule that exists because an
  unknown kind in a published manifest already crashed the nightly cron
  once.

## Open items for Bill

1. **Approve or reject the license path** described above (carry under CC
   BY-NC 4.0 via per-pack manifest metadata, not relicense) as the direction
   to build toward.
2. **Contacting openAIP** — send the confirmation email (draft above), or
   decide it's not necessary given the license's own NC definition already
   covers us; separately, whether/when to make the donation.
3. **Greenlight implementation as a follow-up issue** once 1–2 are settled:
   the manifest schema change (small, mechanical, and reusable beyond
   openAIP), then the airspace builder itself (new kind, merged to `main`
   before first publish, per house rule).
