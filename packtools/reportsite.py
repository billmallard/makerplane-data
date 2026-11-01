# SPDX-License-Identifier: Apache-2.0
"""Report pages on navdata.aerocommons.org (testing-visibility item TV5).

    packtool report-site --bucket makerplane-data        render + upload changed pages
    packtool report-site --from DIR --out PREVIEW        render a local preview

Every page is rendered from ``pack-report/1`` records (docs/pack_report.md)
found under ``reports/<id>/<cycle>/report.json`` in the bucket, which is
where the publish paths upload them (TV3). Nothing here writes a record:
the generator owns only the ``.html`` pages beside them and
``reports/index.json``, the summary the catalog on ``index.html`` reads.

    reports/index.json                     catalog summary line per edition
    reports/index.html                     every pack with a record
    reports/check.html                     check it yourself (plan 5.5)
    reports/<id>/index.html                history of one pack (plan 5.3)
    reports/<id>/<cycle>/report.html       one edition (plan 5.2); its "known
                                           since" section reads status.json
                                           (packtools/community.py, TV8)

Ported from efis-voice-lab's ``report.py``/``site.py``: one self-contained
page per record, one table of per-edition numbers (:func:`edition_row`)
feeding every tally, delta and chart so they cannot disagree, a footer
naming the commit, and a hygiene pass over every record before anything is
rendered. Pages carry no timestamp of their own, so an unchanged record
renders to unchanged bytes and is not re-uploaded.

Two gates fail the build (:func:`lint`): a banned word (plan 4.7) anywhere
outside the standing text itself, and a local path, IP address or internal
hostname that survived the hygiene pass.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any

from .manifest import NOTICE
from .report import publish_blockers, validate_report

PREFIX = "reports/"
INDEX_KEY = "reports/index.json"
INDEX_SCHEMA = "report-index/1"
REPO = "billmallard/makerplane-data"
REPO_URL = f"https://github.com/{REPO}"
BUILDER_REPOS = {"pyEfis": "https://github.com/billmallard/pyEfis"}

# Plan section 4.7 standing text, verbatim (D3). Rendered as the boxed
# p.standing alone; the section heading was dropped on 2026-11-01 (AER-4532).
STANDING_TEXT = (
    "These are the checks we ran on this pack, what each one measured, and what "
    "each one cannot catch. It is evidence, not a warranty and not a certification. "
    "No authority has reviewed or approved this data or these checks. Passing them "
    "does not mean the data is correct, complete or current. Results marked as "
    "coming from Auspex come from a proprietary AI test engine whose method is not "
    "published; each of its findings is stated so you can check it yourself. This "
    "data is for Experimental / Amateur-Built use. This data is provided as-is "
    "with no warranty and any use of the data is at your own risk. Verify "
    "against official publications.")

# Never used about a pack or a check result (plan 4.7). The standing text and
# the notice use two of them in the negative; lint() exempts those exact
# strings and nothing else.
BANNED_WORDS = ("verified", "certified", "approved", "safe", "compliant", "airworthy")
_BANNED = re.compile(r"\b(" + "|".join(BANNED_WORDS) + r")\b", re.I)

# Hosts a rendered page may link to. Any other URL in a record is shown as
# text, so a record can never carry a link onto an internal board.
LINK_HOSTS = ("github.com", "navdata.aerocommons.org", "faa.gov", "ourairports.com",
              "openstreetmap.org", "geofabrik.de", "naturalearthdata.com",
              "copernicus.eu", "opentopography.org")

KIND_LABELS = {"navdata": "Airports & runways", "airports": "Airports (regional)",
               "navaids": "Navaids & airways", "obstacles": "Obstacles",
               "procedures": "Procedures & airways", "cifp": "Procedures & waypoints",
               "terrain": "Terrain", "water": "Water", "highways": "Roads & highways",
               "rivers": "Rivers"}
STATUS_ORDER = ("fail", "warn", "not-run", "pass")
STATUS_LABEL = {"pass": "pass", "warn": "warn", "fail": "fail", "not-run": "not run"}


class SiteError(RuntimeError):
    """A record or a rendered page that must not be published."""


# --- hygiene ----------------------------------------------------------------

_ROOTS = ("home", "Users", "tmp", "paperclip", "root", "mnt", "media", "var", "srv",
          "opt", "run", "workspace", "workspaces", "github", "runner", "private",
          "Volumes", "builds", "data")
_PATH = re.compile(r"(?<![\w.:/~-])(?:/(?:" + "|".join(_ROOTS) + r")/[^\s\"'<>()\]]*"
                   r"|[A-Za-z]:\\[^\s\"'<>()\]]*)")
_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
_INTERNAL = re.compile(r"\b(?:[\w.-]+@)?(?:localhost|[\w-]+(?:\.[\w-]+)*\."
                       r"(?:local|lan|internal|localdomain|intranet|corp|home\.arpa))\b", re.I)
_URL = re.compile(r"https?://[^\s\"'<>()\]]+")


def _public_host(host: str) -> bool:
    host = host.lower().split(":")[0]
    return ("." in host and re.search(r"\.[a-z]{2,}$", host) is not None
            and not _IPV4.fullmatch(host) and not _INTERNAL.fullmatch(host)
            and "paperclip" not in host)


def _scrub_url(m: re.Match) -> str:
    host = m.group(0).split("/")[2]
    return m.group(0) if _public_host(host) else "<internal link removed>"


def scrub(s: str) -> str:
    """One string with local paths, IP addresses, internal hostnames and
    links to non-public hosts replaced by placeholders."""
    s = _URL.sub(_scrub_url, s)
    s = _PATH.sub("<local path>", s)
    s = _IPV4.sub("<host>", s)
    return _INTERNAL.sub("<host>", s)


def clean(obj: Any) -> Any:
    """``obj`` with :func:`scrub` applied to every string in it, keys included."""
    if isinstance(obj, str):
        return scrub(obj)
    if isinstance(obj, dict):
        return {clean(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    return obj


def lint(page: str) -> list[str]:
    """Why ``page`` must not be published, empty if nothing.

    Banned words are looked for in everything but ``<style>`` and the two
    exempt strings; hygiene in everything but ``<style>``."""
    body = re.sub(r"<style\b.*?</style>", "", page, flags=re.S | re.I)
    problems = []
    words = body
    for exempt in (STANDING_TEXT, NOTICE):
        words = words.replace(html.escape(exempt), "").replace(exempt, "")
    for w in sorted({m.group(1).lower() for m in _BANNED.finditer(words)}):
        problems.append(f"banned word {w!r}")
    for rx, what in ((_PATH, "local path"), (_IPV4, "IP address"),
                     (_INTERNAL, "internal hostname")):
        if m := rx.search(body):
            problems.append(f"{what} {m.group(0)!r}")
    return problems


# --- loading records --------------------------------------------------------

def record_key(pack_id: str, cycle: str) -> str:
    return f"{PREFIX}{pack_id}/{cycle}/report.json"


def load_records(store) -> list[dict]:
    """Every ``reports/<id>/<cycle>/report.json`` in ``store``: one dict per
    edition with the cleaned record, its raw bytes' sha256, id and cycle.

    The path is the authority for id and cycle; a record whose ``pack``
    disagrees with its path, or that fails :func:`report.validate_report`,
    raises :class:`SiteError` rather than rendering a page about the wrong
    bytes."""
    out = []
    for key in store.list_keys(PREFIX):
        parts = key[len(PREFIX):].split("/")
        if len(parts) != 3 or parts[2] != "report.json" or parts[0] == "rejected":
            continue
        pack_id, cycle = parts[0], parts[1]
        raw = store.get_bytes(key)
        try:
            rec = json.loads(raw)
        except ValueError as e:
            raise SiteError(f"{key}: not JSON ({e})") from None
        errs = validate_report(rec)
        if errs:
            raise SiteError(f"{key}: " + "; ".join(errs))
        if (rec["pack"].get("id"), rec["pack"].get("cycle")) != (pack_id, cycle):
            raise SiteError(f"{key}: record is about {rec['pack'].get('id')} "
                            f"{rec['pack'].get('cycle')}, not {pack_id} {cycle}")
        out.append({"id": pack_id, "cycle": cycle, "key": key,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "rec": clean(rec)})
    return out


def manifest_entries(manifest: dict | None) -> dict[tuple[str, str], dict]:
    return {(p.get("id"), str(p.get("cycle"))): p
            for p in (manifest or {}).get("packs") or []}


# --- the numbers: one row per edition feeds every tally, delta and chart ----

def tally(rec: dict) -> dict[str, int]:
    t = {s: 0 for s in STATUS_LABEL}
    for c in rec["checks"]:
        t[c["status"]] += 1
    return t


def edition_row(ed: dict, entry: dict | None) -> dict:
    rec = ed["rec"]
    return {"id": ed["id"], "cycle": ed["cycle"], "record": rec["record"],
            "kind": rec["pack"]["kind"], "origin": rec["origin"],
            "tally": tally(rec), "tables": {t: v.get("rows") for t, v in rec["tables"].items()},
            "statuses": {c["id"]: c["status"] for c in rec["checks"]},
            "built_at": (rec.get("built") or {}).get("at") or "",
            "effective": (entry or {}).get("effective") or "",
            "expires": (entry or {}).get("expires") or "",
            "listed": entry is not None,
            "blockers": publish_blockers(rec)}


def _order(row: dict) -> tuple:
    return (row["effective"], row["built_at"], row["cycle"])


# --- formatting -------------------------------------------------------------

def e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def num(x: Any) -> str:
    if x is None:
        return "&mdash;"
    if isinstance(x, bool):
        return e(x)
    if isinstance(x, int):
        return f"{x:,}"
    if isinstance(x, float):
        return f"{x:,.4g}"
    return e(x)


def link(url: str | None, text: str) -> str:
    """``text`` linked to ``url`` if it is a public https URL on an allowed
    host, otherwise ``text`` alone. ``text`` is escaped here."""
    if url and url.startswith("https://"):
        host = url.split("/")[2].lower()
        if any(host == h or host.endswith("." + h) for h in LINK_HOSTS):
            return f'<a href="{e(url)}">{e(text)}</a>'
    return e(text)


def commit_link(repo_url: str, sha: str | None) -> str:
    if not sha:
        return "&mdash;"
    return link(f"{repo_url}/commit/{sha}", sha[:12])


def summary_line(t: dict[str, int]) -> str:
    return (f"{t['pass']} pass / {t['warn']} warn / {t['fail']} fail / "
            f"{t['not-run']} not run")


def pack_label(rec: dict) -> str:
    return KIND_LABELS.get(rec["pack"]["kind"], rec["pack"]["kind"])


def pill(status: str) -> str:
    return f'<span class="st st-{e(status)}">{e(STATUS_LABEL.get(status, status))}</span>'


def delta(rows: int | None, prev: int | None) -> str:
    if rows is None or prev is None:
        return "&mdash;"
    d = rows - prev
    if d == 0:
        return "no change"
    pct = f" ({d / prev * 100:+.1f} %)" if prev else ""
    return f"{d:+,}{pct}"


def generator_commit() -> str:
    sha = os.environ.get("GITHUB_SHA", "")
    if not sha:
        try:
            sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                 cwd=Path(__file__).resolve().parent, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            sha = ""
    return sha


# --- page shell ------------------------------------------------------------

def page(title: str, body: str, *, root: str, commit: str) -> str:
    footer = (f"Rendered from the published <code>pack-report/1</code> records by "
              f"makerplane-data {commit_link(REPO_URL, commit)}. "
              if commit else "Rendered from the published <code>pack-report/1</code> records. ")
    return f"""<!DOCTYPE html>
<!-- SPDX-License-Identifier: Apache-2.0 -->
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} &middot; Navigation Data &middot; AeroCommons</title>
<link rel="icon" href="{root}assets/aerocommons-logo.svg">
<style>{CSS}</style>
<script defer src="{root}assets/analytics.js"></script>
</head><body>
<header class="site"><div class="wrap brandbar">
<a class="ac" href="{root}index.html"><img src="{root}assets/aerocommons-logo.svg" alt="">
<span class="name">AeroCommons</span></a><span class="crumb">Navigation Data &middot;
<a href="{root}reports/index.html">test reports</a> &middot;
<a href="{root}reports/check.html">check it yourself</a></span></div></header>
<main class="wrap">
{body}
</main>
<footer class="site"><div class="wrap">{footer}
<a href="{root}manifest.json">catalog</a> &middot; <a href="{REPO_URL}">source</a></div></footer>
</body></html>
"""


def standing_block() -> str:
    return f'<section><p class="standing">{e(STANDING_TEXT)}</p></section>'


# --- one edition ------------------------------------------------------------

def check_rows(rec: dict) -> str:
    rank = {s: i for i, s in enumerate(STATUS_ORDER)}
    rows = []
    for c in sorted(rec["checks"], key=lambda c: rank.get(c["status"], 9)):
        extra = []
        if c.get("note"):
            extra.append(f"<b>{'Build note' if c['status'] == 'warn' else 'Note'}:</b> "
                         f"{e(c['note'])}")
        elif c["status"] == "warn":
            extra.append("<b>No build note recorded.</b>")
        if c.get("since"):
            extra.append(f"since {e(c['since'])}")
        rows.append(
            f'<tr data-status="{e(c["status"])}"><td>{pill(c["status"])}</td>'
            f'<td class="mono">{e(c["id"])}{"" if c.get("blocking") else "<br><small>non-blocking</small>"}</td>'
            f'<td>{e(c["title"])}</td><td>{num(c.get("measured"))}</td>'
            f'<td>{num(c.get("expected"))}</td><td class="cc">{e(c["cannot_catch"])}</td>'
            f'<td class="cc">{"<br>".join(extra)}</td></tr>')
    return "".join(rows)


def callouts(rec: dict) -> str:
    items = []
    for c in rec["checks"]:
        if c["status"] in ("fail", "warn", "not-run"):
            why = c.get("note") or ("no build note recorded" if c["status"] == "warn" else "")
            said = [num(c.get("measured"))] if c["status"] != "not-run" else []
            said += [e(why)] if why else []
            items.append(f'<li>{pill(c["status"])} <span class="mono">{e(c["id"])}</span> '
                         f'{e(c["title"])}{": " + " &mdash; ".join(said) if said else ""}</li>')
    return f'<ul class="callouts">{"".join(items)}</ul>' if items else ""


def tables_html(rec: dict, prev_cycle: str | None) -> str:
    if not rec["tables"]:
        return ""
    rows = "".join(
        f"<tr><td class=mono>{e(t)}</td><td class=num>{num(v.get('rows'))}</td>"
        f"<td class=num>{num(v.get('prev'))}</td><td class=num>{delta(v.get('rows'), v.get('prev'))}</td></tr>"
        for t, v in sorted(rec["tables"].items()))
    against = (f"against {e(prev_cycle)}" if prev_cycle
               else "no previous edition was compared")
    return (f"<section><h2>Row counts</h2><p class=sub>Rows per table, {against}.</p>"
            f"<table class=grid><tr><th>Table</th><th>Rows</th><th>Previous</th>"
            f"<th>Change</th></tr>{rows}</table></section>")


def sources_html(rec: dict) -> str:
    if not rec["sources"]:
        return ("<p>No upstream sources are recorded for this edition"
                " (see <span class=mono>PACK-004</span>).</p>")
    rows = "".join(
        f"<tr><td>{link(s.get('url'), s.get('name') or '')}</td><td>{e(s.get('edition'))}</td>"
        f"<td>{e(s.get('retrieved'))}</td><td class=mono>{e((s.get('sha256') or '')[:16])}</td></tr>"
        for s in rec["sources"])
    return (f"<table class=grid><tr><th>Source</th><th>Edition</th><th>Retrieved</th>"
            f"<th>sha256</th></tr>{rows}</table>")


def auspex_html(rec: dict) -> str:
    a = rec.get("auspex")
    if not a:
        return ("<section><h2>Black-box check (Auspex)</h2><p>Not run for this edition.</p>"
                "</section>")
    cov = a.get("coverage") or {}
    findings = "".join(
        f"<li><span class=mono>{e(json.dumps(f.get('locator'), sort_keys=True))}</span><br>"
        f"{e(f.get('statement'))} Expected: {e(f.get('expected'))}. Actual: {e(f.get('actual'))}. "
        f"{e(f.get('issue'))}</li>" for f in a.get("findings") or [])
    when = ("in the signed build record (run before publish)" if a.get("when") == "pre-publish"
            else "a dated addendum (run after publish)")
    return (f"<section><h2>Black-box check (Auspex)</h2>"
            f"<p><b>{e(a.get('engine'))}</b>. This result cannot be re-run outside; "
            f"each finding below is stated so you can check it yourself.</p>"
            f"<p>Check <span class=mono>{e(a.get('check'))}</span>, {e(when)}, "
            f"run {e(a.get('run_at'))}: verdict <b>{e(a.get('verdict'))}</b>. "
            f"Coverage on this pack: {num(cov.get('checked'))} checked, "
            f"{num(cov.get('covered'))} covered, {num(cov.get('skipped'))} skipped, "
            f"{num(cov.get('unreadable'))} unreadable.</p>"
            f"{'<ol>' + findings + '</ol>' if findings else '<p>No findings.</p>'}"
            f"<p class=sub>{e(a.get('what_a_pass_means'))}</p></section>")


def known_html(pack_id: str, cycle: str) -> str:
    """The "known since" section (plan 4.5, TV8). Everything else on the page
    is the build record; this is filled in the browser from the unsigned
    nightly ``status.json`` beside the page, so the page bytes do not change
    when an issue does, and issue titles (the community's words, not ours)
    never pass through :func:`lint`. Without the overlay it still links the
    label query and the form."""
    q = urllib.parse.urlencode
    issues = f"{REPO_URL}/issues?" + q({"q": f'label:"pack:{pack_id}"'})
    form = f"{REPO_URL}/issues/new?" + q({"template": "data_problem.yml",
                                          "pack": pack_id, "cycle": cycle})
    return f"""<section id="known"><h2>Known since this edition was built</h2>
<p class="sub">Everything above is the build record: fixed at publish and covered by the
catalog signature. This section is not. It is read from <a href="status.json">status.json</a>,
rewritten nightly from the live catalog and from makerplane-data issues labelled
<span class="mono">pack:{e(pack_id)}</span>.</p>
<div id="overlay"><p>See the {link(issues, f"issues labelled pack:{pack_id}")}.</p></div>
<p>Found something wrong in this data? {link(form, "Report a data problem")}.</p>
<script>
fetch("status.json", {{cache: "no-store"}}).then(r => r.ok ? r.json() : Promise.reject())
.then(s => {{
  const box = document.getElementById("overlay"); box.textContent = "";
  const add = (tag, text, parent) => {{ const n = document.createElement(tag);
    if (text != null) n.textContent = text; (parent || box).appendChild(n); return n; }};
  const a = (href, text, parent) => {{ const n = add("a", text, parent);
    if (/^https:\\/\\/(github\\.com|navdata\\.aerocommons\\.org)\\//.test(href)) n.href = href;
    return n; }};
  const st = add("p"); st.append("As of " + s.as_of + ": "); add("b", s.state, st);
  st.append(" \\u2014 " + s.state_reason + ".");
  add("h3", "Issues labelled pack:" + s.pack.id);
  if (!s.known_issues.length) add("p", "None.");
  else {{ const ul = add("ul"); s.known_issues.forEach(i => {{ const li = add("li", null, ul);
    a(i.url, "#" + i.number + " " + i.title, li);
    li.append(" (" + i.state + (i.closed ? " " + i.closed : "") + ", opened " + i.opened +
      (i.reported_cycle ? ", reported against " + i.reported_cycle : "") + ")"); }}); }}
  if (s.auspex_addenda.length) {{ add("h3", "Auspex addenda (run after publish)");
    const ul = add("ul"); s.auspex_addenda.forEach(x => {{ const li = add("li", null, ul);
      a(x.url, x.name, li); li.append(" sha256 " + x.sha256); }}); }}
}}).catch(() => {{}});
</script></section>"""


def report_page(ed: dict, row: dict, entry: dict | None, prev: dict | None,
                *, commit: str) -> str:
    rec, p, b = ed["rec"], ed["rec"]["pack"], ed["rec"].get("built") or {}
    t = row["tally"]
    head = [f'<span class="tag">{e(rec["origin"])}</span>', e(p["kind"])]
    if row["effective"] and row["expires"]:
        head.append(f"effective {e(row['effective'])} to {e(row['expires'])}")
    head.append("in the live catalog" if row["listed"] else "not in the live catalog")
    banner = ""
    if rec["record"] == "rejected":
        banner = ('<p class="banner bad">The build gate refused this edition. It was never '
                  'listed in the catalog; the record is published because the gate catching '
                  'it is evidence the gate works.</p>')
    elif rec["record"] == "retro":
        banner = ('<p class="banner">This record was written after the edition was published, '
                  'from the published bytes. Build details that could no longer be recovered '
                  'are blank.</p>')
    match = ""
    if entry and entry.get("report_sha256"):
        match = (" &mdash; matches the <span class=mono>report_sha256</span> in the signed catalog"
                 if entry["report_sha256"] == ed["sha256"] else
                 ' &mdash; <b class="bad">differs from the <span class=mono>report_sha256</span> '
                 'in the signed catalog</b>')
    refs = "".join(f", {e(name)} {commit_link(BUILDER_REPOS.get(name, ''), sha)}"
                   for name, sha in sorted((b.get("builder_refs") or {}).items()))
    prev_cycle = (rec.get("previous") or {}).get("cycle")
    prev_link = (f'<a href="../{e(prev["cycle"])}/report.html">{e(prev["cycle"])}</a>'
                 if prev else e(prev_cycle) if prev_cycle else "none")
    shown = row["blockers"]
    stop = "This record would stop a publish:"
    if rec["record"] == "retro":
        # Only a build record can clear the gate, so every retro record
        # "would stop a publish"; say what else today's gate would refuse.
        shown = [x for x in shown if not x.startswith("record is retro")]
        stop = ("Judged by today's publish gate as a new build, this edition would be "
                "refused for:")
    blockers = ("".join(f"<li>{e(x)}</li>" for x in shown))
    body = f"""
<section class="lede"><h1>{e(pack_label(rec))} <span class="mono">{e(p['id'])}</span>
&middot; {e(p['cycle'])}</h1>
<p class="sub">{' &middot; '.join(head)} &middot; <a href="../index.html">history</a></p>
{banner}
<p class="notice">{e(rec['notice'])}</p></section>

<section><h2>Checks</h2>
<p class="tallies">{pill('pass')} {t['pass']} &nbsp; {pill('warn')} {t['warn']} &nbsp;
{pill('fail')} {t['fail']} &nbsp; {pill('not-run')} {t['not-run']}</p>
{callouts(rec)}
{f'<p class="banner bad">{e(stop)}</p><ul>{blockers}</ul>' if blockers else ''}
<p class="filter">Show: <label><input type="checkbox" value="pass" checked> pass</label>
<label><input type="checkbox" value="warn" checked> warn</label>
<label><input type="checkbox" value="fail" checked> fail</label>
<label><input type="checkbox" value="not-run" checked> not run</label></p>
<div class="scroll"><table class="grid checks"><tr><th></th><th>Check</th><th>What it checks</th>
<th>Measured</th><th>Expected</th><th>Cannot catch</th><th></th></tr>{check_rows(rec)}</table></div>
</section>

{auspex_html(rec)}
{tables_html(rec, prev_cycle)}

<section><h2>Identity</h2><table class="kv">
<tr><th>Pack sha256</th><td class="mono">{e(p['sha256'])}</td></tr>
<tr><th>Size</th><td>{num(p['bytes'])} bytes</td></tr>
<tr><th>Content schema</th><td>{e(p.get('schema_version'))}</td></tr>
<tr><th>Record</th><td>{e(rec['record'])} &middot; <a href="report.json">raw JSON</a>
<span class="mono">sha256 {e(ed['sha256'][:16])}</span>{match}</td></tr>
<tr><th>Built</th><td>{e(b.get('at'))} &middot; {e(b.get('runner'))} &middot;
{link(b.get('run_url'), 'run') if b.get('run_url') else 'no CI run recorded'}</td></tr>
<tr><th>From</th><td>makerplane-data {commit_link(REPO_URL, b.get('commit'))}{refs}</td></tr>
<tr><th>Compared against</th><td>{prev_link}</td></tr>
</table>
<h3>Sources</h3>{sources_html(rec)}</section>

{known_html(p['id'], p['cycle'])}

{standing_block()}

<section><h2>Check it yourself</h2>
<pre>packtool report --check {e(p['id'])}-{e(p['cycle'])}.pack</pre>
<p>Re-runs every open check on the pack you downloaded and compares the result with this
record. <a href="../../check.html">How to do it</a>.</p></section>
<script>
document.querySelector(".filter").addEventListener("change", () => {{
  const on = new Set([...document.querySelectorAll(".filter input:checked")].map(i => i.value));
  document.querySelectorAll("tr[data-status]").forEach(r =>
    r.style.display = on.has(r.dataset.status) ? "" : "none");
}});
</script>"""
    return page(f"{p['id']} {p['cycle']} test report", body, root="../../../", commit=commit)


# --- history ----------------------------------------------------------------

W, H, PAD_L, PAD_R, PAD_T, PAD_B = 260, 120, 54, 46, 12, 20


def trend_svg(table: str, rows: list[dict]) -> str:
    pts = [(r, r["tables"].get(table)) for r in rows]
    pts = [(r, v) for r, v in pts if v is not None]
    if not pts:
        return ""
    top = max(v for _, v in pts) * 1.1 or 1
    iw, ih = W - PAD_L - PAD_R, H - PAD_T - PAD_B

    def x(i: int) -> float:
        return PAD_L + (iw / 2 if len(pts) == 1 else iw * i / (len(pts) - 1))

    def y(v: float) -> float:
        return PAD_T + ih * (1 - v / top)

    grid = "".join(
        f'<line class="gl" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
        f'<text class="axis" x="{PAD_L - 6}" y="{y(v) + 4:.1f}" text-anchor="end">'
        f'{v:,.0f}</text>' for v in (0, top / 2, top))
    line = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, (_, v) in enumerate(pts))
    dots = "".join(
        f'<g><title>{e(r["cycle"])}: {v:,} rows</title><circle class="dot" '
        f'cx="{x(i):.1f}" cy="{y(v):.1f}" r="4"/></g>' for i, (r, v) in enumerate(pts))
    lx, lv = x(len(pts) - 1), pts[-1][1]
    labels = (f'<text class="end" x="{lx + 8:.1f}" y="{y(lv) + 4:.1f}">{lv:,}</text>'
              f'<text class="axis" x="{x(0):.1f}" y="{H - 4}" text-anchor="start">'
              f'{e(pts[0][0]["cycle"])}</text>')
    if len(pts) > 1:
        labels += (f'<text class="axis" x="{lx:.1f}" y="{H - 4}" text-anchor="end">'
                   f'{e(pts[-1][0]["cycle"])}</text>')
    return (f'<figure class="trend"><figcaption class="mono">{e(table)}</figcaption>'
            f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{e(table)} rows over '
            f'{len(pts)} editions">{grid}<polyline class="line" points="{line}"/>{dots}'
            f'{labels}</svg></figure>')


def history_page(pack_id: str, eds: list[dict], rows: list[dict], *, commit: str) -> str:
    latest = eds[-1]["rec"]
    editions = "".join(
        f'<tr><td><a href="{e(r["cycle"])}/report.html">{e(r["cycle"])}</a></td>'
        f'<td>{e(r["record"])}</td><td>{e(r["effective"]) or "&mdash;"}</td>'
        f'<td>{summary_line(r["tally"])}</td>'
        f'<td>{"listed" if r["listed"] else "not listed"}</td></tr>'
        for r in reversed(rows))
    tables = sorted({t for r in rows for t in r["tables"]})
    charts = "".join(trend_svg(t, rows) for t in tables)
    checks = sorted({c for r in rows for c in r["statuses"]})
    head = "".join(f"<th>{e(r['cycle'])}</th>" for r in rows)
    grid = "".join(
        f"<tr><td class=mono>{e(c)}</td>"
        + "".join(f"<td>{pill(r['statuses'][c]) if c in r['statuses'] else '&mdash;'}</td>"
                  for r in rows) + "</tr>" for c in checks)
    body = f"""
<section class="lede"><h1>{e(pack_label(latest))} <span class="mono">{e(pack_id)}</span></h1>
<p class="sub">Every edition of this pack with a published test record, newest first.
Editions published before records existed have none.</p></section>
<section><h2>Editions</h2><table class="grid"><tr><th>Edition</th><th>Record</th>
<th>Effective</th><th>Checks</th><th>Live catalog</th></tr>{editions}</table></section>
<section><h2>Row counts over time</h2><p class="sub">Oldest to newest. Hover a point for its
edition.</p><div class="trends">{charts}</div></section>
<section><h2>Check status over time</h2><div class="scroll"><table class="grid">
<tr><th>Check</th>{head}</tr>{grid}</table></div></section>
{standing_block()}"""
    return page(f"{pack_id} history", body, root="../../", commit=commit)


# --- index pages ------------------------------------------------------------

def reports_index_page(by_id: dict[str, list[dict]], *, commit: str) -> str:
    if by_id:
        rows = "".join(
            f'<tr><td><a href="{e(i)}/index.html" class="mono">{e(i)}</a></td>'
            f'<td>{e(KIND_LABELS.get(rs[-1]["kind"], rs[-1]["kind"]))}</td>'
            f'<td><a href="{e(i)}/{e(rs[-1]["cycle"])}/report.html">{e(rs[-1]["cycle"])}</a></td>'
            f'<td>{summary_line(rs[-1]["tally"])}</td><td>{len(rs)}</td></tr>'
            for i, rs in sorted(by_id.items()))
        table = (f'<table class="grid"><tr><th>Pack</th><th>Data</th><th>Newest record</th>'
                 f'<th>Checks</th><th>Editions</th></tr>{rows}</table>')
    else:
        table = ('<p class="msg">No pack has a published test record yet. Records are '
                 'written by the build from the next publish of each pack on.</p>')
    body = f"""
<section class="lede"><h1>Test reports</h1>
<p class="sub">For each data pack edition: the checks we ran on it, what each one measured,
and what each one cannot catch. A pack with no record here has not been published since
records began; the <a href="../index.html">catalog</a> says so on its row.</p></section>
<section>{table}</section>
{standing_block()}"""
    return page("Test reports", body, root="../", commit=commit)


def check_page(*, commit: str) -> str:
    body = f"""
<section class="lede"><h1>Check it yourself</h1>
<p class="sub">Every open check in a test report can be re-run by anyone, on the pack bytes
you download, with public code. Here is how.</p></section>

<section><h2>1. Get the tools</h2>
<pre>pip install "makerplane-data @ git+{REPO_URL}"</pre>
<p>This installs <code>packtool</code>. It needs Python 3.10 or newer and no account or key.</p></section>

<section><h2>2. Download a pack</h2>
<p>Use the <b>Download</b> button on the <a href="../index.html">catalog</a>, or fetch the URL
from <a href="../manifest.json">manifest.json</a>. Each catalog entry lists the pack's
sha256; <code>sha256sum &lt;file&gt;</code> should print the same value.</p></section>

<section><h2>3. Re-run the checks</h2>
<pre>packtool report --check procedures-conus-2611.pack</pre>
<p>With no other arguments, <code>--check</code> fetches the signed catalog, checks its
signature against the published key, matches your file to its entry by sha256, fetches
that edition's <code>report.json</code> and checks it against the catalog's
<code>report_sha256</code>. It then re-runs every open check and compares each check's
status and measured value, the row counts and the pack identity with the published
record. It exits 1 on any difference.</p>
<p>Offline, compare against a record you saved: <code>--against report.json</code>.</p>
<p>Some checks need more than the pack. They print as <b>not reproducible here</b> with the
reason, not as a difference:</p>
<ul><li>checks against the previous edition need it: <code>--previous OLD.pack</code> or
its <code>report.json</code>;</li>
<li>the four upstream-schema layers need the FAA CSVs the build read;</li>
<li>device acceptance needs a pyEfis checkout (<code>--pyefis-src</code>);</li>
<li>results from Auspex, a proprietary AI test engine, cannot be re-run outside. Each of
its findings is printed with the location it names, so you can look at that place in the
pack yourself.</li></ul></section>

<section><h2>4. Check the catalog signature by hand</h2>
<p>The catalog is signed with an ed25519 key in minisign format. The public key is
<a href="{REPO_URL}/blob/main/keys/minisign.pub">keys/minisign.pub</a> in the repository:</p>
<pre>minisign -Vm manifest.json -P RWQXjK7+q8WvsWtCd+QPQNxO7OcI5XuF1uxY7MGp+R2JidxFSO/20AR4</pre>
<p>or, with the tools from step 1, <code>packtool verify manifest.json --pub minisign.pub</code>.</p></section>

<section><h2>5. Check which code built a pack</h2>
<p>A pack built by one of our GitHub Actions workflows carries a build provenance
attestation: a statement, signed for that one workflow run through Sigstore and kept in a
public transparency log, of which workflow, commit and run produced the pack's sha256. Our
signing key cannot make one. With the <a href="https://cli.github.com">GitHub CLI</a>,
signed in to any GitHub account:</p>
<pre>gh attestation verify highways-conus-2026q3r1.pack \
  --repo billmallard/makerplane-data \
  --signer-workflow billmallard/makerplane-data/.github/workflows/roads.yml \
  --deny-self-hosted-runners</pre>
<p>It exits nonzero if the signature, the signer or the file does not match, or if the pack
has no attestation. The workflow is <code>cyclical.yml</code> for airports, navaids,
obstacles and procedures, <code>roads.yml</code> for roads and <code>water.yml</code> for
<code>water-conus</code>. It says which public code made the bytes, not that the data is
right.</p>
<p><b>Terrain packs have no attestation.</b> They are built on our own build machine from a
private repository, and GitHub issues these attestations from private repositories only
on its paid Enterprise plan. Packs built on a workstation (today <code>water-na</code> and
<code>airports-canada</code>) and editions published before attestations began have none either. For
those, the signed catalog and the test record are what you can check.
<a href="{REPO_URL}/blob/main/docs/provenance.md">Details</a>.</p></section>

<section><h2>If your numbers differ</h2>
<p>Please <a href="{REPO_URL}/issues/new">open an issue</a> with the pack id and edition,
the command you ran and its output. A published number that does not reproduce is a
defect in the record or in the tool, and we want to know.</p></section>
{standing_block()}"""
    return page("Check it yourself", body, root="../", commit=commit)


# --- the whole site ---------------------------------------------------------

def build(records: list[dict], manifest: dict | None, *, commit: str = "") -> dict[str, bytes]:
    """Every generated object, ``key -> bytes``. Raises :class:`SiteError`
    if any page fails :func:`lint`."""
    entries = manifest_entries(manifest)
    by_id: dict[str, list[tuple[dict, dict]]] = {}
    for ed in records:
        row = edition_row(ed, entries.get((ed["id"], ed["cycle"])))
        by_id.setdefault(ed["id"], []).append((ed, row))
    for pairs in by_id.values():
        pairs.sort(key=lambda er: _order(er[1]))

    out: dict[str, str | bytes] = {}
    index = {}
    for pack_id, pairs in sorted(by_id.items()):
        eds = [ed for ed, _ in pairs]
        rows = [row for _, row in pairs]
        for i, (ed, row) in enumerate(pairs):
            prev_cycle = (ed["rec"].get("previous") or {}).get("cycle")
            prev = next((r for r in rows if r["cycle"] == prev_cycle), None)
            out[f"{PREFIX}{pack_id}/{ed['cycle']}/report.html"] = report_page(
                ed, row, entries.get((pack_id, ed["cycle"])), prev, commit=commit)
            index.setdefault(pack_id, {})[ed["cycle"]] = {
                "record": row["record"], "origin": row["origin"],
                "summary": {k.replace("-", "_"): v for k, v in row["tally"].items()},
                "page": f"{PREFIX}{pack_id}/{ed['cycle']}/report.html"}
        out[f"{PREFIX}{pack_id}/index.html"] = history_page(pack_id, eds, rows, commit=commit)
    out[f"{PREFIX}index.html"] = reports_index_page(
        {i: [r for _, r in pairs] for i, pairs in by_id.items()}, commit=commit)
    out[f"{PREFIX}check.html"] = check_page(commit=commit)

    problems = [f"{k}: {p}" for k, v in out.items() for p in lint(v)]
    if problems:
        raise SiteError("refusing to publish:\n  " + "\n  ".join(problems))
    blob = {k: v.encode("utf-8") for k, v in out.items()}
    blob[INDEX_KEY] = (json.dumps({"schema": INDEX_SCHEMA, "packs": index}, indent=2,
                                  sort_keys=True) + "\n").encode("utf-8")
    return blob


CONTENT_TYPES = {".html": "text/html; charset=utf-8",
                 ".json": "application/json; charset=utf-8"}


def _writable(key: str) -> bool:
    """The generator writes pages and the index; never a record, a status
    overlay or an Auspex addendum."""
    return key.startswith(PREFIX) and (key == INDEX_KEY or key.endswith(".html"))


def publish(store, objects: dict[str, bytes], *, dry_run: bool = False,
            log=print) -> list[list]:
    """Write each object whose bytes differ from the live one. Returns
    ``[key, bytes, status]`` rows like :func:`sitedeploy.deploy`."""
    bad = [k for k in objects if not _writable(k)]
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


CSS = """
:root{--navy:#152238;--navy2:#1F3151;--orange:#E8702A;--ink:#1c2430;--muted:#5d6675;
--line:#e6e8ec;--bg:#fff;--amber:#b8860b;--green:#2e7d4f;--red:#b3261e}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:var(--orange)}.wrap{max-width:1080px;margin:0 auto;padding:0 22px}
header.site{border-bottom:1px solid var(--line)}
.brandbar{display:flex;align-items:center;gap:20px;padding:16px 22px;flex-wrap:wrap}
.ac{display:flex;align-items:center;gap:10px;text-decoration:none}.ac img{height:34px}
.ac .name{font-weight:700;color:var(--navy);font-size:17px}.crumb{color:var(--muted);font-size:14px}
main{padding:20px 22px 10px}section{margin-bottom:34px}
h1{font-size:25px;color:var(--navy);margin:0 0 6px}h2{font-size:19px;color:var(--navy);margin:0 0 8px}
h3{font-size:15px;color:var(--navy);margin:18px 0 6px}.sub{color:var(--muted);margin:0 0 12px}
.mono,code,pre{font-family:ui-monospace,"SFMono-Regular",Consolas,monospace;font-size:13px}
code{background:#eef1f6;padding:1px 5px;border-radius:4px}
pre{background:#eef1f6;padding:10px 14px;border-radius:8px;overflow-x:auto}
.tag{background:#eef1f6;color:var(--navy2);border-radius:20px;padding:1px 9px;font-weight:700;font-size:12px}
.notice{color:#6b5a2e;font-size:13.5px}
.banner{border-left:3px solid var(--amber);background:#fffaf0;padding:10px 14px;border-radius:0 8px 8px 0}
.banner.bad{border-color:var(--red);background:#fdf1f0}.bad{color:var(--red)}
.standing{border-left:3px solid var(--amber);background:#fffaf0;padding:12px 16px;
border-radius:0 8px 8px 0;color:#6b5a2e}
.st{font-size:11.5px;font-weight:700;padding:1px 8px;border-radius:20px;white-space:nowrap}
.st-pass{background:#e8f5ee;color:var(--green)}.st-warn{background:#fbf2dd;color:var(--amber)}
.st-fail{background:#fdf1f0;color:var(--red)}.st-not-run{background:#eef1f6;color:var(--muted)}
.callouts{padding-left:18px}.callouts li{margin:4px 0}
table.grid{border-collapse:collapse;width:100%}table.grid th,table.grid td{text-align:left;
padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
table.grid th{color:var(--muted);font-weight:600;font-size:13px}
table.checks td{font-size:13.5px}td.cc{color:var(--muted);font-size:13px}td.num{text-align:right;
font-variant-numeric:tabular-nums}
table.kv th{text-align:left;color:var(--muted);font-weight:600;padding:4px 14px 4px 0;
vertical-align:top;white-space:nowrap}table.kv td{padding:4px 0;word-break:break-all}
.scroll{overflow-x:auto}.filter label{margin-right:12px;font-size:14px}
.msg{padding:16px 18px;border:1px solid var(--line);border-radius:10px;color:var(--muted)}
.trends{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}
.trend{margin:0;border:1px solid var(--line);border-radius:10px;padding:8px 10px}
.trend figcaption{color:var(--muted)}.trend svg{width:100%;height:auto;display:block;overflow:visible}
.trend .gl{stroke:var(--line);stroke-width:1}
.trend .axis{fill:var(--muted);font-size:10px;paint-order:stroke;stroke:#fff;stroke-width:3px}
.trend .end{fill:var(--ink);font-size:12px;font-weight:600;paint-order:stroke;stroke:#fff;stroke-width:3px}
.trend .line{fill:none;stroke:var(--navy2);stroke-width:2;stroke-linejoin:round}
.trend .dot{fill:var(--orange);stroke:#fff;stroke-width:2}
footer.site{border-top:1px solid var(--line);padding:20px 0 36px;color:var(--muted);font-size:13px}
footer.site a{color:var(--muted)}
"""
