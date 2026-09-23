# SPDX-License-Identifier: Apache-2.0
"""The catalog manifest — the single contract between all three legs.

One JSON document at a stable URL (``https://data.makerplane.org/manifest.json``)
lists every available pack with its currency window, size, sha256 and
download URL. The build pipeline *writes* it, the distribution layer
*serves* it, the Pi updater *reads* it. Freeze this schema and the three
legs evolve independently.

Signing note: the manifest is signed over its **exact serialized bytes**.
The Pi verifies the bytes it downloaded — it never re-serializes first —
so canonical formatting here is for stable diffs, not for verification
correctness. Use :func:`to_bytes` on both write and any hashing.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import MANIFEST_VERSION
from .packmeta import PackMeta, KINDS
from .signing import sha256_file


def _rank_key(p: "PackEntry") -> tuple:
    """Sort key shared by ``select()`` and ``prune_old_cycles()``: latest
    effective date, then canonical cycles before hyphen-suffixed ones,
    then plain string compare within each group.

    A hyphenated cycle (``"2026q3r1-smoketest"``) is the test/pre-release
    convention (docs/roads.md) and must never outrank the canonical cycle
    it stands in for, however it happens to sort lexically -- a plain
    ``(effective, cycle)`` compare picked the test build because
    ``"...smoketest" > "2026q3r1"`` (makerplane-data#60/AER-1109).
    """
    return (p.effective or "", "-" not in p.cycle, p.cycle)


@dataclass
class PackEntry:
    """One downloadable pack in the catalog (a superset of its pack_meta)."""
    id: str
    kind: str
    cycle: str
    bytes: int
    sha256: str
    url: str
    effective: str | None = None      # ISO date; None => non-cyclical
    expires: str | None = None        # ISO date, exclusive; None => never
    regions: list[str] = field(default_factory=list)
    attribution: str = ""
    # license/license_url: additive, carried through from PackMeta/Source (see
    # packmeta.PackMeta) so a pack's license is checkable from the manifest
    # alone -- no need to download and open the pack. "" means unspecified.
    license: str = ""
    license_url: str = ""
    min_pyefis: str | None = None
    tiles_bbox: list[float] | None = None   # [lat_min, lon_min, lat_max, lon_max]

    # --- currency helpers (shared with the Pi updater) ---
    def covers(self, day: _dt.date) -> bool:
        """True if ``day`` falls in [effective, expires). Non-cyclical => always."""
        if self.effective is None:
            return True
        eff = _dt.date.fromisoformat(self.effective)
        if day < eff:
            return False
        if self.expires is None:
            return True
        return day < _dt.date.fromisoformat(self.expires)

    def days_until_expiry(self, day: _dt.date) -> int | None:
        if self.expires is None:
            return None
        return (_dt.date.fromisoformat(self.expires) - day).days

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, d: dict) -> "PackEntry":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})

    @classmethod
    def from_pack(cls, path: str | Path, meta: PackMeta, *, url: str,
                  regions: list[str] | None = None, min_pyefis: str | None = None,
                  tiles_bbox: list[float] | None = None) -> "PackEntry":
        """Build an entry from a pack file on disk plus its pack_meta."""
        path = Path(path)
        return cls(
            id=meta.id, kind=meta.kind, cycle=meta.cycle,
            effective=meta.effective, expires=meta.expires,
            attribution=meta.attribution,
            license=meta.license, license_url=meta.license_url,
            bytes=path.stat().st_size,
            sha256=sha256_file(path),
            url=url,
            regions=regions or [],
            min_pyefis=min_pyefis,
            tiles_bbox=tiles_bbox,
        )


@dataclass
class Manifest:
    generated: str                                  # ISO-8601 UTC timestamp
    manifest_version: int = MANIFEST_VERSION
    packs: list[PackEntry] = field(default_factory=list)
    regions: dict[str, dict] = field(default_factory=dict)
    # Pack kinds a lenient (client-side) parse dropped as unrecognized. Never
    # serialized (to_obj/to_bytes list fields explicitly); diagnostic only.
    dropped_kinds: list[str] = field(default_factory=list)

    # --- construction ---
    @staticmethod
    def new(generated: str) -> "Manifest":
        """Create an empty manifest. ``generated`` is injected (not wall-clock)
        so cloud builds are reproducible and tests are deterministic."""
        return Manifest(generated=generated)

    def upsert(self, entry: PackEntry) -> None:
        """Add or replace a pack, keyed by (id, cycle)."""
        self.packs = [p for p in self.packs if not (p.id == entry.id and p.cycle == entry.cycle)]
        self.packs.append(entry)
        self.packs.sort(key=lambda p: (p.id, p.cycle))

    def remove(self, pack_id: str, cycle: str) -> bool:
        """Drop the (id, cycle) entry if present. Returns whether anything
        was removed -- for retracting a specific bad publish (e.g. a
        states-limited test build that was uploaded under the production
        id by mistake) rather than pruning by recency."""
        before = len(self.packs)
        self.packs = [p for p in self.packs if not (p.id == pack_id and p.cycle == cycle)]
        return len(self.packs) != before

    def prune_old_cycles(self, keep: int = 2) -> None:
        """Keep at most ``keep`` most-recent cycles per pack id (current + next,
        plus one extra by default), dropping older ones from the catalog."""
        by_id: dict[str, list[PackEntry]] = {}
        for p in self.packs:
            by_id.setdefault(p.id, []).append(p)
        kept: list[PackEntry] = []
        for entries in by_id.values():
            entries.sort(key=_rank_key)
            kept.extend(entries[-keep:])
        kept.sort(key=lambda p: (p.id, p.cycle))
        self.packs = kept

    # --- queries (used by the Pi updater) ---
    def for_id(self, pack_id: str) -> list[PackEntry]:
        return [p for p in self.packs if p.id == pack_id]

    def select(self, pack_id: str, day: _dt.date) -> PackEntry | None:
        """The current edition of ``pack_id`` for ``day``: among the entries
        whose currency window covers ``day``, the newest one (else None).

        Calendar packs (airports/navaids) have disjoint windows, so at most
        one covers a given day and "newest" is moot. No-date packs
        (water/highways, ``effective is None``) all cover every day, so this
        is what lets a re-issue (r5 -> r6) win over the edition it replaces
        while both are still listed in the catalog -- without it the picker
        returned whichever happened to be first in the list (the older one).
        Ordering matches ``prune_old_cycles``: latest effective date, then
        :func:`_rank_key`'s canonical-over-test cycle rank.
        """
        covering = [p for p in self.for_id(pack_id) if p.covers(day)]
        if not covering:
            return None
        return max(covering, key=_rank_key)

    # --- serialization ---
    def to_obj(self) -> dict:
        return {
            "manifest_version": self.manifest_version,
            "generated": self.generated,
            "packs": [p.as_dict() for p in self.packs],
            "regions": self.regions,
        }

    def to_bytes(self) -> bytes:
        """Canonical bytes: stable key order, 2-space indent, trailing NL.
        Sign and hash THESE bytes; write THESE bytes to disk."""
        return (json.dumps(self.to_obj(), indent=2, sort_keys=True,
                           ensure_ascii=False) + "\n").encode("utf-8")

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        path.write_bytes(self.to_bytes())
        return path

    @classmethod
    def from_bytes(cls, raw: bytes, *, lenient: bool = False) -> "Manifest":
        return cls.from_obj(json.loads(raw), lenient=lenient)

    @classmethod
    def from_obj(cls, obj: dict, *, lenient: bool = False) -> "Manifest":
        """``lenient=True`` is the client-parsing path: a pack kind this
        client doesn't recognize yet (an older device fetching a manifest
        published after a newer kind landed) is dropped rather than failing
        the whole catalog -- see ``drop_unknown_kinds``. The build side
        (publish/verify) leaves this False so a genuinely malformed manifest
        still fails loudly (``test_validate_rejects_bad_manifests``)."""
        dropped: list[dict] = []
        if lenient:
            obj, dropped = drop_unknown_kinds(obj)
        validate(obj)
        return cls(
            generated=obj["generated"],
            manifest_version=obj.get("manifest_version", MANIFEST_VERSION),
            packs=[PackEntry.from_dict(p) for p in obj.get("packs", [])],
            regions=obj.get("regions", {}),
            dropped_kinds=sorted({p.get("kind") for p in dropped}),
        )

    @classmethod
    def read(cls, path: str | Path) -> "Manifest":
        return cls.from_bytes(Path(path).read_bytes())


def drop_unknown_kinds(obj: dict) -> tuple[dict, list[dict]]:
    """Split a raw manifest object's packs by whether this client's ``KINDS``
    recognizes them. Used only by the lenient (client) parse path.

    A published manifest can legitimately contain a pack kind this build of
    packtools/pyefis_data predates -- a new kind lands on one branch before
    every device has taken the client release that knows about it. That is
    forward-incompatibility, not corruption: the fix is to ignore the pack
    this client can't use yet, not to reject the entire signed catalog and
    lose every *other* pack too (makerplane-data AER-1935).
    """
    packs = obj.get("packs", [])
    if not isinstance(packs, list):
        return obj, []
    kept, dropped = [], []
    for p in packs:
        (kept if isinstance(p, dict) and p.get("kind") in KINDS else dropped).append(p)
    if not dropped:
        return obj, []
    return {**obj, "packs": kept}, dropped


class ManifestError(ValueError):
    """Raised on a structurally invalid manifest."""


def validate(obj: dict) -> None:
    """Structural validation. Raises ManifestError on any problem.

    Intentionally strict: a malformed manifest must fail loudly on the
    build side rather than ship something the Pi will choke on.
    """
    if not isinstance(obj, dict):
        raise ManifestError("manifest must be a JSON object")
    if obj.get("manifest_version") != MANIFEST_VERSION:
        raise ManifestError(
            f"manifest_version {obj.get('manifest_version')!r} != {MANIFEST_VERSION}")
    if not isinstance(obj.get("generated"), str):
        raise ManifestError("'generated' must be an ISO-8601 string")
    packs = obj.get("packs")
    if not isinstance(packs, list):
        raise ManifestError("'packs' must be a list")
    seen: set[tuple[str, str]] = set()
    for i, p in enumerate(packs):
        where = f"packs[{i}]"
        for required in ("id", "kind", "cycle", "bytes", "sha256", "url"):
            if required not in p:
                raise ManifestError(f"{where} missing required field '{required}'")
        if p["kind"] not in KINDS:
            raise ManifestError(f"{where} unknown kind {p['kind']!r}")
        if not (isinstance(p["sha256"], str) and len(p["sha256"]) == 64):
            raise ManifestError(f"{where} sha256 must be 64 hex chars")
        if not isinstance(p["bytes"], int) or p["bytes"] < 0:
            raise ManifestError(f"{where} bytes must be a non-negative int")
        key = (p["id"], p["cycle"])
        if key in seen:
            raise ManifestError(f"{where} duplicate (id, cycle) {key}")
        seen.add(key)
        # cyclical packs need an effective date to be selectable by date
        if p.get("expires") is not None and p.get("effective") is None:
            raise ManifestError(f"{where} has 'expires' but no 'effective'")
    if not isinstance(obj.get("regions", {}), dict):
        raise ManifestError("'regions' must be an object")
