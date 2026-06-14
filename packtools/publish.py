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
