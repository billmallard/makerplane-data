# SPDX-License-Identifier: Apache-2.0
"""Publish packs to an object store and re-sign the manifest.

Shared by every build path (cyclical navdata, terrain, water, one-off
build-pack --upload): upload the pack file(s), upsert their entries into the
existing manifest (so other data types are preserved), and re-sign. Keep
this the single place that writes the manifest+signature pair.
"""

from __future__ import annotations

from pathlib import Path

from .manifest import Manifest, PackEntry

MANIFEST_KEY = "manifest.json"
SIG_KEY = "manifest.json.minisig"


def publish(store, secret, pairs: list[tuple[PackEntry, str | Path]], *,
            generated: str, sign, comment: str | None = None, log=print) -> Manifest:
    """Upload packs and re-sign the manifest.

    ``pairs`` is a list of (PackEntry, local pack path). ``sign`` is
    ``packtools.signing.sign``. Loads the existing manifest from the store so
    the new entries are *added* alongside whatever is already published.
    """
    raw = store.get_bytes(MANIFEST_KEY)
    m = Manifest.from_bytes(raw) if raw else Manifest.new(generated)
    m.generated = generated
    for entry, path in pairs:
        path = Path(path)
        ctype = "application/json" if path.suffix == ".sqlite" else "application/octet-stream"
        store.put_file(f"packs/{path.name}", path, content_type=ctype)
        m.upsert(entry)
    raw = m.to_bytes()
    sig = sign(raw, secret, trusted_comment=comment or f"published {generated}")
    store.put_bytes(MANIFEST_KEY, raw, content_type="application/json")
    store.put_bytes(SIG_KEY, sig.encode("ascii"), content_type="text/plain")
    log(f"manifest: {len(m.packs)} pack(s), +{len(pairs)} new, signed + uploaded")
    return m


def retract(store, secret, pack_id: str, cycle: str, *,
            generated: str, sign, comment: str | None = None, log=print) -> Manifest:
    """Drop one (id, cycle) entry from the live manifest and re-sign.

    For undoing a bad publish -- e.g. a states-limited test build uploaded
    under the production pack id. ``Manifest.select`` no longer lets a
    hyphen-suffixed test cycle outrank the canonical edition it stood in for
    (AER-1109), but the bad entry is still wrong data to leave listed under
    the production id, so still retract it. Does not touch the pack object
    itself, only its manifest listing.
    """
    raw = store.get_bytes(MANIFEST_KEY)
    if not raw:
        raise RuntimeError("no manifest to modify")
    m = Manifest.from_bytes(raw)
    if not m.remove(pack_id, cycle):
        raise RuntimeError(f"no entry {pack_id}/{cycle} in manifest")
    m.generated = generated
    raw = m.to_bytes()
    sig = sign(raw, secret, trusted_comment=comment or f"retracted {pack_id}/{cycle} at {generated}")
    store.put_bytes(MANIFEST_KEY, raw, content_type="application/json")
    store.put_bytes(SIG_KEY, sig.encode("ascii"), content_type="text/plain")
    log(f"manifest: retracted {pack_id}/{cycle}, {len(m.packs)} pack(s) remain, signed + uploaded")
    return m


def backfill_licenses(store, secret, *, generated: str, sign, dry_run: bool = False,
                      log=print) -> list[tuple[str, str, str, str]]:
    """Fill a blank ``license``/``license_url`` on live manifest entries from
    the upstream their ``attribution`` names, and re-sign.

    Only blank licenses are touched -- a non-blank one is never overwritten --
    and pack objects are never touched, only their manifest listing (so every
    sha256 still matches). An entry whose attribution matches no known upstream
    is left blank and reported. Returns the (id, cycle, license, license_url)
    fills; writes nothing if ``dry_run`` or if there is nothing to fill.
    """
    from .sources import license_for_attribution
    raw = store.get_bytes(MANIFEST_KEY)
    if not raw:
        raise RuntimeError("no manifest to modify")
    m = Manifest.from_bytes(raw)
    filled = []
    for p in m.packs:
        if p.license:
            continue
        hit = license_for_attribution(p.attribution)
        if hit is None:
            log(f"UNMATCHED {p.id}/{p.cycle}: attribution {p.attribution!r} -- left blank")
            continue
        p.license, p.license_url = hit
        filled.append((p.id, p.cycle, p.license, p.license_url))
        log(f"fill {p.id}/{p.cycle}: {p.license} {p.license_url}".rstrip())
    if dry_run or not filled:
        log(f"manifest: {len(filled)} license(s) to fill; "
            f"{'dry-run, ' if dry_run else ''}nothing written")
        return filled
    m.generated = generated
    raw = m.to_bytes()
    sig = sign(raw, secret, trusted_comment=f"backfilled {len(filled)} license(s) at {generated}")
    store.put_bytes(MANIFEST_KEY, raw, content_type="application/json")
    store.put_bytes(SIG_KEY, sig.encode("ascii"), content_type="text/plain")
    log(f"manifest: filled {len(filled)} license(s), {len(m.packs)} pack(s), signed + uploaded")
    return filled
