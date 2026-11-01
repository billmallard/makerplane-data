# SPDX-License-Identifier: Apache-2.0
"""Deploy the static site (``site/``) to the root of the pack bucket.

navdata.aerocommons.org serves ``site/index.html``, ``data.yaml.sample`` and
``assets/`` from the root of the same R2 bucket that holds the packs; a
Cloudflare Transform Rule rewrites ``/`` to ``/index.html``
(docs/cloudflare_setup.md). Until AER-4098 the upload was a manual
``wrangler r2 object put`` under Bill's OAuth login, which no agent holds, so
every site edit waited on him. The ``site.yml`` workflow runs this with the
repo's R2 secrets instead.

Each file is written to its own key, and only when its bytes differ from the
live object. A key the pack pipeline owns (the manifest, its signature, or
anything under ``packs/``, ``reports/`` or ``schema/``) is refused outright, so a stray file under
``site/`` can never overwrite signed data.
"""

from __future__ import annotations

from pathlib import Path

from .publish import MANIFEST_KEY, SIG_KEY

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".sample": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".js": "text/javascript; charset=utf-8",
}

_RESERVED_KEYS = {MANIFEST_KEY, SIG_KEY}
# reports/ belongs to the publish paths (records) and `packtool report-site`
# (the pages rendered from them), never to site/; schema/ likewise to the
# publish paths (captures) and `packtool schema-site` (SCH1).
_RESERVED_PREFIXES = ("packs/", "reports/", "schema/")


def site_files(root: str | Path) -> list[tuple[str, Path, str]]:
    """``(key, path, content_type)`` for every file under ``root``, sorted.

    Raises ``ValueError`` on a file with no known content type or one whose
    key belongs to the pack pipeline, before anything is uploaded."""
    root = Path(root)
    out = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        key = path.relative_to(root).as_posix()
        if key in _RESERVED_KEYS or key.startswith(_RESERVED_PREFIXES):
            raise ValueError(f"{key}: reserved for the pack pipeline, not the site")
        ctype = CONTENT_TYPES.get(path.suffix)
        if ctype is None:
            raise ValueError(f"{key}: no content type for {path.suffix!r}; "
                             f"add it to CONTENT_TYPES")
        out.append((key, path, ctype))
    return out


def deploy(store, root: str | Path, *, dry_run: bool = False, log=print) -> list[list]:
    """Upload each changed site file. Returns ``[key, bytes, status]`` rows,
    status being ``new``, ``changed`` or ``unchanged``. ``dry_run`` compares
    against the live objects and writes nothing."""
    files = site_files(root)
    rows = []
    for key, path, ctype in files:
        data = path.read_bytes()
        live = store.get_bytes(key)
        status = "new" if live is None else ("unchanged" if live == data else "changed")
        if status != "unchanged" and not dry_run:
            store.put_bytes(key, data, content_type=ctype)
        rows.append([key, len(data), status])
        log(f"{status:>9}  {key}")
    return rows
