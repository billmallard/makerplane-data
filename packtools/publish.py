# SPDX-License-Identifier: Apache-2.0
"""Publish packs to an object store and re-sign the manifest.

Shared by every build path (cyclical navdata, terrain, water, roads, plates,
one-off build-pack --upload): gate each pack (``packtools.gate``: its
pack-report/1 record is built and judged before anything is signed), upload
the records and pack files, upsert their entries into the existing manifest
(so other data types are preserved), and re-sign. :func:`sign_and_put` is
the single place that writes the manifest+signature pair; every write,
retract and backfill included, goes through it.

MANIFEST1: every writer is a read-modify-write of one object, and writers
overlap (the nightly cyclical run fires hours late; a terrain publish holds
its read through a multi-GB upload). :func:`commit` makes the write a
compare-and-set: the manifest is put only if it is still the version that
was read (``If-Match`` on its ETag, ``If-None-Match: *`` for the first
one). A writer that lost the race re-reads, re-applies its edit to what is
live now, re-signs and retries, so neither side's entries are dropped.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

from . import gate as _gate
from . import schemasite as _schema
from .manifest import Manifest, PackEntry, validate
from .upload import PreconditionFailed

MANIFEST_KEY = _gate.MANIFEST_KEY
SIG_KEY = _gate.SIG_KEY


class ManifestConflict(RuntimeError):
    """The manifest kept changing under this write (every retry lost the
    race), or another writer changed an entry this write had decided on.
    Nothing was written; the run must go red, not guess."""


def sign_and_put(store, secret, m: Manifest, *, previous: dict, sign, comment: str,
                 etag: str | None, retro: dict | None = None) -> bytes:
    """Validate, sign and upload ``m``. ``previous`` is the signed manifest
    object this write replaces (``{"packs": []}`` if none): every entry new
    relative to it must carry a pack report, and no entry it bound to a
    report may change that binding (``manifest.validate``). An entry it
    listed with no binding may gain one only for a ``retro`` record in
    ``retro`` (``{(id, cycle): raw bytes}``, TV4). Returns the signed bytes.

    ``etag`` is the version ``previous`` was read at (``None``: there was no
    manifest). The manifest is put only if that is still the live version,
    else :class:`packtools.upload.PreconditionFailed` and nothing is written.
    Use :func:`commit`, which retries; this is its single write."""
    validate(m.to_obj(), previous=previous, retro=retro)
    raw = m.to_bytes()
    sig = sign(raw, secret, trusted_comment=comment)
    cond = {"if_match": etag} if etag is not None else {"if_none_match": "*"}
    store.put_bytes(MANIFEST_KEY, raw, content_type="application/json", **cond)
    # The signature follows its manifest. A reader between the two puts sees
    # the new manifest with the old signature, which fails verification
    # (closed): a device keeps its cached manifest, a writer re-reads.
    store.put_bytes(SIG_KEY, sig.encode("ascii"), content_type="text/plain")
    return raw


#: Attempts :func:`commit` makes before :class:`ManifestConflict`, and the
#: first backoff (doubled per attempt, jittered).
ATTEMPTS = 6
BACKOFF_S = 2.0


def read_live(store, secret, *, attempts: int = 3, sleep=time.sleep):
    """``(manifest | None, raw object, etag)`` of the signed live manifest.

    A read that lands between another writer's manifest put and its
    signature put does not verify; that is transient, so it is re-read
    (``attempts`` times) before the :class:`packtools.gate.GateError`
    stands."""
    for i in range(attempts):
        try:
            return _gate.load_signed_etag(store, secret)
        except _gate.GateError:
            if i == attempts - 1:
                raise
            sleep(BACKOFF_S * (i + 1))


def commit(store, secret, edit, *, sign, comment, retro: dict | None = None,
           state=None, attempts: int = ATTEMPTS, sleep=time.sleep, log=print):
    """The one read-modify-write of the live manifest (MANIFEST1).

    ``edit(live, previous)`` gets the signed live manifest (``None`` if
    there is none) and its raw object, and returns the :class:`Manifest` to
    write, or ``None`` to write nothing. It is called again on every retry
    with what is live *then*, so it must derive its result from its
    arguments, never from a manifest it read earlier. It may raise
    :class:`ManifestConflict` when the live manifest moved in a way that
    voids a decision made before the edit (a gate verdict, say).

    ``state`` is an already-read ``read_live`` triple to use for the first
    attempt (the caller decided things from it). ``comment`` is the trusted
    comment, or a callable taking the edited manifest. Returns the signed
    bytes, or ``None`` if ``edit`` wrote nothing."""
    for attempt in range(1, attempts + 1):
        live, previous, etag = state if state is not None else read_live(store, secret, sleep=sleep)
        state = None
        m = edit(live, previous)
        if m is None:
            return None
        try:
            return sign_and_put(store, secret, m, previous=previous, sign=sign,
                                comment=comment(m) if callable(comment) else comment,
                                etag=etag, retro=retro)
        except PreconditionFailed as e:
            if attempt == attempts:
                raise ManifestConflict(
                    f"{MANIFEST_KEY} changed under every one of {attempts} attempts "
                    f"to write it ({e}); nothing written") from None
            wait = BACKOFF_S * 2 ** (attempt - 1) * (0.5 + random.random())
            log(f"manifest: another writer published first ({e}); re-reading and "
                f"re-applying (attempt {attempt + 1}/{attempts}, in {wait:.1f} s)")
            sleep(wait)


def changed_ids(before: Manifest | None, after: Manifest | None, ids) -> list[str]:
    """The ``ids`` whose entries differ between two live manifests: another
    writer touched the same pack, so any decision taken from ``before``
    about it (first edition, previous edition) no longer holds."""
    def view(m):
        out = {}
        for p in (m.packs if m else []):
            out.setdefault(p.id, set()).add(json.dumps(p.as_dict(), sort_keys=True))
        return out
    a, b = view(before), view(after)
    return sorted(i for i in set(ids) if a.get(i) != b.get(i))


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
    state = read_live(store, secret)
    live = state[0]
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
    ids = [g.entry.id for g in gated]
    out: list[Manifest] = []

    def edit(now, previous):
        # The gate judged each pack against ``live``. Another writer may add
        # other packs meanwhile (kept: they are in ``now``), but one that
        # changed these same ids voids the verdict, so stop rather than guess.
        moved = changed_ids(live, now, ids)
        if moved:
            raise ManifestConflict(
                f"another writer changed {', '.join(moved)} in {MANIFEST_KEY} after this "
                "publish gated it; nothing written, re-run the publish")
        m = Manifest.from_obj(previous) if now is not None else Manifest.new(generated)
        m.generated = generated
        for g in gated:
            m.upsert(g.entry)
        out[:] = [m]
        return m

    commit(store, secret, edit, sign=sign, comment=comment or f"published {generated}",
           state=state, log=log)
    m = out[0]
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
    out: list[Manifest] = []

    def edit(live, previous):
        if live is None:
            raise RuntimeError("no manifest to modify")
        if not live.remove(pack_id, cycle):
            raise RuntimeError(f"no entry {pack_id}/{cycle} in manifest")
        live.generated = generated
        out[:] = [live]
        return live

    commit(store, secret, edit, sign=sign, log=log,
           comment=comment or f"retracted {pack_id}/{cycle} at {generated}")
    m = out[0]
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
    filled: list = []

    def edit(live, previous):
        if live is None:
            raise RuntimeError("no manifest to modify")
        filled.clear()
        for p in live.packs:
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
            return None
        live.generated = generated
        return live

    if secret is None:  # a read-only dry run: no key, nothing written
        live, previous = _load(store, secret)
        edit(live, previous)
        return filled
    if commit(store, secret, edit, sign=sign, log=log,
              comment=lambda m: f"backfilled {len(filled)} license(s) at {generated}") is not None:
        log(f"manifest: filled {len(filled)} license(s), signed + uploaded")
    return filled
