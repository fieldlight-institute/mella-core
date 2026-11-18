# SPDX-License-Identifier: MPL-2.0
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

from mella_core.preservation import Container, Rejected, capture, digest, encoded, restore
from mella_core import preservation as container_proof


def populate(container, *, large=True):
    export = {
        "id": "synthetic-conversation", "conversation_id": "synthetic-conversation",
        "title": "Synthetic project formation", "create_time": 1000.0,
        "update_time": 1010.0, "current_node": "answer-b", "default_model_slug": "synthetic-model",
        "mapping": {
            "root": {"id": "root", "parent": None, "message": None},
            "question": {"id": "question", "parent": "root", "message": {
                "id": "question", "create_time": 1001.0, "author": {"role": "user", "name": ""},
                "content": {"content_type": "text", "parts": ["Keep the selected working context compact."]},
                "metadata": {"unrecognized_provider_field": {"future": [1, None, "exactly retained"]}}}},
            "answer-a": {"id": "answer-a", "parent": "question", "message": {
                "id": "answer-a", "author": {"role": "assistant", "name": ""},
                "content": {"parts": ["Alternative branch A."]}, "metadata": {}}},
            "answer-b": {"id": "answer-b", "parent": "question", "message": {
                "id": "answer-b", "create_time": 1010.0, "author": {"role": "assistant", "name": ""},
                "content": {"parts": ["Chosen branch B."], "attachment_record_id": "synthetic-attachment"},
                "metadata": {"unknown_flag": True}}},
        },
        "unknown_top_level": {"preserve": "whitespace and all source fields"},
    }
    exchange = {
        "schema": "fieldlight.proposed-agent-exchange/0", "provenance": "synthetic Fieldlight-observed fixture",
        "participants": [{"id": "agent-planner", "role": "agent"}, {"id": "agent-reviewer", "role": "agent"}],
        "task_id": "synthetic-task", "run_id": "synthetic-run",
        "events": [{"from": "agent-planner", "to": "agent-reviewer", "content": "Check the selected context."},
                   {"from": "agent-reviewer", "to": "agent-planner", "content": "The selected versions are explicit."},
                   {"actor": "agent-reviewer", "type": "tool_action", "tool": "synthetic-inspector", "result": {"ok": True}}],
        "provider_reported_metadata": {}, "fieldlight_observed": {"source": "synthetic fixture generator"},
    }
    raw = {
        "human-agent": (json.dumps(export, indent=2) + "\n").encode(),
        "agent-agent": (json.dumps(exchange, indent=2) + "\n").encode(),
        "synthetic-attachment": b"SYNTHETIC ATTACHMENT\x00\xff\x01\x00\n",
        "historical-grants": b'{"synthetic_historical_allowed_record_ids":["human-agent","agent-agent"]}',
    }
    refs = {}
    for ident, payload in raw.items():
        kind = "attachment" if ident == "synthetic-attachment" else "historical_permissions" if ident == "historical-grants" else "raw_conversation"
        refs[ident] = container.add_record(ident, kind, payload)
    container.add_context("formation-v1", [refs["human-agent"], refs["agent-agent"]], {"requested_effort": "high", "source": "explicit synthetic owner selection"})
    allowed = {"human-agent", "agent-agent"}
    before = container.load_context("formation-v1", current_allowed_record_ids=allowed)
    if large:
        raw["unloaded-large"] = b'{"synthetic":true,"content":"' + b"unselected synthetic history; " * 40000 + b'"}\n'
        refs["unloaded-large"] = container.add_record("unloaded-large", "raw_conversation", raw["unloaded-large"])
    return {"raw": raw, "refs": refs, "allowed": allowed, "before": before}


class ContainerProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".test-", )
        self.root = Path(self.temp.name).resolve()
        self.container = Container(self.root / "live.sqlite3", create=True, container_id="synthetic-tests")
        self.fixture = populate(self.container)
        self.archive = self.root / "snapshot.zip"

    def tearDown(self):
        self.container.close()
        self.temp.cleanup()

    def snapshot(self, **kwargs):
        return capture(self.container, self.archive, **kwargs)

    def rewrite_archive(self, name, transform):
        with zipfile.ZipFile(self.archive) as archive:
            members = [(item.filename, archive.read(item.filename)) for item in archive.infolist()]
        target = self.root / name
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for member, raw in transform(members):
                    archive.writestr(member, raw)
        return target

    def test_roundtrip_preserves_raw_branches_unknown_fields_and_binary_attachment(self):
        manifest = self.snapshot()
        result = restore(self.archive, self.root / "restored")
        self.assertFalse(result["execution_started"])
        self.assertFalse(result["historical_grants_activated"])
        with Container(self.root / "restored/container.sqlite3") as restored:
            for ident, raw in self.fixture["raw"].items():
                actual = restored.db.execute("SELECT raw FROM records WHERE record_id=? AND version=1", (ident,)).fetchone()[0]
                self.assertEqual(actual, raw)
            payload = json.loads(restored.db.execute("SELECT raw FROM records WHERE record_id='human-agent'").fetchone()[0])
            self.assertEqual(payload["current_node"], "answer-b")
            self.assertEqual(payload["mapping"]["answer-a"]["parent"], payload["mapping"]["answer-b"]["parent"])
            self.assertNotIn("create_time", payload["mapping"]["answer-a"]["message"])
            self.assertIn("unknown_top_level", payload)
            self.assertEqual(result["event_boundary"], manifest["event_boundary"])

    def test_retention_access_selection_and_reasoning_remain_independent(self):
        before = self.fixture["before"]
        after = self.container.load_context("formation-v1", current_allowed_record_ids=set(self.fixture["raw"]))
        self.assertEqual(before, after)
        self.assertGreater(len(self.fixture["raw"]["unloaded-large"]), after["loaded_bytes"] * 100)
        self.snapshot()
        restore(self.archive, self.root / "restored")
        with Container(self.root / "restored/container.sqlite3") as restored:
            self.assertEqual(after, restored.load_context("formation-v1", current_allowed_record_ids=self.fixture["allowed"]))
            with self.assertRaisesRegex(Rejected, "Current access excludes"):
                restored.load_context("formation-v1", current_allowed_record_ids={"human-agent"})

    def test_revision_retains_old_bytes_and_context_pins_exact_version(self):
        new = self.container.add_record("human-agent", "raw_conversation", b'{"synthetic":"new revision"}')
        self.assertEqual(new["version"], 2)
        loaded = self.container.load_context("formation-v1", current_allowed_record_ids=self.fixture["allowed"])
        self.assertEqual(loaded["records"][0]["raw"], self.fixture["raw"]["human-agent"])
        self.assertEqual(loaded["records"][0]["version"], 1)
        manifest = self.snapshot()
        self.assertEqual(len([r for r in manifest["records"] if r["record_id"] == "human-agent"]), 2)

    def test_backup_excludes_another_connection_uncommitted_write(self):
        boundary = self.container.db.execute("SELECT MAX(seq) FROM events").fetchone()[0]
        other = sqlite3.connect(self.container.path)
        try:
            other.execute("BEGIN IMMEDIATE")
            raw = b"uncommitted synthetic data"
            other.execute("INSERT INTO records VALUES (?,?,?,?,?)", ("pending", 1, "synthetic", digest(raw), raw))
            other.execute("INSERT INTO events(body) VALUES (?)", (encoded({"type": "record_added", "record_id": "pending", "version": 1, "sha256": digest(raw)}),))
            manifest = self.snapshot()
            self.assertEqual(manifest["event_boundary"], boundary)
            self.assertNotIn("pending", [item["record_id"] for item in manifest["records"]])
            other.commit()
        finally:
            other.close()
        restore(self.archive, self.root / "restored")
        with Container(self.root / "restored/container.sqlite3") as restored:
            self.assertEqual(restored.db.execute("SELECT COUNT(*) FROM records WHERE record_id='pending'").fetchone()[0], 0)

    def test_capture_rejects_own_uncommitted_transaction(self):
        self.container.db.execute("BEGIN")
        with self.assertRaisesRegex(Rejected, "uncommitted"):
            self.snapshot()
        self.container.db.rollback()
        self.assertFalse(self.archive.exists())

    def test_declared_dependencies_remain_unresolved(self):
        dependencies = [{"id": "synthetic-runtime", "included": False}]
        self.snapshot(external_dependencies=dependencies)
        result = restore(self.archive, self.root / "restored")
        self.assertTrue(result["historical_content_restored"])
        self.assertFalse(result["declared_dependencies_satisfied"])
        self.assertEqual(result["unresolved_dependencies"], dependencies)

    def test_corrupted_database_payload_rejected_before_destination_creation(self):
        self.snapshot()
        corrupt = self.rewrite_archive("corrupt.zip", lambda members: [(name, raw + b"damage" if name == "container.sqlite3" else raw) for name, raw in members])
        with self.assertRaisesRegex(Rejected, "integrity"):
            restore(corrupt, self.root / "rejected")
        self.assertFalse((self.root / "rejected").exists())

    def test_missing_payload_duplicate_and_unsafe_paths_rejected(self):
        self.snapshot()
        transforms = [
            lambda members: [item for item in members if item[0] != "container.sqlite3"],
            lambda members: members + [members[0]],
            lambda members: [("../container.sqlite3" if name == "container.sqlite3" else name, raw) for name, raw in members],
        ]
        for i, transform in enumerate(transforms):
            with self.subTest(case=i):
                invalid = self.rewrite_archive(f"invalid-{i}.zip", transform)
                with self.assertRaisesRegex(Rejected, "paths"):
                    restore(invalid, self.root / f"rejected-{i}")
                self.assertFalse((self.root / f"rejected-{i}").exists())

    def test_scalar_or_list_manifest_rejected_before_destination_creation(self):
        self.snapshot()
        for index, value in enumerate((None, [], "a manifest", 42)):
            target = self.rewrite_archive(f"manifest-{index}.zip", lambda members: [
                (name, encoded(value) if name == "manifest.json" else raw) for name, raw in members])
            destination = self.root / f"rejected-manifest-{index}"
            with self.assertRaises(Rejected):
                restore(target, destination)
            self.assertFalse(destination.exists())

    def test_rehashed_database_missing_record_still_fails_manifest_or_event_checks(self):
        self.snapshot()
        def mutate(members):
            values = dict(members)
            dbpath = self.root / "tampered.sqlite3"
            dbpath.write_bytes(values["container.sqlite3"])
            db = sqlite3.connect(dbpath)
            with db:
                db.execute("DELETE FROM records WHERE record_id='synthetic-attachment'")
            db.close()
            raw = dbpath.read_bytes()
            manifest = json.loads(values["manifest.json"])
            manifest["database"].update(bytes=len(raw), sha256=digest(raw))
            return [("manifest.json", encoded(manifest)), ("container.sqlite3", raw)]
        invalid = self.rewrite_archive("missing-record.zip", mutate)
        with self.assertRaises(Rejected):
            restore(invalid, self.root / "rejected")

    def test_existing_archive_and_restore_destination_never_overwritten(self):
        self.snapshot()
        archive_bytes = self.archive.read_bytes()
        with self.assertRaises(Rejected):
            self.snapshot()
        self.assertEqual(self.archive.read_bytes(), archive_bytes)
        destination = self.root / "existing"
        destination.mkdir()
        sentinel = destination / "keep.txt"
        sentinel.write_text("untouched")
        with self.assertRaises(Rejected):
            restore(self.archive, destination)
        self.assertEqual(sentinel.read_text(), "untouched")
        self.assertEqual(list(destination.iterdir()), [sentinel])

    def test_symlink_inputs_and_destinations_rejected(self):
        self.snapshot()
        linked = self.root / "linked.zip"
        linked.symlink_to(self.archive)
        with self.assertRaisesRegex(Rejected, "Symlink"):
            restore(linked, self.root / "rejected")
        linked_parent = self.root / "linked-parent"
        linked_parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(Rejected, "Symlink"):
            restore(self.archive, linked_parent / "rejected")

    def test_capture_never_publishes_members_that_restore_would_reject_for_size(self):
        with patch("mella_core.preservation.MAX_ARCHIVE_MEMBER", 100):
            with self.assertRaisesRegex(Rejected, "size limit"):
                self.snapshot()
        self.assertFalse(self.archive.exists())
        # Exercise the manifest bound independently of the database bound.
        with patch("mella_core.preservation.MAX_ARCHIVE_MEMBER", 2_000_000):
            with self.assertRaisesRegex(Rejected, "size limit"):
                self.snapshot(external_dependencies=[{"id": "synthetic-large-declaration", "description": "x" * 2_000_000}])
        self.assertFalse(self.archive.exists())

    def test_restore_publish_failure_removes_only_its_new_destination(self):
        self.snapshot()
        replace = container_proof.os.replace
        def fail_second_move(source, target):
            if Path(source).name == "snapshot-manifest.json":
                raise OSError("synthetic publish failure")
            return replace(source, target)
        with patch("mella_core.preservation.os.replace", side_effect=fail_second_move):
            with self.assertRaisesRegex(OSError, "synthetic publish failure"):
                restore(self.archive, self.root / "rejected")
        self.assertFalse((self.root / "rejected").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
