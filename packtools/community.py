# SPDX-License-Identifier: Apache-2.0
"""The GitHub community loop (testing-visibility item TV8).

Three things the build record cannot carry, because it is immutable and
signed (docs/community_loop.md):

    packtool status-overlay --bucket makerplane-data [--ensure-labels]
        the nightly, unsigned ``reports/<id>/<cycle>/status.json`` beside
        every record: the edition's state against the signed live catalog,
        the makerplane-data issues labelled ``pack:<id>``, and any dated
        Auspex addenda QA has uploaded, each with its sha256.

    packtool release-assets --records work/reports --out work/release
        after a publish: the files for one GitHub Release per publish event,
        holding each published edition's ``report.json`` (the exact bytes the
        catalog's ``report_sha256`` binds) and its rendered ``report.html``.

    python -m packtools.community label-issue
        on an issue-form submission: add the ``pack:<id>`` label for each
        pack the form names, if that label already exists. Stdlib only, so
        the workflow needs no install.

Nothing here writes a record, a page, the manifest or an addendum. The
overlay writer refuses any key but a ``status.json``.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PREFIX = "reports/"
STATUS_SCHEMA = "pack-status/1"
REPO = "billmallard/makerplane-data"
REPO_URL = f"https://github.com/{REPO}"
SITE = "https://navdata.aerocommons.org"
API = "https://api.github.com"
LABEL_PREFIX = "pack:"
LABEL_COLOR = "1D76DB"
# The labels .github/ISSUE_TEMPLATE/*.yml apply.
FORM_LABELS = {"data-problem": ("D93F0B", "Filed with the Data problem form"),
               "tested-on-hardware": ("0E8A16", "Filed with the Tested on my hardware form")}
PUB_KEY = Path(__file__).resolve().parents[1] / "keys" / "minisign.pub"

STATES = ("current", "pending", "superseded", "expired", "retracted")

# The form field the "Data problem" and "Tested on my hardware" forms both
# use for the pack id(s), and the one for the edition. Their ``label:`` in
# .github/ISSUE_TEMPLATE/*.yml; GitHub renders each as a "### <label>" heading.
PACK_FIELD = "Pack id"
CYCLE_FIELD = "Edition (cycle)"

_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_ADDENDUM = re.compile(r"^auspex-(\d{4}-\d{2}-\d{2})[\w.-]*\.json$")

OVERLAY_NOTE = ("Not covered by the catalog signature. The build record (report.json) "
                "is fixed at publish; this file is rewritten nightly from the signed "
                "catalog, the makerplane-data issue tracker and the files beside the record.")


class CommunityError(RuntimeError):
    """Something that must stop the job rather than publish a wrong overlay."""


# --- GitHub REST, stdlib only -----------------------------------------------

def _gh(method: str, path: str, *, token: str | None = None, body: dict | None = None):
    """``(status, parsed JSON or None, Link header)``. HTTP errors come back as
    a status, not an exception, so callers decide what a 404 means."""
    url = path if path.startswith("https://") else f"{API}{path}"
    req = urllib.request.Request(url, method=method,
                                 data=None if body is None else json.dumps(body).encode())
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "makerplane-data-community-loop")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None), r.headers.get("Link", "")
    except urllib.error.HTTPError as e:
        return e.code, None, ""


def _next_link(link: str) -> str | None:
    m = re.search(r'<([^>]+)>;\s*rel="next"', link or "")
    return m.group(1) if m else None


def fetch_pack_issues(repo: str = REPO, token: str | None = None) -> list[dict]:
    """Every issue (open and closed, never a pull request) carrying at least
    one ``pack:`` label. Any failed page raises :class:`CommunityError`: an
    overlay written from a partial list would claim fewer known issues than
    there are."""
    out, url = [], f"/repos/{repo}/issues?state=all&per_page=100"
    while url:
        status, page, link = _gh("GET", url, token=token)
        if status != 200 or not isinstance(page, list):
            raise CommunityError(f"GitHub issues {url}: HTTP {status}; no overlay written")
        out += [i for i in page if "pull_request" not in i
                and any(lb.get("name", "").startswith(LABEL_PREFIX) for lb in i.get("labels") or [])]
        url = _next_link(link)
    return out


def ensure_labels(ids, repo: str = REPO, token: str | None = None, *,
                  dry_run: bool = False, log=print) -> list[str]:
    """Create ``pack:<id>`` for each id, and the two labels the issue forms
    apply (:data:`FORM_LABELS`; a form silently skips a label that does not
    exist), where missing. Returns the labels it created (or would create).
    Never edits an existing label."""
    want = dict(FORM_LABELS)
    want.update({LABEL_PREFIX + pid: (LABEL_COLOR, f"Known issue in the {pid} data pack")
                 for pid in set(ids)})
    made = []
    for name, (color, description) in sorted(want.items()):
        status, _, _ = _gh("GET", f"/repos/{repo}/labels/{urllib.parse.quote(name, safe='')}",
                           token=token)
        if status == 200:
            continue
        if status != 404:
            raise CommunityError(f"label {name}: HTTP {status}")
        if not dry_run:
            status, _, _ = _gh("POST", f"/repos/{repo}/labels", token=token, body={
                "name": name, "color": color, "description": description})
            if status != 201:
                raise CommunityError(f"creating label {name}: HTTP {status}")
        made.append(name)
        log(f"{'would create' if dry_run else 'created'} label {name}")
    return made


# --- issue-form bodies ------------------------------------------------------

def form_field(body: str | None, label: str) -> str | None:
    """The answer under ``### <label>`` in an issue-form body, or None when
    the field is absent or left blank (GitHub writes ``_No response_``)."""
    m = re.search(r"^###\s+" + re.escape(label) + r"\s*$(.*?)(?=^###\s|\Z)",
                  body or "", flags=re.M | re.S)
    if not m:
        return None
    val = m.group(1).strip()
    return None if not val or val == "_No response_" else val


def pack_ids_in(body: str | None) -> list[str]:
    """The pack ids a form names in its pack field: comma, space or newline
    separated, lower-cased, anything not shaped like an id dropped."""
    val = form_field(body, PACK_FIELD) or ""
    return sorted({t for t in re.split(r"[\s,]+", val.lower()) if _ID.match(t)})


def label_issue(body: str | None, number: int, repo: str = REPO, token: str | None = None,
                *, log=print) -> list[str]:
    """Add ``pack:<id>`` to issue ``number`` for each id its form names whose
    label exists. A label is only ever created by :func:`ensure_labels` from
    the signed catalog, so a typo in a form cannot mint one."""
    added = []
    for pid in pack_ids_in(body):
        name = LABEL_PREFIX + pid
        status, _, _ = _gh("GET", f"/repos/{repo}/labels/{urllib.parse.quote(name, safe='')}",
                           token=token)
        if status != 200:
            log(f"no label {name}; left for a maintainer")
            continue
        added.append(name)
    if added:
        status, _, _ = _gh("POST", f"/repos/{repo}/issues/{number}/labels", token=token,
                           body={"labels": added})
        if status != 200:
            raise CommunityError(f"labelling #{number}: HTTP {status}")
        log(f"#{number}: added {', '.join(added)}")
    return added


# --- the signed live catalog --------------------------------------------------

def load_live_manifest(store, pub_text: str | None = None) -> dict:
    """The live ``manifest.json``, checked against the committed public key
    before anything is decided from it."""
    from . import signing
    raw, sig = store.get_bytes("manifest.json"), store.get_bytes("manifest.json.minisig")
    if raw is None or sig is None:
        raise CommunityError("no signed manifest.json in the store")
    pub_text = pub_text or PUB_KEY.read_text(encoding="utf-8")
    try:
        signing.verify(raw, sig.decode("ascii"), pub_text)
    except Exception as e:
        raise CommunityError(f"manifest.json does not verify: {e}") from None
    return json.loads(raw)


# --- the status overlay -------------------------------------------------------

def status_key(pack_id: str, cycle: str) -> str:
    return f"{PREFIX}{pack_id}/{cycle}/status.json"


def edition_state(pack_id: str, cycle: str, built_at: str, manifest: dict,
                  built_at_of: dict, today: _dt.date) -> tuple[str, str]:
    """``(state, reason)`` for one edition that has a record.

    A listed edition is judged by its currency window and by
    ``Manifest.select`` (the updater's own choice). An unlisted one carries
    no dates, so it is ``superseded`` when the catalog lists a newer edition
    of the same id, and ``retracted`` otherwise: removed from the catalog,
    or never listed. "Newer" is a higher cycle when both cycles are
    canonical (the edition ``prune_old_cycles`` dropped), or a later build
    time. A hyphen-suffixed test cycle is compared by build time only, and
    editions listed with no record were published before records existed,
    so they are built before any record."""
    from .manifest import Manifest
    m = Manifest.from_obj(manifest, lenient=True)
    entry = next((p for p in m.for_id(pack_id) if p.cycle == cycle), None)
    if entry is None:
        newer = sorted(p.cycle for p in m.for_id(pack_id)
                       if built_at_of.get(p.cycle, "") > built_at
                       or ("-" not in cycle and "-" not in p.cycle and p.cycle > cycle))
        if newer:
            return "superseded", (f"not in the live catalog; it lists the newer "
                                  f"{', '.join(newer)}")
        return "retracted", ("not in the live catalog, and it lists no newer edition of "
                             "this pack: removed from the catalog, or never listed")
    if entry.expires and today >= _dt.date.fromisoformat(entry.expires):
        return "expired", f"in the live catalog; its window ended {entry.expires}"
    if entry.effective and today < _dt.date.fromisoformat(entry.effective):
        return "pending", f"in the live catalog; effective from {entry.effective}"
    chosen = m.select(pack_id, today)
    if chosen is not None and chosen.cycle == cycle:
        return "current", "in the live catalog; the edition the updater installs today"
    return "superseded", (f"in the live catalog, but the updater installs "
                          f"{chosen.cycle if chosen else 'another edition'} today")


def _issue_row(i: dict) -> dict:
    from .reportsite import scrub
    return {"number": i["number"], "title": scrub(i.get("title") or ""),
            "url": i.get("html_url") or f"{REPO_URL}/issues/{i['number']}",
            "state": i.get("state"), "opened": (i.get("created_at") or "")[:10],
            "closed": (i.get("closed_at") or "")[:10] or None,
            "reported_cycle": form_field(i.get("body"), CYCLE_FIELD)}


def addenda(store, pack_id: str, cycle: str) -> list[dict]:
    """Dated Auspex addenda beside the record, by name and sha256. Their
    contents are QA's; this lists them and never reads into them."""
    out = []
    base = f"{PREFIX}{pack_id}/{cycle}/"
    for key in sorted(store.list_keys(base)):
        name = key[len(base):]
        m = _ADDENDUM.match(name)
        if not m:
            continue
        raw = store.get_bytes(key)
        out.append({"name": name, "date": m.group(1), "url": f"{SITE}/{key}",
                    "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
    return out


def build_status(store, records: list[dict], manifest: dict, issues: list[dict],
                 today: _dt.date) -> dict[str, bytes]:
    """``status.json`` bytes for every edition in ``records`` (as from
    :func:`reportsite.load_records`)."""
    built_at_of: dict[str, dict] = {}
    for ed in records:
        built_at_of.setdefault(ed["id"], {})[ed["cycle"]] = \
            (ed["rec"].get("built") or {}).get("at") or ""
    listed = {(p.get("id"), str(p.get("cycle"))) for p in manifest.get("packs") or []}
    by_label: dict[str, list[dict]] = {}
    for i in issues:
        for lb in i.get("labels") or []:
            name = lb.get("name", "")
            if name.startswith(LABEL_PREFIX):
                by_label.setdefault(name[len(LABEL_PREFIX):], []).append(_issue_row(i))
    out = {}
    for ed in records:
        pid, cyc, rec = ed["id"], ed["cycle"], ed["rec"]
        state, reason = edition_state(pid, cyc, built_at_of[pid][cyc], manifest,
                                      built_at_of[pid], today)
        known = sorted(by_label.get(pid, []), key=lambda r: r["number"])
        obj = {"schema": STATUS_SCHEMA,
               "pack": {"id": pid, "cycle": cyc, "sha256": rec["pack"]["sha256"]},
               "record": {"url": f"{SITE}/{ed['key']}", "sha256": ed["sha256"]},
               "as_of": today.isoformat(),
               "signed": False,
               "listed": (pid, cyc) in listed,
               "state": state, "state_reason": reason,
               "known_issues": known,
               "issues_url": f"{REPO_URL}/issues?q=" + urllib.parse.quote(
                   f'label:"{LABEL_PREFIX}{pid}"'),
               "auspex_addenda": addenda(store, pid, cyc),
               "note": OVERLAY_NOTE}
        out[status_key(pid, cyc)] = (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode()
    return out


def _is_status_key(key: str) -> bool:
    parts = key[len(PREFIX):].split("/") if key.startswith(PREFIX) else []
    return len(parts) == 3 and parts[2] == "status.json" and parts[0] != "rejected"


def publish_status(store, objects: dict[str, bytes], *, dry_run: bool = False,
                   log=print) -> list[list]:
    """Write each overlay whose bytes changed. Refuses anything but a
    ``reports/<id>/<cycle>/status.json``."""
    bad = [k for k in objects if not _is_status_key(k)]
    if bad:
        raise CommunityError(f"not a status overlay: {', '.join(sorted(bad))}")
    rows = []
    for key, data in sorted(objects.items()):
        live = store.get_bytes(key)
        status = "new" if live is None else ("unchanged" if live == data else "changed")
        if status != "unchanged" and not dry_run:
            store.put_bytes(key, data, content_type="application/json; charset=utf-8")
        rows.append([key, len(data), status])
        log(f"{status:>9}  {key}")
    return rows


# --- one GitHub Release per publish event ---------------------------------------

def release_assets(record_dir: str | Path, manifest: dict, out: str | Path, *,
                   today: _dt.date, run_id: str = "", run_attempt: str = "",
                   run_url: str = "", commit: str = "", log=print) -> list[dict]:
    """Write a Release's files to ``out`` for every edition the publish gate
    accepted in this job (``<record_dir>/<id>-<cycle>.gate.json``) **and**
    the signed live catalog now lists with a ``report_sha256`` equal to the
    record's bytes. An accepted edition that did not reach the catalog is
    left out and named. Returns the editions released; writes nothing and
    returns ``[]`` when there are none, so a no-op nightly cuts no release.

        out/assets/<id>-<cycle>.report.json   the bound record, byte for byte
        out/assets/<id>-<cycle>.report.html   rendered from it
        out/notes.md, out/tag.txt, out/title.txt
    """
    from . import reportsite as rs
    from .report import validate_report
    entries = rs.manifest_entries(manifest)
    done = []
    for verdict in sorted(Path(record_dir).glob("*.gate.json")):
        if not json.loads(verdict.read_text(encoding="utf-8")).get("accepted"):
            continue
        rec_path = verdict.with_name(verdict.name[: -len(".gate.json")] + ".report.json")
        raw = rec_path.read_bytes()
        rec = json.loads(raw)
        p = rec["pack"]
        entry = entries.get((p["id"], str(p["cycle"])))
        sha = hashlib.sha256(raw).hexdigest()
        if entry is None or entry.get("report_sha256") != sha:
            log(f"left out {p['id']} {p['cycle']}: accepted by the gate but "
                f"{'not in the live catalog' if entry is None else 'its report_sha256 differs'}")
            continue
        errs = validate_report(rec)
        if errs:
            raise CommunityError(f"{rec_path.name}: " + "; ".join(errs))
        done.append({"id": p["id"], "cycle": str(p["cycle"]), "raw": raw, "rec": rec,
                     "sha256": sha, "entry": entry})
    if not done:
        log("no edition published in this job; no release")
        return []

    out = Path(out)
    assets = out / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    rows = []
    for d in done:
        stem = f"{d['id']}-{d['cycle']}"
        page_base = f"{SITE}/{PREFIX}{d['id']}/{d['cycle']}/"
        ed = {"id": d["id"], "cycle": d["cycle"], "key": rs.record_key(d["id"], d["cycle"]),
              "sha256": d["sha256"], "rec": rs.clean(d["rec"])}
        page = rs.report_page(ed, rs.edition_row(ed, d["entry"]), d["entry"], None,
                              commit=commit)
        # A downloaded copy resolves its links (history, raw JSON, the status
        # overlay) against the live page rather than against nothing.
        page = page.replace("<head>", f'<head><base href="{page_base}">', 1)
        problems = rs.lint(page)
        if problems:
            raise CommunityError(f"{stem}.report.html: " + "; ".join(problems))
        (assets / f"{stem}.report.json").write_bytes(d["raw"])
        (assets / f"{stem}.report.html").write_text(page, encoding="utf-8")
        t = rs.tally(ed["rec"])
        rows.append([f"`{d['id']}`", d["cycle"], rs.pack_label(ed["rec"]), ed["rec"]["origin"],
                     rs.summary_line(t), f"`{d['rec']['pack']['sha256'][:16]}`",
                     f"[page]({page_base}report.html)"])
        log(f"release asset {stem}.report.json (sha256 {d['sha256'][:12]}) + .report.html")

    from .summary import table
    from .manifest import NOTICE
    ids = ", ".join(f"{d['id']} {d['cycle']}" for d in done)
    title = f"Data publish {today.isoformat()}: {ids}"
    tag = f"data/{today.isoformat()}/{run_id or 'local'}" + (f".{run_attempt}" if run_attempt else "")
    built = (f"Built by [this run]({run_url})" if run_url else "Built") + (
        f" from makerplane-data `{commit[:12]}`." if commit else ".")
    notes = "\n".join([
        f"{len(done)} pack edition(s) added to the signed catalog at "
        f"{SITE}/manifest.json.", "",
        table(["pack", "edition", "data", "origin", "checks", "pack sha256", "test record"], rows),
        "Attached for each edition: `<id>-<cycle>.report.json`, the test record whose "
        "sha256 is the `report_sha256` the signed catalog carries, and "
        "`<id>-<cycle>.report.html`, the page rendered from it. Anything learned after "
        f"publish is in the edition's `status.json` beside its page, and in issues "
        f"labelled `pack:<id>`.", "",
        built, "", NOTICE, ""])
    problems = rs.lint(notes)
    if problems:
        raise CommunityError("release notes: " + "; ".join(problems))
    (out / "notes.md").write_text(notes, encoding="utf-8")
    (out / "tag.txt").write_text(tag + "\n", encoding="utf-8")
    (out / "title.txt").write_text(title + "\n", encoding="utf-8")
    log(f"release {tag}: {title}")
    return done


# --- entry point for the issue-labelling workflow (stdlib only) ------------------

def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] != ["label-issue"]:
        print("usage: python -m packtools.community label-issue  "
              "(reads ISSUE_BODY, ISSUE_NUMBER, GITHUB_REPOSITORY, GH_TOKEN)", file=sys.stderr)
        return 2
    label_issue(os.environ.get("ISSUE_BODY", ""), int(os.environ["ISSUE_NUMBER"]),
                os.environ.get("GITHUB_REPOSITORY", REPO), os.environ.get("GH_TOKEN"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
