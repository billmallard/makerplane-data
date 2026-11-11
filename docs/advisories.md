<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Safety advisories: an opt-in, pull-only feed

When MakerPlane finds a defect with a safety consequence in pyEfis,
`pyefis-data` or a published data pack, it needs a way to tell the people
flying it. This is that channel. We publish a small signed list of
advisories; a device that has opted in downloads it on its normal update
schedule and works out **on the device** whether any of them apply.

We never learn who is running what. There is no install registry, no
acknowledgment sent home, no install id, and nothing about the aircraft or
its software in any request. This was decided on 2026-08-30 (BACKLOG L5). A
registry would create the post-sale duty to warn that Restatement (Third) of
Torts: Products Liability section 10 ties to "the ability to identify past
purchasers". It would be undisclosed telemetry in an open-source project. It
would hold personal data (IP addresses and hostnames of EU users), and it
would be a write-accepting service reachable from the field. Do not
re-propose it.

## On the device

Off by default. To turn it on or off:

```bash
pyefis-data advisories --enable     # shows what it does, saves `advisories: true`, checks now
pyefis-data advisories --disable    # saves `advisories: false`; nothing is fetched after this
```

`--enable` and `--disable` write the `advisories:` key in `data.yaml`. Editing
that key by hand does the same thing. With the key absent or `false`, the
updater never requests the feed: the off path makes no request at all.

When it is on, every `pyefis-data update` (the daily timer) and every
`pyefis-data import` (USB) also:

1. fetches `advisories.json` and `advisories.json.minisig` from the same
   origin as the manifest. A USB stick may carry them at its root beside
   `manifest.json`. A stick without them is normal and leaves the last feed in
   place;
2. verifies the signature against the same embedded key as the manifest, and
   retries the pair once if the check fails, since the two are separate
   requests and a publish can land between them;
3. refuses a feed whose `sequence` is lower than the cached one, or equal to
   it with different bytes. A replayed old feed must not hide a newer
   advisory;
4. caches the verified feed under the data root (`advisories.json`, `.minisig`,
   `advisories-state.json`);
5. matches every advisory against what is installed here and prints the ones
   that apply.

Nothing in this path can block or fail an update. A bad signature, a broken
feed, a full disk or no network ends up as an `error` in the report. The
update's exit code and the installed data are untouched, and the last
verified feed stays in use. This is non-blocking, as the startup notice is
(L3).

```bash
pyefis-data advisories              # what applies, from the last verified feed
pyefis-data advisories --check      # fetch and verify now first
pyefis-data advisories --json       # the status.json block (below)
pyefis-data advisories --software pyefis=2.1.0   # name a component version (see "Matching")
```

**An empty feed is silence.** With nothing applicable, `update` prints nothing
about advisories, and the status block has `"active": []`. Only an explicit
`pyefis-data advisories` says "no safety advisories apply to this device".

### What leaves the device

One HTTPS GET of `<base_url>/advisories.json` and one of
`<base_url>/advisories.json.minisig`. The requests have no query string, no
body, no cookies and no added headers, so every device sends the same two
requests. `tests/test_advisories.py::test_requests_carry_no_device_identifying_data`
drives the CLI through the production transport and fails on any other
request, or if the hostname, user, software versions, pack ids or cycles
appear anywhere in what was sent.

Like any download, the request shows the device's network address to the
server. The feed is a static object on the same Cloudflare R2 origin as the
packs, which every updater already downloads from. We do not log or keep
those addresses (see Feed policy).

## status.json: what the About/System screen reads

`pyefis-data status --json --out` and every non-dry `update` write an
`advisories` block into `~/.makerplane/pyefis/status.json`, beside `packs`.
pyEfis reads it from there and needs no network.

Off:

```json
"advisories": {"enabled": false}
```

On:

```json
"advisories": {
  "enabled": true,
  "sequence": 3,
  "generated": "2026-12-01T00:00:00Z",
  "checked": "2026-12-02T09:15:03Z",
  "error": null,
  "active": [
    {
      "id": "MPA-2026-001",
      "issued": "2026-12-01",
      "severity": "warning",
      "title": "...",
      "description": "...",
      "action": "...",
      "applies": "yes",
      "affects": [
        {"pack": "obstacles-conus", "cycles": ["260611"], "result": "yes"}
      ]
    }
  ]
}
```

- `active` lists only advisories whose `applies` is `yes` (this device is
  affected) or `unknown` (it may be; see below), `yes` first, then most
  severe first. An advisory that does not apply is left out.
- `checked` is the last successful verified fetch. `error` is the last
  failure, or `"no verified advisory feed yet"` before the first one. Both are
  shown with the cached feed, which stays in force.
- Each `affects` target carries its own `result`, so a reader can show why.

### Matching

- **Pack targets** match the installed (`current`) or `staged` cycle of that
  pack id.
- **Software targets** match a component's version. The updater always knows
  its own (`pyefis-data`). pyEfis is usually *not* importable from the
  updater's environment, so its version is known only when it is
  pip-installed beside the updater or a caller names it
  (`--software pyefis=<version>` on `update`, `import` or `advisories`). The
  daily timer does not, so a pyEfis target there reports `unknown`, never
  `no`. Not knowing a version never counts as unaffected.
- **pyEfis resolves its own.** For an `unknown` target with
  `"software": "pyefis"`, the About screen knows its running version and
  should apply the same rule (next bullet) to settle it.
- **Version rule.** A target covers `introduced <= v < fixed`. Either bound may
  be absent. `versions` lists exact versions instead. Versions compare
  numerically by their leading `N.N.N` (`2.1` equals `2.1.0`). A `+local`
  label is ignored. Any other suffix (`rc1`, `.dev0`, `-test`) marks a
  pre-release, and a pre-release counts as *inside* the range at both ends:
  a pre-release of the introducing version may already carry the defect, and
  a pre-release of the fixing version may not carry the fix yet. A version
  with no leading number is `unknown`. Reference: `packtools/advisories.py`
  `software_match`, with its table test in `tests/test_advisories.py`.
- An advisory this updater cannot fully read (an unknown target type or
  severity from a later revision) is reported as `unknown` with severity
  `warning`, not dropped.

## The feed

Published at `https://navdata.aerocommons.org/advisories.json`, signed with
the manifest's key into `advisories.json.minisig` (minisign format), so
anyone can check it with the stock tool:

```bash
minisign -Vm advisories.json -p keys/minisign.pub
pyefis-data verify advisories.json        # the same check, embedded key
```

Schema `advisory-feed/1` (`packtools/advisories.py` is the reference):

```json
{
  "schema": "advisory-feed/1",
  "generated": "2026-11-08T00:00:00Z",
  "sequence": 1,
  "advisories": [
    {
      "id": "MPA-2026-001",
      "issued": "2026-11-08",
      "severity": "warning",
      "title": "Short, plain statement of the problem",
      "description": "What is wrong and what it can do in flight.",
      "action": "What to do: update to X, deselect pack Y, do not use Z.",
      "affects": [
        {"software": "pyefis", "introduced": "2.1.0", "fixed": "2.1.3"},
        {"software": "pyefis-data", "versions": ["0.4.0"]},
        {"pack": "obstacles-conus", "cycles": ["260611"]}
      ]
    }
  ]
}
```

| Field | Rule |
|---|---|
| `sequence` | Positive integer, raised by at least one on every publish. The publish tool refuses a sequence not above the live one; devices refuse a lower one. |
| `generated` | UTC, `YYYY-MM-DDTHH:MM:SSZ`. |
| `id` | `MPA-<year>-<NNN>`, unique, never reused. |
| `issued` | ISO date. |
| `severity` | `warning`, `caution` or `advisory` (below). |
| `title`, `description`, `action` | Plain-language text, non-empty. `action` is the required action. |
| `affects` | One or more targets. A device is affected if any target matches. |

Proposed severity meanings, borrowing the annunciator words pilots already
know:

- `warning`: can lead to a hazardous indication or loss of a function in
  flight. Act before the next flight.
- `caution`: wrong or degraded information that a pilot cross-checking would
  likely notice. Act soon.
- `advisory`: no direct safety effect; worth knowing or fixing.

## Publishing an advisory

The committed file `advisories/feed.json` is the source of truth. Every
advisory, correction and withdrawal is a reviewed commit, which is also the
record of when we knew and what we said.

1. Edit `advisories/feed.json`: add, change or remove the entry, raise
   `sequence` by one and set `generated`. Withdrawing an advisory means
   removing it; it then disappears from devices at their next check.
2. `packtool advisories --check-only` validates it (CI also runs this, in
   `tests/test_advisories.py::test_committed_feed_is_valid_and_canonical`,
   which requires the file to be in canonical form).
3. Open a PR, get it approved, and merge it through `dev` and `qa` to
   `main`.
4. Dispatch the `advisories` workflow on `main` with `dry_run` on, then off.
   It refuses a real publish from any other branch. It verifies the live feed
   before trusting its sequence, signs, uploads, then fetches the public URL
   and verifies what devices will see.

`packtool deploy-site` refuses the two feed keys, so a site deploy can never
overwrite the feed.

## Feed policy (proposed; Bill's call)

- **Who may issue:** an advisory is published only on Bill's explicit
  approval of its text, as a PR to `advisories/feed.json`.
- **Default:** off on every install. A user turns it on with
  `pyefis-data advisories --enable`, or the installer/picker offers it after
  showing `advisory_disclosure` from `pyefis-data notice --json`.
- **Disclosure:** the text in `pyefis_data/advisories.py` `DISCLOSURE`. It is
  printed after the software notice is accepted at a terminal and when the
  channel is enabled, and is returned to callers as `advisory_disclosure`. It
  is deliberately not part of the accepted notice text (`NOTICE_TEXT`):
  changing that would require a new accept on every device.
- **Logging:** no access logging or Logpush on the bucket or the
  `navdata.aerocommons.org` zone keeps requests for this object, and none is
  to be turned on. Nothing we run receives the request beyond Cloudflare
  serving a static file. Not yet confirmed: checking the zone's current
  settings needs Cloudflare dashboard access, which the agent that wrote
  this does not have.
- **Empty is normal:** the feed is published once, empty, at sequence 1, so
  devices that opt in see a verified empty feed rather than an error.

## Limits

- A signature proves who signed the feed, not that it is current. The
  sequence check stops a replay of an *older* feed once a device has seen a
  newer one. It cannot stop someone who controls the network from serving a
  device the same old feed forever. The status block's `checked` time shows
  when the device last fetched a verified feed. A feed `expires` field plus a
  scheduled re-sign would close this gap, at the cost of a daily signing
  job; it is not built.
- A device learns of an advisory only when it next checks: at its next
  timer run with a network, or its next USB import.
