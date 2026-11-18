# SPDX-License-Identifier: MPL-2.0
"""Lossless Mella container preservation and validated independent restoration.

Source records, current access, and selected context remain separate. A
backup-scoped callback records external dependencies from the captured state.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from datetime import datetime, timezone
import zipfile


SCHEMA = "mella.container-proof/1"
ARCHIVE_SCHEMA = "mella.snapshot-proof/1"
MAX_ARCHIVE_MEMBER = 64 * 1024 * 1024
MEMBERS = {"manifest.json", "container.sqlite3"}


class Rejected(ValueError):
    pass


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_path(value):
    """Only explicit paths; reject symlinks, including parent components."""
    path = Path(os.path.abspath(value))
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise Rejected("Symlinks are not supported")
    return path


def require_fresh(path):
    if os.path.lexists(path):
        raise Rejected(f"Destination already exists: {path}")


def readonly(path):
    db = sqlite3.connect(safe_path(path).as_uri() + "?mode=ro&immutable=1", uri=True)
    db.execute("PRAGMA trusted_schema=OFF")
    return db


class Container:
    def __init__(self, path, *, create=False, container_id=None):
        self.path = safe_path(path)
        if create:
            if not container_id:
                raise Rejected("A stable container identity is required")
            require_fresh(self.path)
            with self.path.open("xb"):
                pass
            os.chmod(self.path, 0o600)
        elif not self.path.is_file():
            raise Rejected("Container does not exist")
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA trusted_schema=OFF")
        if create:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript("""
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE records (
                    record_id TEXT NOT NULL, version INTEGER NOT NULL,
                    kind TEXT NOT NULL, sha256 TEXT NOT NULL, raw BLOB NOT NULL,
                    PRIMARY KEY (record_id, version));
                CREATE TABLE contexts (context_id TEXT PRIMARY KEY, descriptor BLOB NOT NULL);
                CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, body BLOB NOT NULL);
            """)
            with self.db:
                self.db.executemany("INSERT INTO meta VALUES (?,?)", [("schema", SCHEMA), ("container_id", container_id)])
        if dict(self.db.execute("SELECT key,value FROM meta")).get("schema") != SCHEMA:
            self.db.close()
            raise Rejected("Unsupported container schema")

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _event(self, value):
        self.db.execute("INSERT INTO events(body) VALUES (?)", (encoded(value),))

    def add_record(self, record_id, kind, raw):
        if not record_id or not kind or not isinstance(raw, bytes):
            raise Rejected("Record identity, kind, and raw bytes are required")
        if self.db.in_transaction:
            raise Rejected("Finish the current transaction first")
        with self.db:
            # Acquire the write lock before choosing a version.
            self.db.execute("BEGIN IMMEDIATE")
            version = self.db.execute("SELECT COALESCE(MAX(version),0)+1 FROM records WHERE record_id=?", (record_id,)).fetchone()[0]
            self.db.execute("INSERT INTO records VALUES (?,?,?,?,?)", (record_id, version, kind, digest(raw), raw))
            self._event({"type": "record_added", "record_id": record_id, "version": version, "sha256": digest(raw)})
        return {"record_id": record_id, "version": version}

    def add_context(self, context_id, ordered_records, reasoning_settings):
        """Context selections are immutable. A revision receives a new descriptor ID."""
        if not context_id or not isinstance(reasoning_settings, dict):
            raise Rejected("Context identity and explicit reasoning settings are required")
        refs = []
        for ref in ordered_records:
            row = self.db.execute("SELECT sha256 FROM records WHERE record_id=? AND version=?", (ref["record_id"], ref["version"])).fetchone()
            if not row:
                raise Rejected("Context references a missing record version")
            refs.append({"record_id": ref["record_id"], "version": ref["version"], "sha256": row[0]})
        descriptor = {"context_id": context_id, "ordered_records": refs, "reasoning_settings": reasoning_settings}
        with self.db:
            self.db.execute("INSERT INTO contexts VALUES (?,?)", (context_id, encoded(descriptor)))
            self._event({"type": "context_added", "context_id": context_id, "sha256": digest(encoded(descriptor))})
        return descriptor

    def load_context(self, context_id, *, current_allowed_record_ids):
        """Caller-supplied CURRENT access only; archived permissions are never consulted."""
        row = self.db.execute("SELECT descriptor FROM contexts WHERE context_id=?", (context_id,)).fetchone()
        if not row:
            raise Rejected("Unknown context")
        descriptor = json.loads(row[0])
        allowed = set(current_allowed_record_ids)
        forbidden = [ref["record_id"] for ref in descriptor["ordered_records"] if ref["record_id"] not in allowed]
        if forbidden:
            raise Rejected("Current access excludes selected records: " + ", ".join(forbidden))
        loaded = []
        for ref in descriptor["ordered_records"]:
            row = self.db.execute("SELECT kind,sha256,raw FROM records WHERE record_id=? AND version=?", (ref["record_id"], ref["version"])).fetchone()
            if not row or digest(row[2]) != ref["sha256"] or row[1] != ref["sha256"]:
                raise Rejected("Selected record integrity failed")
            loaded.append({**ref, "kind": row[0], "raw": row[2]})
        return {"descriptor": descriptor, "records": loaded, "loaded_bytes": sum(len(item["raw"]) for item in loaded)}


def inventory(db):
    """Verify the immutable records and describe a committed database snapshot."""
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise Rejected("SQLite integrity check failed")
    objects = set(db.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND type != 'index'"))
    if objects != {("table", name) for name in ("meta", "records", "contexts", "events")}:
        raise Rejected("Unexpected database schema objects")
    meta = dict(db.execute("SELECT key,value FROM meta"))
    if meta.get("schema") != SCHEMA or not meta.get("container_id"):
        raise Rejected("Unsupported container identity or schema")
    records, lookup = [], {}
    for ident, version, kind, expected, raw in db.execute("SELECT record_id,version,kind,sha256,raw FROM records ORDER BY record_id,version"):
        if not isinstance(raw, bytes) or digest(raw) != expected or not ident or version < 1:
            raise Rejected("Record payload integrity failed")
        item = {"record_id": ident, "version": version, "kind": kind, "sha256": expected, "bytes": len(raw)}
        records.append(item)
        lookup[(ident, version)] = expected
    contexts = []
    for ident, raw in db.execute("SELECT context_id,descriptor FROM contexts ORDER BY context_id"):
        descriptor = json.loads(raw)
        if descriptor.get("context_id") != ident or not isinstance(descriptor.get("reasoning_settings"), dict):
            raise Rejected("Invalid context descriptor")
        for ref in descriptor["ordered_records"]:
            if lookup.get((ref["record_id"], ref["version"])) != ref["sha256"]:
                raise Rejected("Context references a missing or changed record")
        contexts.append({"context_id": ident, "sha256": digest(raw)})
    events = list(db.execute("SELECT seq,body FROM events ORDER BY seq"))
    if [seq for seq, _ in events] != list(range(1, len(events) + 1)):
        raise Rejected("Event boundary is not contiguous")
    # Bind events to the exact record/descriptor revisions in this capture.
    seen_records, seen_contexts = set(), set()
    context_hashes = {item["context_id"]: item["sha256"] for item in contexts}
    for _, body in events:
        event = json.loads(body)
        if event.get("type") == "record_added":
            key = (event["record_id"], event["version"])
            if key in seen_records or lookup.get(key) != event["sha256"]:
                raise Rejected("Record event does not match committed state")
            seen_records.add(key)
        elif event.get("type") == "context_added":
            ident = event["context_id"]
            if ident in seen_contexts or context_hashes.get(ident) != event["sha256"]:
                raise Rejected("Context event does not match committed state")
            seen_contexts.add(ident)
        else:
            raise Rejected("Unsupported event type")
    if seen_records != set(lookup) or seen_contexts != set(context_hashes):
        raise Rejected("Committed objects are missing their events")
    return {"container_schema": SCHEMA, "container_id": meta["container_id"], "records": records,
            "contexts": contexts, "event_boundary": len(events),
            "events_sha256": digest(encoded([[seq, json.loads(body)] for seq, body in events]))}


def capture(container, archive_path, *, external_dependencies=()):
    """Backup provides one committed cut; no directory enumeration or source reads."""
    archive_path = safe_path(archive_path)
    require_fresh(archive_path)
    if container.db.in_transaction:
        raise Rejected("Cannot capture this connection's uncommitted transaction")
    dependencies = None if callable(external_dependencies) else list(external_dependencies)
    if dependencies is not None and any(not isinstance(item, dict) or not item.get("id") for item in dependencies):
        raise Rejected("Each declared dependency needs an identity")
    with tempfile.TemporaryDirectory(prefix=".capture-", dir=archive_path.parent) as staging:
        backup_path = Path(staging) / "container.sqlite3"
        backup = sqlite3.connect(backup_path)
        try:
            container.db.backup(backup)
            contents = inventory(backup)
            if dependencies is None:
                # App adapter derives attachment references from this exact committed
                # backup, so a concurrent importer cannot make the manifest stale.
                dependencies = list(external_dependencies(backup))
                if any(not isinstance(item, dict) or not item.get("id") for item in dependencies):
                    raise Rejected("Each declared dependency needs an identity")
        finally:
            backup.close()
        raw = backup_path.read_bytes()
        manifest = {"archive_schema": ARCHIVE_SCHEMA, "captured_at": now(), **contents,
                    "database": {"path": "container.sqlite3", "bytes": len(raw), "sha256": digest(raw)},
                    "external_dependencies": dependencies}
        manifest_raw = encoded(manifest)
        if len(raw) > MAX_ARCHIVE_MEMBER or len(manifest_raw) > MAX_ARCHIVE_MEMBER:
            raise Rejected("Snapshot exceeds the 64 MiB uncompressed member size limit")
        temp_archive = Path(staging) / "snapshot.zip"
        with zipfile.ZipFile(temp_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", manifest_raw)
            archive.writestr("container.sqlite3", raw)
        os.chmod(temp_archive, 0o600)
        # Same-filesystem publication that cannot replace an existing destination.
        os.link(temp_archive, archive_path)
    return manifest


def restore(archive_path, destination):
    """Validate in staging, then create a fresh inert destination; never run archived code."""
    archive_path, destination = safe_path(archive_path), safe_path(destination)
    require_fresh(destination)
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=destination.parent) as staging:
        staging = Path(staging)
        try:
            with zipfile.ZipFile(archive_path) as archive:
                entries = archive.infolist()
                if len(entries) != 2 or {item.filename for item in entries} != MEMBERS:
                    raise Rejected("Missing, duplicate, or unexpected archive paths")
                if any(item.file_size > MAX_ARCHIVE_MEMBER or ((item.external_attr >> 16) & 0o170000) == 0o120000 for item in entries):
                    raise Rejected("Oversized or symlink archive member")
                manifest = json.loads(archive.read("manifest.json"))
                raw = archive.read("container.sqlite3")
            if not isinstance(manifest, dict) or manifest.get("archive_schema") != ARCHIVE_SCHEMA:
                raise Rejected("Unsupported archive schema")
            expected = manifest["database"]
            if expected != {"path": "container.sqlite3", "bytes": len(raw), "sha256": digest(raw)}:
                raise Rejected("Database payload integrity failed")
            db_path = staging / "container.sqlite3"
            db_path.write_bytes(raw)
            os.chmod(db_path, 0o600)
            db = readonly(db_path)
            try:
                contents = inventory(db)
            finally:
                db.close()
            if any(manifest.get(key) != value for key, value in contents.items()):
                raise Rejected("Manifest inventory does not match database payload")
            dependencies = manifest["external_dependencies"]
            if not isinstance(dependencies, list) or any(not isinstance(item, dict) or not item.get("id") for item in dependencies):
                raise Rejected("Invalid external dependency declaration")
        except (KeyError, TypeError, sqlite3.Error, zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise Rejected(f"Malformed snapshot: {type(exc).__name__}") from exc
        result = {"historical_content_restored": True, "included_payloads_complete": True,
                  "declared_dependencies_satisfied": not dependencies,
                  "external_dependencies_resolved": False if dependencies else None,
                  "unresolved_dependencies": dependencies, "historical_grants_activated": False,
                  "execution_started": False, "event_boundary": contents["event_boundary"],
                  "container_id": contents["container_id"]}
        (staging / "snapshot-manifest.json").write_bytes(encoded(manifest))
        (staging / "restoration.json").write_bytes(encoded(result))
        # Validate and prepare before creating a fresh destination. The final receipt
        # is the completion marker; these three moves are not one atomic operation.
        destination.mkdir(mode=0o700)
        created = []
        try:
            for name in ("container.sqlite3", "snapshot-manifest.json", "restoration.json"):
                target = destination / name
                os.replace(staging / name, target)
                created.append(target)
        except OSError:
            for target in reversed(created):
                try:
                    target.unlink()
                except OSError:
                    pass
            try:
                destination.rmdir()
            except OSError:
                pass
            raise
    return result
