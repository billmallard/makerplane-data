# PR impact report for builder changes (TV9)

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

A pull request that changes a pack builder gets a comment saying what the
change does to the data, before it merges. makerplane-data#17 is the reason
it exists: the June 2026 highways pack shipped with no California roads and
the build stayed green. With this report, a change like that shows up on the
PR as `state california/socal: 9 -> 0 (-100 %)`, and the check goes red.

## What runs

`.github/workflows/impact.yml` runs on any PR that touches a builder, its
fixtures or the report itself (the `paths:` list in the workflow). It:

1. checks out the PR's merge commit (`head`) and that commit's first parent
   (`base`), so the diff is exactly what the merge would change;
2. checks out pyEfis at `PYEFIS_REF` (default `dev`) once, for both sides;
3. runs `python -m packtools.impact --base base --head head`. For each
   kind, `scripts/impact_build.py` builds a fixture pack with `base`'s
   builder and again with `head`'s;
4. posts the result as one PR comment, updated on every push, plus the job
   summary and a `pr-impact` artifact holding `impact.md` and `impact.json`.

Fork PRs get a read-only token, so they get the job summary but no comment.
The job uses no secrets, so it cannot sign or publish anything.

## What is held fixed

So that a difference can only come from the builder change, both sides use:

- the same fixture input: always `head`'s `tests/fixtures`;
- the same runner, `head`'s `scripts/impact_build.py`. It imports only the
  builder entry points from the tree it is building with:
  `make_roads.build_na_roads`, `build.build_procedures` and
  `build.build_airspace_cifp`;
- the same pyEfis checkout;
- the same packing and check catalogue (`head`'s `packtools.report`). The
  base pack is the previous edition of both records, so the delta checks
  run on both sides.

## Fixtures

| Kind | Builder under test | Input |
|---|---|---|
| highways | `make_roads.build_na_roads` (state list, fetch, extract) -> pyEfis `build_highway_db.py` | Synthetic. `tests/fixtures/impact/roads_states.json` gives one road set per Geofabrik slug in `CONUS_STATES`, placed at the state's approximate centre. A stand-in downloader serves it; for a slug it does not know, the downloader answers with the HTML page Geofabrik serves, as the real mirror does. |
| procedures | `build.build_procedures` | `tests/fixtures/cifp/FAACIFP18`, real FAA records |
| airspace | `build.build_airspace_cifp` | `tests/fixtures/cifp_airspace/FAACIFP18`, real FAA records |

Not covered:

- navdata, navaids and obstacles: their builders live in pyEfis, so a PR
  here does not change them;
- water, terrain and plates: these have no offline fixture yet.

A PR that changes only pyEfis's builder is also out of scope, because both
sides use the same pyEfis.

## What it reports

For each kind:

- the row count of every table;
- feature counts broken down by the unit a loss would hide in: state and
  road class for highways, airport and procedure kind for procedures,
  airspace type for airspace;
- every check whose status moved between the base pack and the head pack.

Only the rows that changed are listed. Unchanged rows are counted.

A **regression** makes the job exit 1:

- the head build fails where the base built;
- a state, airport or airspace type that had features has none;
- a table that had rows is empty or gone;
- a check that did not fail on the base pack fails on the head pack.

If both sides fail to build, that is an error, and the job also exits 1.
Every other difference is listed for a person to read.

The check is not required: a deliberate change, such as a road class taken
out on purpose, is read and merged anyway.

## What it cannot catch

- **Problems that appear only in real data.** Fixture packs are small, and
  the roads input is synthetic. A change that only matters at real-world
  scale, or on data shapes the fixture lacks, passes here. The publish gate
  (`docs/pack_report.md`) judges the real build.
- **A broken fetch of the real upstream.** The downloader is a stand-in.
  Geofabrik's behaviour for unknown slugs is mimicked, but transient
  failures and wedged editions are not (makerplane-data#60).
- **Changes outside the `paths:` list.** If a PR stops touching those
  files, its old comment stays as it was.

## Running it locally

```bash
git worktree add ../mpd-base origin/dev            # or any base
export PYEFIS_TOOLS_DIR=/path/to/pyEfis/tools      # highways only; skipped if unset
pip install pyshp numpy
python -m packtools.impact --base ../mpd-base --head . --work /tmp/impact \
  --out-md impact.md
```

Exit 0 means no regressions. Exit 1 means at least one regression or error.
