# SPDX-License-Identifier: Apache-2.0
"""Publish packs to an object store and re-sign the manifest.

Shared by every build path (cyclical navdata, terrain, water, roads, plates,
one-off build-pack --upload): gate each pack (``packtools.gate``: its
pack-report/1 record is built and judged before anything is signed), upload
the records and pack files, upsert their entries into the existing manifest
(so other data types are preserved), and re-sign. :func:`sign_and_put` is
the single place that writes the manifest+signature pair; every write,
retract and backfill included, goes through it.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import gate as _gate
from . import schemasite as _schema
from .manifest import Manifest, PackEntry, validate

MANIFEST_KEY = _gate.MANIFEST_KEY
SIG_KEY = _gate.SIG_KEY


def sign_and_put(store, secret, m: Manifest, *, previous: dict, sign, comment: str) -> bytes:
    """Validate, sign and upload ``m``. ``previous`` is the signed manifest
    object this write replaces (``{"packs": []}`` if none): every entry new
    relative to it must carry a pack report, and no entry it bound to a
    report may change that binding (``manifest.validate``). Returns the
    signed bytes."""
    validate(m.to_obj(), previous=previous)
    raw = m.to_bytes()
    sig = sign(raw, secret, trusted_comment=comment)
    store.put_bytes(MANIFEST_KEY, raw, content_type="application/json")
    store.put_bytes(SIG_KEY, sig.encode("ascii"), content_type="text/plain")
    return raw


def _load(store, secret) -> tuple[Manifest | None, dict]:
    """The live manifest for an edit. A signing key verifies it first; a
    read-only dry run (no key) parses it unverified, since it writes nothing."""
    if secret is not None:
        return _gate.load_signed(store, secret)
    raw = store.get_bytes(MANIFEST_KEY)
    return (Manifest.from_bytes(raw), json.loads(raw)) if raw else (None, {"packs": []})


def publish(store, secret, pairs: list[tuple[PackEntry, str | Path]], *,
            generated: str, sign, comment: str | None = None, log=print,
            gate: "_gate.Options | None" = None) -> Manifest:
    """Gate, upload and re-sign.

    ``pairs`` is a list of (PackEntry, local pack path). ``sign`` is
    ``packtools.signing.sign``. Loads the signed live manifest so the new
    entries are *added* alongside whatever is already published. ``gate``
    carries the person-supplied build notes, origin and sources.

    All or nothing: every pack is reported on first. If the gate refuses
    any of them, their ``rejected`` records are uploaded and
    :class:`packtools.gate.GateRefused` is raised before any pack, accepted
    record or manifest is written."""
    opts = gate or _gate.Options()
    live, previous = _gate.load_signed(store, secret)
    m = Manifest.from_obj(previous) if live is not None else Manifest.new(generated)
    m.generated = generated
    gated = [_gate.gate_one(store, live, entry, path, opts=opts, log=log)
             for entry, path in pairs]
    refused = [g for g in gated if not g.accepted]
    if refused:
        raise _gate.GateRefused(refused)
    for g in gated:
        path = Path(g.path)
        ctype = "application/json" if path.suffix == ".sqlite" else "application/octet-stream"
        _gate.put_record(store, g)
        store.put_file(f"packs/{path.name}", path, content_type=ctype)
        m.upsert(g.entry)
    sign_and_put(store, secret, m, previous=previous, sign=sign,
                 comment=comment or f"published {generated}")
    log(f"manifest: {len(m.packs)} pack(s), +{len(pairs)} new, records bound, signed + uploaded")
    # Only once the manifest naming them is signed: the pack format pages
    # (schema/<kind>.json, SCH1) describe published packs, never refused ones.
    _schema.put_captures(store, [(g.path, g.record) for g in gated], log=log)
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
    live, previous = _load(store, secret)
    if live is None:
        raise RuntimeError("no manifest to modify")
    m = live
    if not m.remove(pack_id, cycle):
        raise RuntimeError(f"no entry {pack_id}/{cycle} in manifest")
    m.generated = generated
    sign_and_put(store, secret, m, previous=previous, sign=sign,
                 comment=comment or f"retracted {pack_id}/{cycle} at {generated}")
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
    live, previous = _load(store, secret)
    if live is None:
        raise RuntimeError("no manifest to modify")
    m = live
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
    sign_and_put(store, secret, m, previous=previous, sign=sign,
                 comment=f"backfilled {len(filled)} license(s) at {generated}")
    log(f"manifest: filled {len(filled)} license(s), {len(m.packs)} pack(s), signed + uploaded")
    return filled
