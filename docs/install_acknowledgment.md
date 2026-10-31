<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Software notice acknowledgment at install / update

Installing or updating through `pyefis-data` shows this notice and requires an
explicit accept before anything is installed:

> pyEfis is experimental software for Experimental / Amateur-Built aircraft. It is
> not certified. It is provided as-is with no warranty, and any use of it is at
> your own risk.

The accept is asked for only on the ground, during install or update. It is
never a modal in front of the instruments; the pyEfis startup notice is a
non-blocking banner (BACKLOG L3) that reads the record described here.

## What the installer sees

First run on a fresh box, at a terminal:

```
$ pyefis-data update
------------------------------------------------------------------------
pyEfis is experimental software for Experimental / Amateur-Built aircraft. It is not certified. It is provided as-is with no warranty, and any use of it is at your own risk.
------------------------------------------------------------------------
Type "accept" to accept and continue, anything else to stop: accept
notice accepted; recorded in /home/pi/.local/state/makerplane/acknowledgments.jsonl
```

Anything other than the word `accept` (an empty line, `y`, `yes`) stops with
exit code 3 and installs nothing. The selection in `data.yaml` is not touched
either: the gate runs before `--only` is persisted.

Once accepted, later updates by the same `pyefis-data` version proceed without
asking. Installing a new `pyefis-data` version, or any change to the notice
text, requires a new accept.

To accept ahead of time, for example as a step in an image build or install
script run at a terminal:

```bash
pyefis-data accept                 # shows the notice, asks for "accept"
pyefis-data notice                 # the notice and whether it is accepted
pyefis-data notice --json          # the same plus every record (for the About screen)
```

## Unattended runs

The daily timer (`pyefis-data.service`) and the USB import (`usb-import.sh`)
have no terminal, so they never prompt. With an accept on record for the
installed version they run as before. Without one they stop with exit code 3,
log the notice and the instruction to run `pyefis-data accept`, and install
nothing. On a fresh box that means data starts arriving only after someone has
accepted. After a `pyefis-data` upgrade, automatic updates pause until the new
version is accepted, and the EFIS DATA flag shows the data ageing in the
meantime.

## Callers that show the notice themselves

The pyEfis installer and the on-device pack picker run `pyefis-data` without a
terminal. They display the full notice text themselves (from
`pyefis-data notice --json`, field `notice`), collect the accept, and then pass
it through:

```bash
pyefis-data update --accept-notice --software pyefis=2.1.0 ...   # picker: accept, then install
pyefis-data accept --yes --trigger pyefis-install --software pyefis=2.1.0
```

These are recorded with `"via": "caller"`. `--software NAME=VERSION` (repeatable,
on `update`, `import` and `accept`) adds a component version to the record. The
picker should pass `pyefis=<version>`, because pyEfis is not importable from the
updater's own environment and would otherwise be absent from `software`. A caller must not pass them without
having shown the full text and received an explicit accept; the record asserts
that it did.

With `--progress`, a refused update prints one JSON line the picker can act on:

```json
{"event": "notice_required", "notice": "pyEfis is experimental ...", "notice_id": "pyefis-software-2026-10-30"}
```

## The record

Path: `$XDG_STATE_HOME/makerplane/acknowledgments.jsonl`, by default
`~/.local/state/makerplane/acknowledgments.jsonl`. `MAKERPLANE_ACK_LOG`
overrides it.

It sits deliberately outside both config trees, `~/.makerplane/` (this updater)
and `~/makerplane/pyefis/config` (pyEfis), so resetting either does not erase
it. It is append-only: the file is only ever opened with `O_APPEND`, one JSON
object per line, and each write is fsynced before the install proceeds. If the
record cannot be written, nothing is installed (exit code 2). An accept that
cannot be shown later does not count.

Schema 1, one line per accept (pretty-printed here):

```json
{
  "schema": 1,
  "event": "accept",
  "timestamp": "2026-10-31T14:05:09Z",
  "notice_id": "pyefis-software-2026-10-30",
  "notice_sha256": "<sha256 of notice_text, hex>",
  "notice_text": "pyEfis is experimental software for ...",
  "software": {"pyefis-data": "0.4.0", "pyefis": "2.1.0"},
  "packs": {"airports-conus": "2606", "obstacles-conus": "260611"},
  "user": "pi",
  "host": "pyefis",
  "via": "tty",
  "trigger": "update"
}
```

| Field | Meaning |
|---|---|
| `schema` | Record format version. Readers skip records with a schema they do not know. |
| `event` | Always `accept` in schema 1. |
| `timestamp` | UTC, ISO 8601, `YYYY-MM-DDTHH:MM:SSZ`. |
| `notice_id`, `notice_sha256`, `notice_text` | The exact text accepted. The sha256 is over the UTF-8 text, so a record proves which wording was shown. |
| `software` | Component versions at accept. `pyefis-data` is always present; `pyefis` when it is pip-installed or the caller names it with `--software`. |
| `packs` | Pack id to installed cycle at the moment of accept. On a fresh install this is empty, because the accept comes before the first pack. |
| `user`, `host` | The local account and hostname that accepted. |
| `via` | `tty` (typed at a terminal) or `caller` (a caller that showed the notice itself). |
| `trigger` | What asked: `update`, `import`, `install`, or the `--trigger` given to `accept`. |

`pyefis_data.acknowledge.validate_record` is the reference check, and
`tests/test_acknowledge.py` asserts the schema. Readers skip any line that fails
it, and an unreadable or missing log counts as "no accept on record". That only
ever means the notice is shown again on the ground. It never blocks anything in
flight.

### Reading it from pyEfis

The startup banner (L3) and the About screen (L4) read the file directly (no
updater install needed) or call `pyefis-data notice --json`. A reader should:

- Resolve the path the way the writer does: `MAKERPLANE_ACK_LOG` if set, else
  `$XDG_STATE_HOME/makerplane/acknowledgments.jsonl`, else
  `~/.local/state/makerplane/acknowledgments.jsonl`. A systemd user unit may
  not see the shell's environment, so in practice the last form is the one
  that matters.
- Count a line only if `schema == 1` and `event == "accept"`. Later schemas
  may add other event types that are not acceptances.
- Treat the record as covering the notice it shows only if `notice_sha256`
  equals the sha256 of the text it displays. A changed wording then counts as
  not yet accepted.
- Skip unparseable lines, including a torn last line. Every record is written
  as one `write()` of a complete line followed by fsync.

What to match on is L3's decision. Two facts constrain it:

- **`software.pyefis` is only present when the accept names it**: the pyEfis
  installer records it with `--software pyefis=<ver>`. A record written by
  `pyefis-data update` carries only `pyefis-data`, because pyEfis is normally
  not importable from the updater's own environment.
- **`packs` is what was installed at the moment of accept**, which comes before
  the install it gates (on a fresh box, `{}`). A pack-only update by the timer
  adds no record, because nobody accepted anything. It also differs in shape
  from `status.json`, which lists every tracked pack including missing ones
  (cycle `""`). Matching on an exact `packs` equality will therefore almost
  never succeed. Matching on `notice_sha256` plus the software version will.
