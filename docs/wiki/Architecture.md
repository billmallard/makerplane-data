# Architecture — how it works

makerplane-data delivers three kinds of artifact to an aircraft — **navigation
packs**, **panel layouts**, and **aircraft parameters** — and they all follow
one safety principle:

> **Verify first, place atomically, and never disturb what is already running.**

A download that is corrupt, tampered with, or interrupted is thrown away. The
data the avionics are currently using is only ever replaced by a complete,
verified file, in a single atomic step. This page explains that mechanism and
how the three artifact types share it.

## The three delivery channels

| Channel | Carries | Public or private | How it's trusted |
|---|---|---|---|
| **Navigation data** | terrain, airports, navaids, obstacles, water, roads | **Public** — one global catalog everyone pulls | **Signed** manifest + per-pack SHA-256 |
| **Panel config** | your EFIS screen layout | **Private** — one per paired device | Device **token** over TLS + SHA-256 for integrity |
| **Aircraft parameters** | V-speeds, gauge bands, engine/fuel limits | **Private** — one per aircraft | Same private device channel |

The navigation channel is signed because it is public and shared — anyone can
fetch it, so authenticity has to be provable. The private channels are
authorised by a per-device token over TLS instead; there is no public identity
to forge, so they carry a SHA-256 for corruption/change detection but are not
separately signed. All three reuse the same **download → verify → atomic-swap**
code on the aircraft.

## The navigation trust chain

The public navigation data is protected by a short signing chain. One offline
**secret key** signs a catalog; a committed **public key** on every device
verifies it; the catalog then vouches for each pack.

```
  keys/minisign.pub  (public, shipped in the updater)
        │  verifies
        ▼
  manifest.json.minisig   (ed25519 signature, minisign format)
        │  signs the exact bytes of
        ▼
  manifest.json   (the catalog: every pack's cycle, size, URL, SHA-256)
        │  carries a SHA-256 for
        ▼
  each .pack file   (verified byte-for-byte before install)
```

Two properties fall out of this:

- **Compromising the website is not enough to push a bad pack.** Serving a
  malicious file would still need the offline secret key to forge the signature.
  Without it, the device refuses the pack and keeps its current data.
- **The device trusts exactly one key and nothing else.** The public key is
  embedded in the updater, so verification needs no network and no key file. (If
  the key were ever rotated, a new release would update that constant.)

The signature covers the manifest's **exact serialized bytes** — the device
verifies the bytes it downloaded, it never re-serializes first — so signing can
never drift from verification. The same signing code runs on the build side and
the device side, from one implementation.

> The secret signing key and all hosting credentials are **never** in the
> repository or this wiki. Only the public key is published (that's the point).

## The manifest is the contract

The whole navigation system is held together by one JSON document —
`manifest.json` — at a stable URL. It lists every available pack with its
currency window, size, SHA-256, and download URL. Because that schema is frozen,
three independent "legs" can each be built, tested, and replaced on their own:

```
  LEG 1  BUILD          GitHub Actions fetch upstream data (FAA, Copernicus,
  (packtools/)          OpenStreetMap), build packs, sign the manifest
        │  packs + manifest.json + manifest.json.minisig
        ▼
  LEG 2  DISTRIBUTE     Cloudflare R2 object storage, served at
                        navdata.aerocommons.org (edge-cached, zero egress)
        │  HTTPS  /  USB stick
        ▼
  LEG 3  UPDATE         the on-Pi updater verifies, stages, and atomically
  (pyefis_data/)        swaps the new data in; raises a currency flag
```

Freeze the contract and each leg evolves independently — you can rebuild the
pipeline, move the hosting, or reflash the aircraft without touching the other
two. Details in **[[Navigation Data Currency]]**.

## Verify-then-atomic-swap (the safety contract)

This is the part that matters most on a flying aircraft. Every install — a
navigation pack, a panel, or an aircraft-parameter file — works the same way:

1. **Download to a staging area**, never on top of the live file.
2. **Verify** — check the signature (navdata) and/or the SHA-256 against the
   trusted catalog. Any mismatch: discard, keep the current data.
3. **Move into place atomically** — a filesystem rename / symlink flip that
   either fully succeeds or does nothing. A process already reading the old file
   keeps reading it; the new data applies on the next restart or cache miss.
4. **On config installs, verify the aircraft still boots**, and **roll back** to
   the previous known-good version if it doesn't.

The result: an interrupted transfer, a bad signature, a corrupt byte, or a
config that would crash the display can **never** leave the aircraft worse off
than before the update. Offline, the device falls back to the last good,
still-verified data it already has.

## Two contracts, kept separate

A recurring theme across the whole MakerPlane stack is a clean split between two
data contracts:

- **The runtime flight-data contract** — live values (airspeed, attitude,
  engine data) flowing over FIX-Gateway / CAN-FIX **in flight**. Not this repo.
- **The reference & configuration contract** — the packs, panels, and
  parameters that change on a *cycle*, delivered by makerplane-data.

Keeping them separate is deliberate: the thing that updates at the hangar is
never in the path of the thing that runs in the air.

## Guarding against silent breakage

The upstream data (e.g. the FAA's monthly files) occasionally changes format.
Because the build tools read data by column name, a change can silently produce
*wrong or near-empty* data on an otherwise-green build. A **schema guard** in
the pipeline checks each nightly download against a committed snapshot of the
expected format and **fails the build loudly** if anything drifted — so bad data
is caught before it is ever signed or shipped. See
[Navigation Data Currency → Schema guard](Navigation-Data-Currency#the-schema-guard-catching-upstream-drift).

## Where to go next

- **[[Navigation Data Currency]]** — packs, the build pipeline, the on-Pi
  updater, and the currency flag.
- **[[The Configurator]]** — the web app and how a panel reaches a paired
  aircraft.
- **[[Aircraft Parameters]]** — the CAN-FIX V-speed / gauge-band profile.
