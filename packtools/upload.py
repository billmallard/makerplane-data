# SPDX-License-Identifier: Apache-2.0
"""Object stores — where packs and the manifest live.

The orchestrator talks to an :class:`ObjectStore` interface, so the same
pipeline code drives Cloudflare R2 in production and the local filesystem
in dry-run / tests. The R2 backend is the S3 API via boto3, imported
lazily so importing this module (and running the test suite) needs no
boto3 and no credentials.

R2 specifics: endpoint is ``https://<account-id>.r2.cloudflarestorage.com``;
zero egress fees; objects served at ``data.makerplane.org`` once the
custom domain is attached to the bucket.

Conditional writes (MANIFEST1): ``get_bytes_etag`` returns an object with
the ETag it was read at, and ``put_bytes(..., if_match=etag)`` (or
``if_none_match="*"`` for "must not exist yet") writes only if the object is
still that version, raising :class:`PreconditionFailed` otherwise. That
compare-and-set is what lets two manifest writers overlap without either
one silently dropping the other's entries (``packtools.publish.commit``).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
from pathlib import Path
from typing import Protocol


class PreconditionFailed(Exception):
    """A conditional put found the object changed (or present, or absent)
    since it was read: another writer got there first. Nothing was written."""


class ObjectStore(Protocol):
    def exists(self, key: str) -> bool: ...
    def put_file(self, key: str, path: str | Path, content_type: str | None = None) -> None: ...
    def put_bytes(self, key: str, data: bytes, content_type: str | None = None, *,
                  if_match: str | None = None, if_none_match: str | None = None) -> None: ...
    def get_bytes(self, key: str) -> bytes | None: ...
    def get_bytes_etag(self, key: str) -> tuple[bytes | None, str | None]: ...
    def get_file(self, key: str, dest: str | Path) -> bool: ...
    def list_keys(self, prefix: str) -> list[str]: ...


def _etag(data: bytes) -> str:
    """An S3-style ETag (quoted md5) for a single-part object."""
    return '"' + hashlib.md5(data).hexdigest() + '"'


class LocalStore:
    """Filesystem-backed store. Used for local builds, dry-run, and tests."""

    # Conditional puts compare-and-write under this lock, so threads racing
    # one LocalStore (the MANIFEST1 tests) see one winner, as R2 would.
    _cas = threading.Lock()

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        return self.root / key

    def exists(self, key: str) -> bool:
        return self._p(key).exists()

    def put_file(self, key: str, path: str | Path, content_type: str | None = None) -> None:
        dst = self._p(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(Path(path).read_bytes())

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None, *,
                  if_match: str | None = None, if_none_match: str | None = None) -> None:
        dst = self._p(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if if_match is None and if_none_match is None:
            dst.write_bytes(data)
            return
        with self._cas:
            cur = self.get_bytes(key)
            if if_none_match == "*" and cur is not None:
                raise PreconditionFailed(f"{key} already exists")
            if if_match is not None and (cur is None or _etag(cur) != if_match):
                raise PreconditionFailed(f"{key} changed since it was read")
            tmp = dst.with_name(dst.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, dst)

    def get_bytes(self, key: str) -> bytes | None:
        p = self._p(key)
        return p.read_bytes() if p.exists() else None

    def get_bytes_etag(self, key: str) -> tuple[bytes | None, str | None]:
        """``key``'s bytes and the ETag to pass back as ``if_match``;
        ``(None, None)`` if absent."""
        with self._cas:
            raw = self.get_bytes(key)
        return (raw, _etag(raw)) if raw is not None else (None, None)

    def get_file(self, key: str, dest: str | Path) -> bool:
        """Copy ``key`` to ``dest``. False (and nothing written) if absent."""
        p = self._p(key)
        if not p.exists():
            return False
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
        return True

    def list_keys(self, prefix: str) -> list[str]:
        """Every key starting with ``prefix``, sorted."""
        keys = (p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
        return sorted(k for k in keys if k.startswith(prefix))


class R2Store:
    """Cloudflare R2 (S3 API) backend. boto3 is imported lazily."""

    def __init__(self, bucket: str, *, endpoint_url: str,
                 access_key_id: str, secret_access_key: str):
        import boto3  # lazy: only the production path needs it
        self.bucket = bucket
        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
        )

    @classmethod
    def from_env(cls, bucket: str | None = None) -> "R2Store":
        """Build from the R2_* environment (the GitHub Actions secrets)."""
        missing = [v for v in ("R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
                   if not os.environ.get(v)]
        if missing:
            raise RuntimeError(f"missing R2 env vars: {', '.join(missing)}")
        return cls(
            bucket or os.environ.get("R2_BUCKET", "makerplane-data"),
            endpoint_url=os.environ["R2_ENDPOINT"],
            access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        )

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError
        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def put_file(self, key: str, path: str | Path, content_type: str | None = None) -> None:
        extra = {"ContentType": content_type} if content_type else {}
        self._s3.upload_file(str(path), self.bucket, key, ExtraArgs=extra)

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None, *,
                  if_match: str | None = None, if_none_match: str | None = None) -> None:
        from botocore.exceptions import ClientError
        kw = {"ContentType": content_type} if content_type else {}
        if if_match is not None or if_none_match is not None:
            self._check_conditional_writes()
            if if_match is not None:
                kw["IfMatch"] = if_match
            if if_none_match is not None:
                kw["IfNoneMatch"] = if_none_match
        try:
            self._s3.put_object(Bucket=self.bucket, Key=key, Body=data, **kw)
        except ClientError as e:
            if _precondition_failed(e):
                raise PreconditionFailed(f"{key}: {e.response['Error'].get('Code')}") from None
            raise

    def get_bytes(self, key: str) -> bytes | None:
        return self.get_bytes_etag(key)[0]

    def get_bytes_etag(self, key: str) -> tuple[bytes | None, str | None]:
        """Body and ETag from one GET, so the ETag is the version the bytes
        are (never a HEAD racing a writer)."""
        from botocore.exceptions import ClientError
        try:
            r = self._s3.get_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return None, None
            raise
        return r["Body"].read(), r.get("ETag")

    #: Where the conditional-write self-check writes. Never a production key.
    PROBE_KEY = "test-fixtures/cas-probe.txt"

    def _check_conditional_writes(self) -> None:
        """Refuse to trust a conditional put the store would not enforce.

        R2 documents If-Match / If-None-Match on PutObject, but a store (or a
        proxy, or a future API change) that silently ignored them would turn
        every compare-and-set back into a blind overwrite with no error. So
        once per process, before the first conditional write, put a probe
        object with a validator that cannot match and require the 412."""
        if getattr(self, "_cas_checked", False):
            return
        if self.get_bytes_etag(self.PROBE_KEY)[0] is None:
            try:
                self.put_bytes(self.PROBE_KEY, b"MANIFEST1 conditional-write probe\n",
                               content_type="text/plain")
            except PreconditionFailed:
                pass
        for kw in ({"IfMatch": '"' + "0" * 32 + '"'}, {"IfNoneMatch": "*"}):
            try:
                self._s3.put_object(Bucket=self.bucket, Key=self.PROBE_KEY,
                                    Body=b"must not be written\n", **kw)
            except Exception as e:  # noqa: BLE001 -- only a 412 is the pass
                if _precondition_failed(e):
                    continue
                raise
            raise RuntimeError(f"object store accepted a put with a failing {next(iter(kw))}: "
                               "conditional writes are not enforced, refusing to "
                               "write the manifest")
        self._cas_checked = True

    def get_file(self, key: str, dest: str | Path) -> bool:
        """Stream ``key`` to ``dest`` (a terrain pack is gigabytes; never
        held in memory). False (and nothing written) if absent."""
        if not self.exists(key):
            return False
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        self._s3.download_file(self.bucket, key, str(dest))
        return True

    def list_keys(self, prefix: str) -> list[str]:
        """Every key starting with ``prefix``, sorted (all pages of the listing)."""
        keys = []
        for page in self._s3.get_paginator("list_objects_v2").paginate(
                Bucket=self.bucket, Prefix=prefix):
            keys.extend(o["Key"] for o in page.get("Contents", []))
        return sorted(keys)


def _precondition_failed(e) -> bool:
    """A botocore error meaning "the condition did not hold": 412, or the
    409 S3 returns when a concurrent conditional write to the key is in
    flight (ConditionalRequestConflict). Both mean re-read and retry."""
    r = getattr(e, "response", None) or {}
    code = r.get("Error", {}).get("Code")
    status = r.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return (status == 412 or code in ("PreconditionFailed", "ConditionalRequestConflict"))
