# SPDX-License-Identifier: Apache-2.0
"""Pack format pages on navdata.aerocommons.org (SCH1, makerplane-data#137).

    packtool schema-site --bucket makerplane-data            render + upload changed pages
    packtool schema-site --from DIR --out PREVIEW            render a local preview
    packtool schema-site --bucket makerplane-data --bootstrap
                                                            first capture each live id
                                                            that has none, then render

The tables a pack carries are defined in two repos: procedures, plates and
airspace in ``packtools/build/``, airports, navaids, obstacles, water and
highways in pyEfis ``tools/``. A hand-written page would drift from both, so
every page here is generated from the packs themselves.

**Capture.** Each publish path (:func:`packtools.publish.publish` and the
cyclical orchestrator) calls :func:`put_captures` once the manifest naming
the new packs is signed. It reads each pack and merges a ``pack-schema/1``
capture into ``schema/<kind>.json``, one entry per pack id:

  * sqlite packs: every table's ``sqlite_master`` statement verbatim (SQLite
    keeps the original ``CREATE`` text, so inline ``--`` comments survive and
    become column descriptions), ``PRAGMA table_info``, the explicit indexes,
    R-trees, views and triggers;
  * zip packs (terrain): the member layout, as path patterns with counts;
  * both: the ``pack_meta`` keys and ``schema_version``, and which builder
    made the pack (the tool, plus the commits from its pack-report/1 record).

**Render.** :func:`build` turns the captures into ``schema/index.html``,
``schema/<kind>.html`` and ``schema/index.json``. Pages carry no timestamp,
so an unchanged capture renders to unchanged bytes and nothing re-uploads.
A capture is rewritten on every publish, so a ``schema_version`` change
reaches the pages on the next render with no manual step.

**Bootstrap.** :func:`bootstrap` captures each live pack id that has no
capture yet, from the published pack: the smallest pack of each sqlite kind
(downloaded and checked against the signed manifest's sha256), and every
zip pack (its central directory read over HTTP ranges). It never replaces a
capture a publish path wrote.

The report site's hygiene pass and lint (:mod:`packtools.reportsite`) gate
every page: a banned word, local path, IP address or internal host fails the
render and nothing is written.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import tempfile
import urllib.request
import zipfile
from dataclasses import fields
from pathlib import Path
from typing import Any, Callable

from . import packmeta
from .manifest import MANIFEST_VERSION, NOTICE, Manifest, PackEntry
from .reportsite import (BUILDER_REPOS, CSS, KIND_LABELS, REPO_URL, SiteError, clean,
                         commit_link, e, lint)

SCHEMA = "pack-schema/1"
PREFIX = "schema/"
INDEX_KEY = "schema/index.json"
INDEX_SCHEMA = "schema-index/1"
DESCRIPTIONS = Path(__file__).resolve().parent.parent / "docs" / "schema_descriptions.yaml"

#: The tool that writes each kind's tables (or zip members). Named on every
#: page; the commit comes from the pack's own record when it has one.
BUILDERS = {
    "navdata": ("pyEfis", "tools/build_airport_db.py"),
    "airports": ("makerplane-data", "packtools/ourairports.py"),
    "navaids": ("pyEfis", "tools/build_navaid_db.py"),
    "obstacles": ("pyEfis", "tools/build_obstacle_db.py"),
    "procedures": ("makerplane-data", "packtools/build/procedures.py"),
    "airspace": ("makerplane-data", "packtools/build/airspace.py"),
    "plates": ("makerplane-data", "packtools/make_plates.py"),
    "terrain": ("makerplane-data", "packtools/make_terrain.py"),
    "water": ("pyEfis", "tools/fetch_geofabrik_water.py"),
    "highways": ("pyEfis", "tools/build_highway_db.py"),
}
_REPO_URLS = {"makerplane-data": REPO_URL, **BUILDER_REPOS}

LICENSE_TEXT = (
    "The pack formats described here are published under the Apache License 2.0, "
    "so anyone may write a tool that reads or writes them. The text of these pages "
    "is CC BY 4.0. The data inside each pack carries its own licence, named in the "
    "pack's manifest entry (license, license_url) and its attribution string.")


def capture_key(kind: str) -> str:
    return f"{PREFIX}{kind}.json"


# --- capture ------------------------------------------------------------------

_COMMENT = re.compile(r"^\s*[\"`\[]?(\w+)[\"`\]]?\s.*?--\s*(.+?)\s*$")


def column_comments(sql: str) -> dict[str, str]:
    """Trailing ``--`` comments in a ``CREATE TABLE`` statement, keyed by the
    column the line declares."""
    out = {}
    for line in (sql or "").splitlines():
        if m := _COMMENT.match(line):
            out[m.group(1)] = m.group(2)
    return out


def _rtree_shadows(names: list[str], rtrees: set[str]) -> set[str]:
    return {n for n in names for r in rtrees if n in (f"{r}_node", f"{r}_rowid", f"{r}_parent")}


def capture_sqlite(path: str | Path) -> dict:
    """Tables, indexes, R-trees, views and triggers of one sqlite pack."""
    con = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master "
                           "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
        virtual = {n: s for t, n, _, s in rows
                   if t == "table" and s and s.lstrip().upper().startswith("CREATE VIRTUAL")}
        rtrees = {n for n, s in virtual.items() if re.search(r"USING\s+rtree", s, re.I)}
        shadows = _rtree_shadows([n for _, n, _, _ in rows], rtrees)
        tables, rt, other_virtual, indexes, views, triggers = {}, {}, {}, {}, {}, {}
        for typ, name, tbl, sql in rows:
            if name in shadows:
                continue
            if typ == "table" and name in rtrees:
                cols = [r[1] for r in con.execute(f'PRAGMA table_info("{name}")')]
                rt[name] = {"sql": sql, "columns": cols}
            elif typ == "table" and name in virtual:
                other_virtual[name] = {"sql": sql}
            elif typ == "table":
                notes = column_comments(sql)
                cols = []
                for cid, cname, ctype, notnull, default, pk in con.execute(
                        f'PRAGMA table_info("{name}")'):
                    c = {"name": cname, "type": ctype, "notnull": bool(notnull),
                         "default": default, "pk": pk}
                    if cname in notes:
                        c["comment"] = notes[cname]
                    cols.append(c)
                tables[name] = {"sql": sql, "columns": cols}
            elif typ == "index" and sql:
                unique = bool(re.match(r"\s*CREATE\s+UNIQUE", sql, re.I))
                cols = [r[2] for r in con.execute(f'PRAGMA index_info("{name}")')]
                indexes[name] = {"table": tbl, "sql": sql, "unique": unique, "columns": cols}
            elif typ == "view":
                views[name] = {"sql": sql}
            elif typ == "trigger":
                triggers[name] = {"table": tbl, "sql": sql}
        keys = []
        if "pack_meta" in tables:
            keys = sorted(r[0] for r in con.execute("SELECT key FROM pack_meta"))
    finally:
        con.close()
    out: dict[str, Any] = {"container": "sqlite", "tables": tables, "indexes": indexes,
                           "rtrees": rt, "pack_meta_keys": keys}
    for k, v in (("virtual", other_virtual), ("views", views), ("triggers", triggers)):
        if v:
            out[k] = v
    return out


def member_pattern(name: str) -> str:
    """A zip member's path with each run of digits replaced by ``#``s."""
    return re.sub(r"\d+", lambda m: "#" * len(m.group(0)), name)


def zip_layout(z: zipfile.ZipFile) -> dict:
    """Member patterns of one zip pack: count, total size, compression and
    the first example of each, sorted by pattern."""
    groups: dict[str, dict] = {}
    for info in z.infolist():
        if info.is_dir():
            continue
        g = groups.setdefault(member_pattern(info.filename), {
            "count": 0, "bytes": 0, "example": info.filename, "compression": set()})
        g["count"] += 1
        g["bytes"] += info.file_size
        g["example"] = min(g["example"], info.filename)
        g["compression"].add("deflated" if info.compress_type == zipfile.ZIP_DEFLATED
                             else "stored" if info.compress_type == zipfile.ZIP_STORED
                             else str(info.compress_type))
    layout = [{"pattern": p, "count": g["count"], "bytes": g["bytes"],
               "example": g["example"], "compression": sorted(g["compression"])}
              for p, g in sorted(groups.items())]
    keys = []
    if "pack_meta.json" in z.namelist():
        keys = sorted(json.loads(z.read("pack_meta.json")))
    return {"container": "zip", "layout": layout, "pack_meta_keys": keys}


def builder_block(kind: str, built: dict | None) -> dict:
    """The tool that made a pack, and the commits its record names."""
    repo, tool = BUILDERS.get(kind, ("", ""))
    built = built or {}
    refs = dict(built.get("builder_refs") or {})
    return {"repo": repo, "tool": tool,
            "commit": refs.get(repo) if repo != "makerplane-data" else built.get("commit"),
            "makerplane_data_commit": built.get("commit"), "builder_refs": refs}


def capture(path: str | Path, *, built: dict | None = None, source: str = "publish",
            zip_file: zipfile.ZipFile | None = None) -> dict:
    """One pack's ``pack-schema/1`` capture. ``built`` is the ``built`` block
    of its pack-report/1 record, if it has one. ``zip_file`` lets a caller
    hand in an already-open zip (a remote one read over HTTP ranges)."""
    if zip_file is not None:
        meta = packmeta.PackMeta.from_dict(json.loads(zip_file.read("pack_meta.json")))
        body = zip_layout(zip_file)
    else:
        meta = packmeta.read(path)
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                body = zip_layout(z)
        else:
            body = capture_sqlite(path)
    return {"schema": SCHEMA, "source": source,
            "pack": {"id": meta.id, "kind": meta.kind, "cycle": meta.cycle,
                     "schema_version": str(meta.schema_version)},
            "builder": builder_block(meta.kind, built), **body}


def _dumps(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_kind(store, kind: str) -> dict:
    """The ``schema/<kind>.json`` document, or an empty one if absent or not
    a ``pack-schema/1`` document."""
    raw = store.get_bytes(capture_key(kind))
    try:
        doc = json.loads(raw) if raw else None
    except ValueError:
        doc = None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or doc.get("kind") != kind:
        return {"schema": SCHEMA, "kind": kind, "packs": {}}
    doc.setdefault("packs", {})
    return doc


def merge(store, caps: list[dict], *, replace: bool = True, log=print) -> list[str]:
    """Merge captures into their kind documents and write each changed one.
    ``replace=False`` keeps an id's existing capture. Returns the keys written."""
    by_kind: dict[str, list[dict]] = {}
    for c in caps:
        by_kind.setdefault(c["pack"]["kind"], []).append(c)
    written = []
    for kind, items in sorted(by_kind.items()):
        doc = load_kind(store, kind)
        before = _dumps(doc)
        for c in items:
            if replace or c["pack"]["id"] not in doc["packs"]:
                doc["packs"][c["pack"]["id"]] = c
        data = _dumps(doc)
        if data != before:
            store.put_bytes(capture_key(kind), data, content_type="application/json")
            written.append(capture_key(kind))
            log(f"schema: wrote {capture_key(kind)} "
                f"({', '.join(sorted(c['pack']['id'] for c in items))})")
    return written


def put_captures(store, packs: list[tuple[str | Path, dict | None]], *, log=print) -> list[str]:
    """Capture each just-published ``(pack path, record)`` and merge it into
    ``schema/<kind>.json``. Called after the manifest is signed; a pack that
    cannot be read is logged and skipped, never a reason to fail a publish
    that already happened (its previous capture keeps standing)."""
    caps = []
    for path, rec in packs:
        try:
            caps.append(capture(path, built=(rec or {}).get("built")))
        except Exception as ex:     # noqa: BLE001 -- the publish has already happened
            log(f"WARN schema: no capture of {Path(path).name}: {type(ex).__name__}: {ex}")
    try:
        return merge(store, caps, log=log)
    except Exception as ex:         # noqa: BLE001
        log(f"WARN schema: captures not written: {type(ex).__name__}: {ex}")
        return []


# --- bootstrap ------------------------------------------------------------------

class HTTPRangeFile(io.RawIOBase):
    """A read-only, seekable view of a remote file, read with HTTP ``Range``
    requests, so :mod:`zipfile` can list a multi-GB pack's members without
    downloading it. ``fetch(url, start, end)`` returns bytes start..end
    inclusive; tests inject one."""

    def __init__(self, url: str, size: int, fetch: Callable[[str, int, int], bytes] | None = None):
        self.url, self.size, self.pos = url, size, 0
        self.fetch = fetch or _http_range

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        self.pos = base + offset
        return self.pos

    def readinto(self, b):
        if self.pos >= self.size or not len(b):
            return 0
        data = self.fetch(self.url, self.pos, min(self.pos + len(b), self.size) - 1)
        b[:len(data)] = data
        self.pos += len(data)
        return len(data)


def _http_range(url: str, start: int, end: int) -> bytes:
    req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}",
                                               "User-Agent": "makerplane-data-schema-site"})
    with urllib.request.urlopen(req, timeout=60) as r:
        if r.status != 206:
            raise SiteError(f"{url}: range request answered {r.status}, not 206")
        return r.read()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _record_built(store, p: PackEntry) -> dict | None:
    if not p.report_url:
        return None
    from .reportsite import record_key
    raw = store.get_bytes(record_key(p.id, p.cycle))
    if not raw or (p.report_sha256 and hashlib.sha256(raw).hexdigest() != p.report_sha256):
        return None
    return json.loads(raw).get("built")


def bootstrap(store, manifest: dict, *, fetch: Callable | None = None, log=print) -> list[str]:
    """First capture of each live pack id with none: the smallest pack of
    every sqlite kind still uncaptured, and every uncaptured zip pack. The
    current edition (``Manifest.select`` order) of each id is the one read."""
    m = Manifest.from_obj(manifest)
    latest: dict[str, PackEntry] = {}
    for p in m.packs:
        cur = latest.get(p.id)
        if cur is None or _rank(p) > _rank(cur):
            latest[p.id] = p
    have = {k: set(load_kind(store, k)["packs"]) for k in {p.kind for p in latest.values()}}
    caps = []
    sqlite_done: set[str] = {k for k, ids in have.items() if ids}
    for p in sorted(latest.values(), key=lambda p: (p.bytes, p.id)):
        if p.id in have[p.kind]:
            continue
        key = f"packs/{p.id}-{p.cycle}.pack"
        is_zip = p.kind in ("terrain", "plates")
        if not is_zip and p.kind in sqlite_done:
            continue
        built = _record_built(store, p)
        try:
            if is_zip:
                raw = io.BufferedReader(HTTPRangeFile(p.url, p.bytes, fetch), buffer_size=1 << 16)
                with zipfile.ZipFile(raw) as z:
                    c = capture(None, built=built, zip_file=z,
                                source="bootstrap: zip directory read from the published pack")
            else:
                with tempfile.TemporaryDirectory() as tmp:
                    dest = Path(tmp) / "pack"
                    if not store.get_file(key, dest):
                        raise SiteError(f"{key} is not in the store")
                    if _sha256(dest) != p.sha256:
                        raise SiteError(f"{key} does not match the signed manifest's sha256")
                    c = capture(dest, built=built,
                                source="bootstrap: read from the published pack")
                sqlite_done.add(p.kind)
        except Exception as ex:     # noqa: BLE001 -- one bad pack must not stop the rest
            log(f"WARN schema bootstrap: {p.id} {p.cycle}: {type(ex).__name__}: {ex}")
            continue
        if (c["pack"]["id"], c["pack"]["cycle"]) != (p.id, p.cycle):
            log(f"WARN schema bootstrap: {key} says it is {c['pack']['id']} {c['pack']['cycle']}")
            continue
        log(f"schema bootstrap: captured {p.id} {p.cycle}")
        caps.append(c)
    return merge(store, caps, replace=False, log=log)


def _rank(p: PackEntry) -> tuple:
    from .manifest import _rank_key
    return _rank_key(p)


# --- descriptions -------------------------------------------------------------

def load_descriptions(path: str | Path = DESCRIPTIONS) -> dict[str, str]:
    """``kind.table.column`` (and ``manifest.*``, ``pack_meta.*``,
    ``<kind>.layout.<pattern>``) -> one-line description."""
    import yaml
    p = Path(path)
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {str(k): str(v).strip() for k, v in data.items()}


# --- rendering ------------------------------------------------------------------

def _link(url: str, text: str) -> str:
    return f'<a href="{e(url)}">{e(text)}</a>'


def page(title: str, body: str, *, root: str, commit: str) -> str:
    footer = (f"Generated from the published packs by makerplane-data "
              f"{commit_link(REPO_URL, commit)}. " if commit
              else "Generated from the published packs. ")
    return f"""<!DOCTYPE html>
<!-- SPDX-License-Identifier: Apache-2.0 -->
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} &middot; Navigation Data &middot; AeroCommons</title>
<link rel="icon" href="{root}assets/aerocommons-logo.svg">
<style>{CSS}{EXTRA_CSS}</style>
<script defer src="{root}assets/analytics.js"></script>
</head><body>
<header class="site"><div class="wrap brandbar">
<a class="ac" href="{root}index.html"><img src="{root}assets/aerocommons-logo.svg" alt="">
<span class="name">AeroCommons</span></a><span class="crumb">Navigation Data &middot;
<a href="{root}schema/index.html">pack formats</a> &middot;
<a href="{root}reports/index.html">test reports</a></span></div></header>
<main class="wrap">
{body}
</main>
<footer class="site"><div class="wrap">{footer}
<a href="{root}schema/index.json">schema/index.json</a> &middot;
<a href="{root}manifest.json">catalog</a> &middot; <a href="{REPO_URL}">source</a></div></footer>
</body></html>
"""


def _tool_html(b: dict) -> str:
    repo, tool = b.get("repo") or "", b.get("tool") or ""
    if not repo:
        return "builder not recorded"
    base = _REPO_URLS.get(repo)
    sha = b.get("commit")
    where = (_link(f"{base}/blob/{sha}/{tool}", f"{repo} {tool}") if base and sha
             else f"{e(repo)} <code>{e(tool)}</code>")
    at = f" at {commit_link(base, sha)}" if base and sha else " (commit not recorded)"
    extra = ""
    mpd = b.get("makerplane_data_commit")
    if repo != "makerplane-data" and mpd:
        extra = f", packed by makerplane-data {commit_link(REPO_URL, mpd)}"
    return where + at + extra


def _fingerprint(c: dict) -> str:
    if c.get("container") == "zip":
        body = [g["pattern"] for g in c.get("layout") or []]
    else:
        body = [sorted((n, t["sql"]) for n, t in (c.get("tables") or {}).items()),
                sorted((n, i["sql"]) for n, i in (c.get("indexes") or {}).items()),
                sorted((n, r["sql"]) for n, r in (c.get("rtrees") or {}).items())]
    return json.dumps([c["pack"]["schema_version"], c.get("pack_meta_keys"), body])


def groups(doc: dict, live: dict[str, list[str]]) -> list[list[dict]]:
    """The captures of one kind grouped by identical format, live ids first."""
    by_fp: dict[str, list[dict]] = {}
    for pid, c in sorted(doc["packs"].items()):
        by_fp.setdefault(_fingerprint(c), []).append(c)
    return sorted(by_fp.values(), key=lambda cs: (not any(c["pack"]["id"] in live for c in cs),
                                                  cs[0]["pack"]["id"]))


def _edition_line(c: dict, live: dict[str, list[str]]) -> str:
    p = c["pack"]
    listed = p["cycle"] in live.get(p["id"], [])
    note = "" if listed else (" (no longer listed in the catalog)" if p["id"] in live
                              else " (this pack id is not in the catalog)")
    return (f"<li><code>{e(p['id'])}</code> edition <code>{e(p['cycle'])}</code>{note}, "
            f"schema_version {e(p['schema_version'])}; built by {_tool_html(c['builder'])}"
            f"<span class=\"muted\"> &middot; {e(_SOURCES.get(c['source'], c['source']))}</span></li>")


_SOURCES = {"publish": "captured when it was published"}


def _desc(desc: dict, key: str, inline: str | None) -> tuple[str, bool]:
    """An inline DDL comment, else ``kind.table.column``, else the
    every-kind entry ``*.table.column``."""
    if inline:
        return inline, True
    for k in (key, "*." + key.split(".", 1)[-1]):
        if k in desc:
            return desc[k], True
    return "", False


def table_html(kind: str, name: str, t: dict, desc: dict, undocumented: list[str]) -> str:
    rows = []
    for c in t["columns"]:
        text, ok = _desc(desc, f"{kind}.{name}.{c['name']}", c.get("comment"))
        if not ok:
            undocumented.append(f"{name}.{c['name']}")
        default = "" if c.get("default") is None else str(c["default"])
        rows.append(f"<tr><td><code>{e(c['name'])}</code></td><td>{e(c['type'])}</td>"
                    f"<td>{'yes' if c.get('notnull') else ''}</td><td>{e(default)}</td>"
                    f"<td>{e(c['pk']) if c.get('pk') else ''}</td><td>{e(text)}</td></tr>")
    tdesc, _ = _desc(desc, f"{kind}.{name}", None)
    return (f'<h3 id="t-{e(name)}"><code>{e(name)}</code></h3>'
            + (f'<p class="sub">{e(tdesc)}</p>' if tdesc else "")
            + '<div class="scroll"><table class="grid"><tr><th>column</th><th>type</th>'
              '<th>not null</th><th>default</th><th>primary key</th><th>description</th></tr>'
            + "".join(rows) + "</table></div>"
            + f"<details><summary>CREATE statement, verbatim</summary><pre>{e(t['sql'])}</pre>"
              f"</details>")


def sqlite_html(kind: str, c: dict, desc: dict) -> tuple[str, list[str]]:
    undocumented: list[str] = []
    parts = [table_html(kind, n, t, desc, undocumented)
             for n, t in sorted(c.get("tables", {}).items())]
    if c.get("indexes"):
        parts.append('<h3>Indexes</h3><div class="scroll"><table class="grid"><tr><th>index</th>'
                     '<th>table</th><th>columns</th><th>unique</th></tr>' + "".join(
                         f"<tr><td><code>{e(n)}</code></td><td><code>{e(i['table'])}</code></td>"
                         f"<td>{e(', '.join(i['columns']))}</td><td>{'yes' if i['unique'] else ''}"
                         f"</td></tr>" for n, i in sorted(c["indexes"].items()))
                     + "</table></div>")
    if c.get("rtrees"):
        parts.append("<h3>R-trees</h3><p class=\"sub\">SQLite R*Tree virtual tables, the "
                     "spatial index a reader queries by bounding box.</p>" + "".join(
                         f"<p><code>{e(n)}</code>: {e(', '.join(r['columns']))}</p>"
                         f"<pre>{e(r['sql'])}</pre>" for n, r in sorted(c["rtrees"].items())))
    for label, k in (("Other virtual tables", "virtual"), ("Views", "views"),
                     ("Triggers", "triggers")):
        if c.get(k):
            parts.append(f"<h3>{label}</h3>" + "".join(
                f"<p><code>{e(n)}</code></p><pre>{e(v['sql'])}</pre>"
                for n, v in sorted(c[k].items())))
    return "".join(parts), undocumented


def zip_html(kind: str, cs: list[dict], desc: dict) -> tuple[str, list[str]]:
    c = cs[0]
    undocumented: list[str] = []
    rows = []
    for g in c["layout"]:
        text, ok = _desc(desc, f"{kind}.layout.{g['pattern']}", None)
        if not ok:
            undocumented.append(g["pattern"])
        rows.append(f"<tr><td><code>{e(g['pattern'])}</code></td><td><code>{e(g['example'])}"
                    f"</code></td><td>{e(', '.join(g['compression']))}</td><td>{e(text)}</td></tr>")
    counts = "".join(
        f"<tr><td><code>{e(x['pack']['id'])}</code></td>"
        + "".join(f'<td class="num">{g["count"]:,}</td>' for g in x["layout"]) + "</tr>"
        for x in cs)
    heads = "".join(f"<th><code>{e(g['pattern'])}</code></th>" for g in c["layout"])
    return ('<p class="sub">A zip archive; there is no SQL. In each member path a <code>#'
            '</code> stands for one digit.</p>'
            '<div class="scroll"><table class="grid"><tr><th>member path</th><th>example</th>'
            '<th>compression</th><th>description</th></tr>' + "".join(rows) + "</table></div>"
            '<h3>Members per pack</h3><div class="scroll"><table class="grid"><tr><th>pack</th>'
            + heads + "</tr>" + counts + "</table></div>"), undocumented


def kind_page(kind: str, doc: dict, live: dict[str, list[str]], desc: dict, *,
              commit: str) -> tuple[str, list[str]]:
    label = KIND_LABELS.get(kind, kind)
    sections, missing_all = [], []
    gs = groups(doc, live)
    for n, cs in enumerate(gs, 1):
        c = cs[0]
        if c.get("container") == "zip":
            body, missing = zip_html(kind, cs, desc)
        else:
            body, missing = sqlite_html(kind, c, desc)
        missing_all += missing
        head = f"<h2>Format {n} of {len(gs)}</h2>" if len(gs) > 1 else "<h2>Format</h2>"
        keys = ", ".join(f"<code>{e(k)}</code>" for k in c.get("pack_meta_keys") or []) or "none"
        sections.append(
            f"<section>{head}<p>Generated from:</p><ul>"
            + "".join(_edition_line(x, live) for x in cs)
            + f"</ul><p><code>pack_meta</code> keys: {keys}.</p>{body}</section>")
    if missing_all:
        uniq = sorted(set(missing_all))
        sections.append(
            "<section><h2>Not yet described</h2><p class=\"sub\">These have no inline comment "
            "in the builder's CREATE statement and no entry in <code>docs/schema_descriptions"
            f".yaml</code>. Their names and types above are still exact.</p><p>{len(uniq)}: "
            + ", ".join(f"<code>{e(m)}</code>" for m in uniq) + "</p></section>")
    body = (f'<h1>{e(label)} <span class="tag">{e(kind)}</span></h1>'
            f'<p class="sub">The format of the <code>{e(kind)}</code> packs, read from the '
            f'published packs themselves. Machine-readable: <a href="{kind}.json">{kind}.json</a>.'
            f'</p>' + "".join(sections))
    return page(f"{label} pack format", body, root="../", commit=commit), sorted(set(missing_all))


def _fields_table(name: str, cls, desc: dict) -> str:
    rows = "".join(
        f"<tr><td><code>{e(f.name)}</code></td><td>{e(desc.get(f'{name}.{f.name}', ''))}</td></tr>"
        for f in fields(cls))
    return f'<table class="grid"><tr><th>field</th><th>description</th></tr>{rows}</table>'


def index_page(kinds: dict[str, dict], live: dict[str, list[str]], live_kinds: set[str],
               desc: dict, *, commit: str) -> str:
    rows = []
    for kind in sorted(set(kinds) | live_kinds):
        label = KIND_LABELS.get(kind, kind)
        doc = kinds.get(kind)
        if not doc or not doc["packs"]:
            rows.append(f"<tr><td>{e(label)}</td><td><code>{e(kind)}</code></td>"
                        f"<td colspan=\"3\" class=\"muted\">no capture yet; it appears after "
                        f"this kind's next build</td></tr>")
            continue
        caps = sorted(doc["packs"].values(), key=lambda c: c["pack"]["id"])
        eds = ", ".join(f"{e(c['pack']['id'])} {e(c['pack']['cycle'])}" for c in caps)
        svs = ", ".join(sorted({c["pack"]["schema_version"] for c in caps}))
        cont = "zip" if caps[0].get("container") == "zip" else "SQLite"
        rows.append(f'<tr><td><a href="{kind}.html">{e(label)}</a></td><td><code>{e(kind)}'
                    f'</code></td><td>{cont}</td><td>{e(svs)}</td><td>{eds}</td></tr>')
    body = f"""<h1>Pack formats</h1>
<p class="sub">Exactly what a pyEfis device reads from each pack, generated from the published
packs, never written by hand. Machine-readable index: <a href="index.json">index.json</a>.</p>
<section><div class="scroll"><table class="grid"><tr><th>pack kind</th><th>kind</th>
<th>container</th><th>schema_version</th><th>generated from</th></tr>{"".join(rows)}</table>
</div></section>
<section><h2>The contract</h2>
<p>Every pack carries a <code>pack_meta</code> header (a <code>pack_meta(key, value)</code>
table in an SQLite pack, a <code>pack_meta.json</code> member in a zip pack) whose
<code>schema_version</code> names the version of its format. Today it is the version of the
<code>pack_meta</code> header itself for every kind. For <code>highways</code> the build
also sets it from the <code>highway_lines</code> columns: 1 without <code>flags</code> and
<code>ref</code>, 2 with them. Editions packed before that rule declare 1 whatever their
columns, so a reader should check for the columns it needs.</p>
<p>The tables and columns on these pages are public and versioned. Readers should look up
columns by name and ignore tables and columns they do not know: new ones are added without
a <code>schema_version</code> change. Removing or changing one is a format change.</p>
<p>What the build enforces today: each pack's test record (check ROW-003) fails when a table
that the previous edition had is gone and <code>schema_version</code> did not change. A
removed or retyped column is not yet checked by the record; the device acceptance check
(ACC-001) only covers the columns pyEfis itself reads.</p>
<p>{e(LICENSE_TEXT)}</p></section>
<section><h2>The catalog: <code>manifest.json</code></h2>
<p><a href="../manifest.json">manifest.json</a> lists every pack (manifest_version
{MANIFEST_VERSION}). It is signed: <code>manifest.json.minisig</code> is an ed25519
minisign signature checked against the public key in the repository
(<code>keys/minisign.pub</code>), and each pack is checked against the sha256 its entry
carries before it is installed. Each entry in <code>packs</code> has these fields:</p>
<div class="scroll">{_fields_table("manifest", PackEntry, desc)}</div></section>
<section><h2>The header inside every pack: <code>pack_meta</code></h2>
<p>A pack still identifies itself when it is copied away from the catalog. Its
<code>pack_meta</code> fields are a subset of its manifest entry:</p>
<div class="scroll">{_fields_table("pack_meta", packmeta.PackMeta, desc)}</div></section>
<section><p class="notice">{e(NOTICE)}</p></section>"""
    return page("Pack formats", body, root="../", commit=commit)


def live_editions(manifest: dict | None) -> tuple[dict[str, list[str]], set[str]]:
    live: dict[str, list[str]] = {}
    kinds = set()
    for p in (manifest or {}).get("packs") or []:
        live.setdefault(p["id"], []).append(p["cycle"])
        kinds.add(p["kind"])
    return live, kinds


def build(kinds: dict[str, dict], manifest: dict | None, *, commit: str = "",
          descriptions: dict | None = None) -> dict[str, bytes]:
    """Every generated object, ``key -> bytes``, from the kind documents.
    Raises :class:`SiteError` if any page fails the report site's lint."""
    desc = load_descriptions() if descriptions is None else descriptions
    kinds = {k: clean(v) for k, v in kinds.items()}
    live, live_kinds = live_editions(manifest)
    out: dict[str, str] = {}
    index: dict[str, Any] = {}
    for kind, doc in sorted(kinds.items()):
        if not doc["packs"]:
            continue
        html_, missing = kind_page(kind, doc, live, desc, commit=commit)
        out[f"{PREFIX}{kind}.html"] = html_
        index[kind] = {
            "page": f"{PREFIX}{kind}.html", "capture": capture_key(kind),
            "container": next(iter(doc["packs"].values())).get("container"),
            "packs": {pid: {"cycle": c["pack"]["cycle"],
                            "schema_version": c["pack"]["schema_version"],
                            "listed": c["pack"]["cycle"] in live.get(pid, [])}
                      for pid, c in sorted(doc["packs"].items())},
            "undocumented": missing}
    out[f"{PREFIX}index.html"] = index_page(kinds, live, live_kinds, desc, commit=commit)
    problems = [f"{k}: {p}" for k, v in out.items() for p in lint(v)]
    if problems:
        raise SiteError("refusing to publish:\n  " + "\n  ".join(problems))
    blob = {k: v.encode("utf-8") for k, v in out.items()}
    blob[INDEX_KEY] = _dumps({"schema": INDEX_SCHEMA, "kinds": index,
                              "uncaptured": sorted(live_kinds - set(index))})
    return blob


def load_kinds(store) -> dict[str, dict]:
    """Every ``schema/<kind>.json`` capture document in the store."""
    out = {}
    for key in store.list_keys(PREFIX):
        kind = key[len(PREFIX):-len(".json")] if key.endswith(".json") else ""
        if key == INDEX_KEY or not kind or "/" in kind or kind not in packmeta.KINDS:
            continue
        out[kind] = load_kind(store, kind)
    return out


CONTENT_TYPES = {".html": "text/html; charset=utf-8",
                 ".json": "application/json; charset=utf-8"}


def writable(key: str) -> bool:
    """The renderer writes the pages and the index; never a capture."""
    if not key.startswith(PREFIX) or "/" in key[len(PREFIX):]:
        return False
    return key == INDEX_KEY or key.endswith(".html")


def publish(store, objects: dict[str, bytes], *, dry_run: bool = False,
            log=print) -> list[list]:
    """Write each object whose bytes differ from the live one. Returns
    ``[key, bytes, status]`` rows like :func:`packtools.sitedeploy.deploy`."""
    bad = [k for k in objects if not writable(k)]
    if bad:
        raise SiteError(f"not a generated page: {', '.join(sorted(bad))}")
    rows = []
    for key, data in sorted(objects.items()):
        live = store.get_bytes(key)
        status = "new" if live is None else ("unchanged" if live == data else "changed")
        if status != "unchanged" and not dry_run:
            store.put_bytes(key, data, content_type=CONTENT_TYPES[Path(key).suffix])
        rows.append([key, len(data), status])
        log(f"{status:>9}  {key}")
    return rows


EXTRA_CSS = """
.muted{color:var(--muted)}details{margin:6px 0 14px}summary{cursor:pointer;color:var(--muted);
font-size:13px}td code{white-space:nowrap}
"""
