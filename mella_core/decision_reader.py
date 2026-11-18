# SPDX-License-Identifier: MPL-2.0
"""Read an existing Mella container offline, without a model or commercial app.

Usage: python3 -m mella_core.decision_reader /path/container.sqlite3 --work-id default
       python3 -m mella_core.decision_reader /path/container.sqlite3 --html reading.html
       python3 -m mella_core.decision_reader /path/container.sqlite3 --json record.json

Outputs never overwrite existing files. Exports include private source wording;
they are local reading artifacts, not an automatic publication operation.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import hashlib
import html
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from string import Template

from .decision_core import SCHEMA, PREFIX, ORIGIN, DecisionError, reduce_events, validate_event
from .decision_validation import validate_journal
from .preservation import MAX_ARCHIVE_MEMBER, ARCHIVE_SCHEMA


@contextmanager
def _read_database(path):
    """Read live WAL with query-only SQL; immutable is only for a verified copy.

    Some SQLite builds need writable file admission to create WAL sidecars,
    even for SELECT. query_only is set before accessing stored content; no
    data SQL writes or missing-database creation are permitted. If filesystem
    admission still fails, only a manifest-matched frozen copy is considered.
    """
    def connect(mode):
        connection = sqlite3.connect(path.as_uri() + "?mode=" + mode, uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("BEGIN")
            connection.execute("SELECT key,value FROM meta LIMIT 1").fetchall()
            return connection
        except BaseException:
            connection.close()
            raise

    try:
        try:
            db = connect("ro")
        except sqlite3.OperationalError:
            db = connect("rw")  # No create; incidental SQLite WAL/SHM only.
    except sqlite3.OperationalError:
        manifest_path = path.parent / "snapshot-manifest.json"
        wal_path = Path(str(path) + "-wal")
        if wal_path.exists() or not manifest_path.is_file():
            raise
        with manifest_path.open("rb") as source:
            manifest_raw = source.read(MAX_ARCHIVE_MEMBER + 1)
        if len(manifest_raw) > MAX_ARCHIVE_MEMBER:
            raise DecisionError("Snapshot manifest exceeds the supported size limit.")
        manifest = json.loads(manifest_raw)
        with path.open("rb") as source:
            raw = source.read(MAX_ARCHIVE_MEMBER + 1)
        expected = {"path": "container.sqlite3", "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        if (not isinstance(manifest, dict) or len(raw) > MAX_ARCHIVE_MEMBER or wal_path.exists() or
                manifest.get("archive_schema") != ARCHIVE_SCHEMA or manifest.get("database") != expected):
            raise DecisionError("Cannot read this live database safely; create and restore a fresh snapshot first.")
        with tempfile.TemporaryDirectory(prefix="mella-core-decision-read-") as directory:
            frozen = Path(directory).resolve() / "container.sqlite3"
            with os.fdopen(os.open(frozen, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
                output.write(raw)
            with closing(sqlite3.connect(frozen.as_uri() + "?mode=ro&immutable=1", uri=True)) as copy:
                copy.execute("PRAGMA query_only=ON")
                copy.execute("PRAGMA trusted_schema=OFF")
                copy.execute("BEGIN")
                yield copy
        return
    with closing(db):
        yield db


def read_container(database, work_id="default", conversation_id=None):
    path = Path(database).expanduser().absolute()
    if not path.is_file():
        raise DecisionError("An existing container.sqlite3 file is required.")
    # mode=ro reads the committed live WAL too; immutable=1 would omit it.
    with _read_database(path) as db:
        metadata = dict(db.execute("SELECT key,value FROM meta"))
        if metadata.get("schema") != "mella.container-proof/1":
            raise DecisionError("Unsupported container schema.")
        versions = dict(db.execute("SELECT record_id,MAX(version) FROM records GROUP BY record_id"))
        sources = {}

        def resolve(ident, version):
            key = (ident, version)
            if key not in sources:
                row = db.execute("SELECT kind,sha256,raw FROM records WHERE record_id=? AND version=?", key).fetchone()
                if not row or hashlib.sha256(row[2]).hexdigest() != row[1]:
                    raise DecisionError("A referenced source is missing or has failed its integrity check.")
                sources[key] = {"record_id": ident, "version": version, "kind": row[0],
                                "sha256": row[1], "record": json.loads(row[2])}
            return sources[key]

        events = []
        for ident, version, kind, expected, raw in db.execute(
                "SELECT record_id,version,kind,sha256,raw FROM records WHERE record_id GLOB ? ORDER BY record_id,version", (PREFIX + "*",)):
            if version != 1 or kind != "decision_event" or hashlib.sha256(raw).hexdigest() != expected:
                raise DecisionError("Reserved decision journal failed its integrity check.")
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get("metadata") != {"schema": SCHEMA, "origin": ORIGIN}:
                raise DecisionError("Invalid reserved decision envelope.")
            event = validate_event(json.loads(payload["content"]))
            if event["event_id"] != ident or event["seq"] != len(events) + 1:
                raise DecisionError("Invalid decision journal sequence.")
            events.append(event)
        validate_journal(events, resolve, metadata.get("container_id"))
        guidance = reduce_events(events, work_id, conversation_id, versions)
        # Include the exact source records needed to inspect all extracted
        # readings, including inactive history and original pre-review supports.
        references = set()
        for entry in guidance["directions"] + guidance["history"]:
            for source in [entry["source"]] + entry.get("supports", []) + entry.get("effective_supports", []):
                references.add((source["record_id"], source["version"]))
        for ident, version in sorted(references):
            resolve(ident, version)
    return {"schema": SCHEMA, "container": metadata, "guidance": guidance,
            "events": events, "current_versions": versions,
            "referenced_sources": [sources[key] for key in sorted(sources)],
            "authority": "Local record integrity and application provenance; no cryptographic identity or active permissions."}


def render_html(document):
    esc = lambda value: html.escape(str(value), quote=True)
    guidance = document["guidance"]

    def card(entry):
        source = entry["source"]
        explanation = "".join("<li><strong>{}</strong>: {} <small>{}</small></li>".format(
            esc(x["rule"]), esc(x["detail"]), esc(x["event_id"])) for x in entry["explanation"])
        origin = source.get("input_origin", {})
        attributed = "{} via {}".format(entry["issuer"]["provider"], origin.get("route", "unspecified"))
        return ("<article><p class='meta'>{status} · {subject} · {scope}</p><h3>{reading}</h3>"
                "<p>{attribution}. This is an interpretation, not authenticated speaker identity.</p>"
                "<blockquote>{quote}</blockquote><p class='meta'>{identity} · source {source_id} v{version} · characters {start}–{end}</p>"
                "<details><summary>Original human message and source route</summary><pre>{context}</pre><pre>{origin}</pre></details>"
                "<details><summary>Why this reading applies here</summary><ol>{explanation}</ol><pre>{relations}</pre></details>"
                "<details><summary>Supporting sources and revision review</summary><pre>{supports}</pre></details></article>").format(
            status=esc(entry["status"]), subject=esc(entry["subject_label"]), scope=esc(json.dumps(entry["scope"], ensure_ascii=False)),
            reading=esc(entry["interpretation"]), attribution=esc(attributed), quote=esc(source["quote"]), identity=esc(entry["id"]),
            source_id=esc(source["record_id"]), version=source["version"], start=source["start"], end=source["end"],
            context=esc(source.get("full_text", source.get("context", source["quote"]))), origin=esc(json.dumps(origin, ensure_ascii=False, indent=2)),
            explanation=explanation, relations=esc(json.dumps(entry.get("relations", []), ensure_ascii=False, indent=2)),
            supports=esc(json.dumps({"review_required": entry["review_required"], "original": entry.get("supports", []),
                                     "effective": entry.get("effective_supports", [])}, ensure_ascii=False, indent=2)))

    current = "".join(card(e) for e in guidance["directions"]) or "<p>No current extracted directions in this scope.</p>"
    history = "".join(card(e) for e in guidance["history"]) or "<p>No inactive readings.</p>"
    events = "".join("<details><summary>{} · {}</summary><pre>{}</pre></details>".format(
        esc(e["event_id"]), esc(e["type"]), esc(json.dumps(e, ensure_ascii=False, indent=2))) for e in document["events"])
    sources = "".join("<details><summary>{} · version {}</summary><pre>{}</pre></details>".format(
        esc(s["record_id"]), s["version"], esc(json.dumps(s["record"], ensure_ascii=False, indent=2))) for s in document["referenced_sources"])
    return Template("""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Mella · Decision record</title><style>
:root{color-scheme:light}body{margin:0;background:#f6f5ed;color:#142735;font:17px/1.6 system-ui,sans-serif}
main{max-width:900px;margin:auto;padding:3rem 1.5rem 6rem}h1{font:2.4rem Georgia,serif;margin-bottom:.5rem}
h2{border-top:1px solid #bac8c7;padding-top:2rem;margin-top:3rem}h3{font-size:1.2rem;font-weight:550}
article{padding:1.5rem 0 2rem;border-bottom:1px solid #cdd6d0}.meta,small{font-size:.83rem;color:#536b73;overflow-wrap:anywhere}
blockquote{border-left:3px solid #398b81;padding:.5rem 1rem;margin:1rem 0;white-space:pre-wrap}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.5 ui-monospace,monospace;background:#e9eee8;padding:1rem}
details{margin:.8rem 0}summary{cursor:pointer;color:#17665d}a{color:#17665d}nav{display:flex;gap:1.2rem;flex-wrap:wrap}
</style><main><p class="meta">Mella · Offline record</p><h1>What governs this work?</h1>
<p>Original words, attributed readings, changes, and the reasons behind the current view. This file makes no model requests and activates no permissions.</p>
<p class="meta">Work: $WORK · Conversation: $CONVERSATION<br>State: $HEAD</p>
<nav><a href="#current">Current readings</a><a href="#history">History</a><a href="#journal">Journal</a><a href="#sources">Exact sources</a></nav>
<h2 id="current">Current readings</h2>$CURRENT<h2 id="history">History</h2>$HISTORY
<h2 id="journal">Complete decision journal</h2>$EVENTS<h2 id="sources">Referenced original records</h2>$SOURCES</main></html>""").substitute(
        WORK=esc(guidance["coverage"]["work_id"]), CONVERSATION=esc(guidance["coverage"]["conversation_id"] or "all work-level guidance"),
        HEAD=esc(guidance["head"]), CURRENT=current, HISTORY=history, EVENTS=events, SOURCES=sources)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--work-id", default="default")
    parser.add_argument("--conversation-id")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--html", type=Path)
    output.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    try:
        document = read_container(args.database, args.work_id, args.conversation_id)
        if args.html:
            content, destination = render_html(document), args.html
        elif args.json:
            content, destination = json.dumps(document, ensure_ascii=False, indent=2) + "\n", args.json
        else:
            print(json.dumps(document["guidance"], ensure_ascii=False, indent=2))
            return 0
        from .cli import _write_new
        _write_new(destination, content)
        return 0
    except (DecisionError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print("Mella Core decision reader: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
