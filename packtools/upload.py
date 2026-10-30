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
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Protocol


class ObjectStore(Protocol):
    def exists(self, key: str) -> bool: ...
    def put_file(self, key: str, path: str | Path, content_type: str | None = None) -> None: ...
    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None: ...
    def get_bytes(self, key: str) -> bytes | None: ...
    def get_file(self, key: str, dest: str | Path) -> bool: ...
    def list_keys(self, prefix: str) -> list[str]: ...


class LocalStore:
    """Filesystem-backed store. Used for local builds, dry-run, and tests."""

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

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        dst = self._p(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)

    def get_bytes(self, key: str) -> bytes | None:
        p = self._p(key)
        return p.read_bytes() if p.exists() else None

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

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        kw = {"ContentType": content_type} if content_type else {}
        self._s3.put_object(Bucket=self.bucket, Key=key, Body=data, **kw)

    def get_bytes(self, key: str) -> bytes | None:
        from botocore.exceptions import ClientError
        try:
            return self._s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

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
