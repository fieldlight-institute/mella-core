# SPDX-License-Identifier: MPL-2.0
"""Independent local record, reading, and recovery tools. No model is required."""
from __future__ import annotations

import argparse
import base64
import html
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

from . import preservation
from .store import Store, StoreError


def _json(value):
    return json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _write_new(path, content):
    """Never replace an existing output, including an existing symlink."""
    path = preservation.safe_path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as output:
            output.write(content)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def export_record(store):
    """A complete inspectable export, including source records and every revision.

    The archive format remains the supported restore input. This JSON is an
    independent reading/interchange representation with exact payload bytes.
    It has no secret detector: imported private content remains private content.
    """
    with store._open() as container:
        container.db.execute("BEGIN")
        inventory = preservation.inventory(container.db)
        records = []
        for ident, version, kind, sha, raw in container.db.execute(
                "SELECT record_id,version,kind,sha256,raw FROM records ORDER BY record_id,version"):
            item = {"record_id": ident, "version": version, "kind": kind,
                    "sha256": sha, "raw_base64": base64.b64encode(raw).decode("ascii")}
            try:
                item["value"] = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                item["value"] = None
            records.append(item)
        contexts = [{"context_id": ident, "raw_base64": base64.b64encode(raw).decode("ascii"),
                     "value": json.loads(raw)} for ident, raw in container.db.execute(
                         "SELECT context_id,descriptor FROM contexts ORDER BY context_id")]
        events = [{"seq": seq, "raw_base64": base64.b64encode(raw).decode("ascii"),
                   "value": json.loads(raw)} for seq, raw in container.db.execute(
                       "SELECT seq,body FROM events ORDER BY seq")]
        return {"export_schema": "mella.core-export/1", "exported_at": preservation.now(),
                "meta": dict(container.db.execute("SELECT key,value FROM meta")),
                "inventory": inventory, "records": records, "contexts": contexts, "events": events}


def reader_html(export):
    """Offline, script-free reading surface with all versions and source objects."""
    esc = lambda value: html.escape(str(value), quote=True)
    def object_value(record):
        return record["value"] if isinstance(record["value"], dict) else {}
    sections, links = [], []
    ordered = sorted(export["records"], key=lambda record: (
        record["kind"] == "source",
        str(object_value(record).get("title", record["record_id"])).casefold(),
        record["record_id"], -record["version"]))
    for index, record in enumerate(ordered):
        value = object_value(record)
        title = value.get("title", value.get("filename", record["record_id"]))
        label = "{} · version {} · {}".format(title, record["version"], record["kind"])
        links.append('<li><a href="#record-{}">{}</a></li>'.format(index, esc(label)))
        text = value.get("content", value.get("text", ""))
        sections.append('<article id="record-{index}"><h2>{title}</h2><p class="meta">{meta}</p>'
                        '<pre>{text}</pre><details><summary>Original stored object and provenance</summary>'
                        '<pre>{original}</pre></details></article>'.format(
                            index=index, title=esc(title), meta=esc(label + " · " + record["record_id"]),
                            text=esc(text), original=esc(_json(record))))
    return '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Mella Core — local record</title><style>
:root {{ color-scheme: light; font: 17px/1.6 system-ui,sans-serif; color:#142d3a; background:#faf8f1; }}
body {{ max-width:62rem; margin:0 auto; padding:2rem 1.3rem 5rem; }}
h1,h2 {{ line-height:1.2; }} h1 {{ font-size:2.4rem; }} h2 {{ font-size:1.5rem; }}
a {{ color:#09665f; }} article {{ border-top:1px solid #bbc9c8; margin-top:2.5rem; padding-top:1rem; }}
pre {{ white-space:pre-wrap; overflow-wrap:anywhere; font:inherit; }}
.meta {{ color:#50646a; font-size:.85rem; overflow-wrap:anywhere; }}
summary {{ cursor:pointer; }} details pre {{ font: .8rem/1.5 ui-monospace,monospace; }}
nav {{ max-height:24rem; overflow:auto; }} @media print {{ nav {{ max-height:none; }} }}
</style></head><body><h1>Your local record</h1>
<p>All stored revisions and original source objects are included below. This file works offline.
It contains your data; share it only with people you intend to receive that record.</p>
<p class="meta">Container: {container} · Exported: {date}</p>
<nav aria-label="Records"><ol>{links}</ol></nav>{sections}
<details><summary>Container metadata, pinned contexts, and event index</summary><pre>{metadata}</pre></details>
</body></html>'''.format(container=esc(export["meta"]["container_id"]), date=esc(export["exported_at"]),
                        links="".join(links), sections="".join(sections),
                        metadata=esc(_json({key: export[key] for key in ("meta", "inventory", "contexts", "events")})))


def parser():
    root = argparse.ArgumentParser(description="Mella Core: local records, independent reading, and recovery.")
    root.add_argument("--data-dir", type=Path, help="Explicit local container directory")
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create a new local container")
    imp = sub.add_parser("import", help="Preserve a UTF-8 text, Markdown, or conversation JSON file")
    imp.add_argument("file", type=Path)
    listing = sub.add_parser("list", help="List or search current reading views")
    listing.add_argument("--query", default="")
    listing.add_argument("--json", action="store_true")
    read = sub.add_parser("read", help="Read a record or pinned historical version")
    read.add_argument("record_id")
    read.add_argument("--version", type=int)
    read.add_argument("--format", choices=("text", "json", "original"), default="text")
    versions = sub.add_parser("versions", help="List preserved versions of a record")
    versions.add_argument("record_id")
    for name in ("note", "revise"):
        note = sub.add_parser(name, help="Append a note" if name == "note" else "Append an immutable note revision")
        if name == "revise":
            note.add_argument("record_id")
            note.add_argument("--expected-version", type=int, required=True)
        note.add_argument("--title", required=True)
        note.add_argument("--file", type=Path, required=True)
        note.add_argument("--provider", default="You" if name == "note" else None,
                          help="Attribution label, not authenticated identity")
    context = sub.add_parser("context", help="Read or select a version-pinned working context")
    context.add_argument("--record", action="append", dest="records")
    context.add_argument("--clear", action="store_true")
    context.add_argument("--title", default="Working context")
    context.add_argument("--reasoning", choices=("standard", "deep", "maximum"), default="standard")
    sub.add_parser("snapshot", help="Create a verified, compressed container snapshot")
    for name in ("export", "reader"):
        out = sub.add_parser(name, help="Write complete JSON" if name == "export" else "Write a standalone offline HTML reader")
        out.add_argument("--output", type=Path, required=True)
    explain = sub.add_parser("explain", help="Read source-validated decision history without a model")
    explain.add_argument("--work-id", default="default")
    explain.add_argument("--conversation-id")
    explain.add_argument("--format", choices=("json", "html"), default="json")
    explain.add_argument("--output", type=Path, help="New offline file; otherwise JSON goes to stdout")
    verify = sub.add_parser("verify", help="Fully validate a snapshot in a temporary independent directory")
    verify.add_argument("archive", type=Path)
    restore = sub.add_parser("restore", help="Restore a snapshot to a fresh directory; never overwrite")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--to", type=Path, required=True)
    return root


def _input_text(path):
    with path.open("rb") as source:
        raw = source.read(20 * 1024 * 1024 + 1)
    if len(raw) > 20 * 1024 * 1024:
        raise StoreError("Input exceeds the 20 MiB limit.")
    return raw.decode("utf-8")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "verify":
            with tempfile.TemporaryDirectory(prefix="mella-core-verify-") as temp:
                receipt = preservation.restore(args.archive, Path(temp).resolve() / "restored")
            print(_json({"verified": True, **receipt}), end="")
            return 0
        if args.command == "restore":
            print(_json(preservation.restore(args.archive, args.to)), end="")
            return 0
        if args.data_dir is None:
            raise StoreError("Use --data-dir PATH to choose a container explicitly.")
        directory = preservation.safe_path(args.data_dir)
        exists = (directory / "container.sqlite3").is_file()
        if args.command == "init" and exists:
            raise StoreError("A container already exists here; init will not replace it.")
        if args.command != "init" and not exists:
            raise StoreError("Container not found. Create it with init, or select an existing directory.")
        if args.command == "explain":
            from .decision_reader import read_container, render_html
            if args.format == "html" and args.output is None:
                raise StoreError("Use --output PATH for an HTML reading copy.")
            document = read_container(directory / "container.sqlite3", args.work_id, args.conversation_id)
            if args.output:
                content = render_html(document) if args.format == "html" else _json(document)
                result = {"path": str(_write_new(args.output, content)), "head": document["guidance"]["head"]}
            else:
                result = document
            print(_json(result), end="")
            return 0
        store = Store(directory)
        if args.command == "init":
            result = store.state()["container"]
        elif args.command == "import":
            result = store.import_text(args.file.name, _input_text(args.file))
        elif args.command == "list":
            result = store.search_records(args.query)
            if not args.json:
                for row in result:
                    print("{}  v{}  {}".format(row["id"], row["version"], row["title"]))
                return 0
        elif args.command == "read":
            result = store.get_record(args.record_id, version=args.version)
            if args.format == "text":
                print(result["content"])
                return 0
            if args.format == "original":
                source = result.get("metadata", {}).get("source")
                if source:
                    sys.stdout.write(store.get_record(source["id"])["content"])
                else:
                    sys.stdout.write(_json(result.get("metadata", {}).get("original", result)))
                return 0
        elif args.command == "versions":
            result = store.versions(args.record_id)
        elif args.command == "note":
            result = store.add_note(args.title, _input_text(args.file), provider=args.provider)
        elif args.command == "revise":
            result = store.revise_note(args.record_id, args.title, _input_text(args.file),
                                       provider=args.provider, expected_version=args.expected_version)
        elif args.command == "context":
            if args.clear and args.records:
                raise StoreError("Choose --clear or --record, not both.")
            result = store.set_context(args.records or [], reasoning=args.reasoning, title=args.title) \
                if args.clear or args.records is not None else store.get_context()
        elif args.command == "snapshot":
            result = store.create_snapshot()
            result["path"] = str(store.snapshot_path(result["id"]))
        elif args.command in {"export", "reader"}:
            exported = export_record(store)
            content = _json(exported) if args.command == "export" else reader_html(exported)
            result = {"path": str(_write_new(args.output, content)), "record_versions": len(exported["records"])}
        print(_json(result), end="")
        return 0
    except (StoreError, preservation.Rejected, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print("Mella Core: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
