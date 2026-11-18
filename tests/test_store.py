# SPDX-License-Identifier: MPL-2.0
import concurrent.futures
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mella_core import preservation
from mella_core.store import Store, StoreError


def conversation_fixture():
    return {"id": "original-id", "title": "Branch preservation", "create_time": 1700000000,
            "current_node": "chosen", "unknown_top_level": {"future": [1, "λ", None]},
            "mapping": {
                "root": {"id": "root", "parent": None, "children": ["user"], "message": None},
                "user": {"id": "user", "parent": "root", "children": ["other", "chosen"],
                         "message": {"id": "m-user", "author": {"role": "user"}, "content": {"parts": ["A question"]}}},
                "other": {"id": "other", "parent": "user", "children": [], "hidden_flag": "retain",
                          "message": {"id": "m-other", "author": {"role": "assistant"},
                                      "content": {"parts": ["Alternate response"]}, "metadata": {"model_slug": "old-model"}}},
                "chosen": {"id": "chosen", "parent": "user", "children": [],
                           "message": {"id": "m-chosen", "author": {"role": "assistant"}, "create_time": 1700000010,
                                       "content": {"parts": ["Selected response", {"asset_pointer": "file-service://missing-photo"}]},
                                       "metadata": {"future_setting": {"enabled": True}}}}}}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / "library"
        self.store = Store(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_empty_and_persistence_across_instances(self):
        state = self.store.state()
        self.assertEqual(state["records"], [])
        self.assertEqual(state["context"]["text"], "")
        note = self.store.add_note("Private note", "Keep exact\nUnicode Ω.")
        another = Store(self.root)
        self.assertEqual(another.get_record(note["id"])["content"], "Keep exact\nUnicode Ω.")
        self.assertEqual(another.state()["container"], state["container"])
        self.assertEqual(another.state()["records"][0]["id"], note["id"])

    def test_import_preserves_original_unknown_metadata_and_all_branches(self):
        original = conversation_fixture()
        wrapper = {"conversations": [original], "unknown_export_field": {"exact": "kept"}}
        text = " \n" + json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n"
        result = self.store.import_text("conversations.json", text)
        self.assertEqual(result["imported"], 1)
        self.assertIn("not provided or copied", result["warnings"][0])
        record = self.store.get_record(result["record_ids"][0])
        self.assertEqual(record["metadata"]["original"], original)
        self.assertEqual(record["branch_count"], 2)
        self.assertEqual(record["attachment_count"], 1)
        self.assertEqual(record["provider"], "ChatGPT")
        self.assertTrue(record["created_at"].startswith("2023-11-14"))
        self.assertNotIn("Alternate response", record["content"])
        self.assertIn("Selected response", record["content"])
        self.assertEqual([m["id"] for m in record["messages"]], ["m-user", "m-chosen"])
        self.assertEqual(self.store.get_record(record["metadata"]["source"]["id"])["content"], text)

    def test_generic_provider_not_misrepresented_and_markdown_exact(self):
        result = self.store.import_text("other.json", json.dumps({"provider": "Claude", "conversations": [{"title": "Generic", "messages": [{"role": "assistant", "content": "Hello"}]}]}))
        record = self.store.get_record(result["record_ids"][0])
        self.assertEqual(record["provider"], "Claude")
        result = self.store.import_text("unlabeled.json", json.dumps({"messages": [{"role": "user", "content": "Hello"}]}))
        self.assertEqual(self.store.get_record(result["record_ids"][0])["provider"], "Imported")
        content = "# My title\n\nAn exact café.  \n"
        result = self.store.import_text("../anything.md", content)
        record = self.store.get_record(result["record_ids"][0])
        self.assertEqual(record["title"], "My title")
        self.assertEqual(record["content"], content)
        self.assertEqual(record["metadata"]["source"]["filename"], "anything.md")

    def test_context_order_pinned_version_hash_and_history(self):
        first = self.store.add_note("First", "Version one")
        second = self.store.add_note("Second", "Second body", provider="Claude")
        context = self.store.set_context([second["id"], first["id"]], "maximum", "A deliberate context")
        self.assertEqual(context["record_ids"], [second["id"], first["id"]])
        self.assertLess(context["text"].index("Second body"), context["text"].index("Version one"))
        self.assertIn("Provider: Claude", context["text"])
        self.assertIn("Pinned SHA-256:", context["text"])
        self.assertIn("no provider has been invoked", context["text"])
        with self.store._open() as container:
            payload = json.loads(container.db.execute("SELECT raw FROM records WHERE record_id=?", (first["id"],)).fetchone()[0])
            payload["content"] = "Version two"
            container.add_record(first["id"], "note", preservation.encoded(payload))
        self.assertEqual(self.store.get_record(first["id"])["version"], 2)
        self.assertEqual(self.store.get_context(), context)
        next_context = self.store.set_context([first["id"]], "deep")
        self.assertNotEqual(next_context["id"], context["id"])
        self.assertIn("Version two", next_context["text"])
        with self.store._open() as container:
            self.assertEqual(container.db.execute("SELECT COUNT(*) FROM contexts").fetchone()[0], 2)
            row = container.db.execute("SELECT raw FROM records WHERE record_id=? AND version=2", (first["id"],)).fetchone()
            changed = json.loads(row[0]); changed["content"] = "Tampered"
            raw = preservation.encoded(changed)
            with container.db:
                container.db.execute("UPDATE records SET raw=?,sha256=? WHERE record_id=? AND version=2", (raw, preservation.digest(raw), first["id"]))
        with self.assertRaisesRegex(StoreError, "fingerprint"):
            self.store.get_context()

    def test_markdown_preview_hides_frontmatter_but_keeps_exact_source(self):
        content = "---\r\ntitle: Private working note\r\ntags: [research, draft]\r\n---\r\n\r\n## Human heading\r\n\r\nA human thought, café Ω.  \r\n"
        result = self.store.import_text("working-note.md", content)
        record = self.store.get_record(result["record_ids"][0])
        self.assertEqual(record["preview"], "Human heading A human thought, café Ω.")
        self.assertEqual(record["content"].encode("utf-8"), content.encode("utf-8"))
        source = self.store.get_record(record["metadata"]["source"]["id"])
        self.assertEqual(source["content"].encode("utf-8"), content.encode("utf-8"))
        self.assertEqual(source["metadata"]["sha256"], preservation.digest(content.encode("utf-8")))
        self.assertEqual(self.store.state()["records"][0]["preview"], record["preview"])
        self.assertEqual(Store(self.root).get_record(record["id"])["content"], content)

    def test_snapshot_restores_complete_context_sources_and_separate_copy(self):
        original = conversation_fixture()
        text = json.dumps([original], indent=3)
        imported = self.store.import_text("conversations.json", text)
        record = self.store.get_record(imported["record_ids"][0])
        context = self.store.set_context(imported["record_ids"], "deep", "Saved choice")
        snapshot = self.store.create_snapshot()
        self.assertEqual(snapshot["record_count"], 1)
        with zipfile.ZipFile(self.store.snapshot_path(snapshot["id"])) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["external_dependencies"][0]["count"], 1)
        late = self.store.add_note("After snapshot", "Must stay live")
        restored = self.store.restore_snapshot(snapshot["id"])
        self.assertTrue(restored["verified"])
        copied = Store(restored["path"])
        self.assertEqual(copied.get_context(), context)
        self.assertEqual(copied.get_record(record["id"])["metadata"]["original"], original)
        self.assertEqual(copied.get_record(record["metadata"]["source"]["id"])["content"], text)
        self.assertEqual(len(copied.state()["records"]), 1)
        self.assertEqual(len(self.store.state()["records"]), 2)
        self.assertEqual(self.store.get_record(late["id"])["content"], "Must stay live")
        with patch("mella_core.store._id", return_value=restored["id"]):
            with self.assertRaisesRegex(preservation.Rejected, "already exists"):
                self.store.restore_snapshot(snapshot["id"])
        self.assertEqual(copied.get_context(), context)

    def test_rejects_bad_input_atomically_and_tampered_snapshot(self):
        for filename, text in [("bad.json", "{broken"), ("wrong.json", '{"not": "a conversation"}'), ("empty.json", "[]")]:
            with self.assertRaises(StoreError):
                self.store.import_text(filename, text)
        with self.assertRaises(StoreError):
            self.store.import_text("partial.json", json.dumps([conversation_fixture(), "bad entry"]))
        self.assertEqual(self.store.state()["records"], [])
        with self.assertRaises(KeyError):
            self.store.set_context(["missing"])
        with self.assertRaises(StoreError):
            self.store.set_context([], reasoning=[])
        note = self.store.add_note("Existing", "Body")
        with self.assertRaises(StoreError):
            self.store.set_context([note["id"], note["id"]])
        snapshot = self.store.create_snapshot()
        path = self.store.snapshot_path(snapshot["id"])
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr("../escape", b"not allowed")
        with self.assertRaises(preservation.Rejected):
            self.store.restore_snapshot(snapshot["id"])
        self.assertEqual(list(self.store.restores.iterdir()), [])
        with self.assertRaises(KeyError):
            self.store.snapshot_path("../../container.sqlite3")

    def test_concurrent_clients_and_process_initialization(self):
        def add(index):
            return Store(self.root).add_note(str(index), "Concurrent content")["id"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            ids = list(pool.map(add, range(18)))
        self.assertEqual(len(set(ids)), 18)
        self.assertEqual(len(self.store.state()["records"]), 18)
        fresh = Path(self.temp.name).resolve() / "shared-process-start"
        script = "from mella_core.store import Store; import sys; s=Store(sys.argv[1]); s.add_note('process','body')"
        procs = [subprocess.Popen([sys.executable, "-c", script, str(fresh)], cwd=str(Path(__file__).resolve().parents[1]), stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(5)]
        for proc in procs:
            _, err = proc.communicate(timeout=20)
            self.assertEqual(proc.returncode, 0, err.decode())
        self.assertEqual(len(Store(fresh).state()["records"]), 5)
        with self.store._open() as container:
            self.assertEqual(len(preservation.inventory(container.db)["records"]), 18)

    def test_search_uses_full_active_content(self):
        note = self.store.add_note("A note", "x" * 1000 + " deep phrase Ω")
        self.assertEqual(self.store.search_records("DEEP PHRASE Ω")[0]["id"], note["id"])
        self.assertEqual(self.store.search_records("missing"), [])
        imported = self.store.import_text("branches.json", json.dumps(conversation_fixture()))
        self.assertEqual(self.store.search_records("Alternate response"), [])
        self.assertEqual(self.store.search_records("Selected response")[0]["id"], imported["record_ids"][0])
        with self.assertRaises(StoreError):
            self.store.search_records("x" * 1001)

    def test_note_revision_preserves_identity_history_and_pinned_context(self):
        first = self.store.add_note("A project decision", "Original direction", provider="Author")
        selected = self.store.set_context([first["id"]])
        revised = self.store.revise_note(first["id"], "A project decision", "Corrected direction",
            metadata={"correction_of_version": 1, "interpretation": False}, expected_version=1)
        self.assertEqual(revised["id"], first["id"])
        self.assertEqual(revised["version"], 2)
        self.assertEqual(revised["provider"], "Author")
        self.assertEqual(revised["created_at"], first["created_at"])
        self.assertEqual(revised["metadata"]["origin"], "capture")
        self.assertEqual(revised["metadata"]["correction_of_version"], 1)
        self.assertEqual(self.store.get_record(first["id"], 1), first)
        self.assertEqual([v["version"] for v in self.store.versions(first["id"])], [1, 2])
        self.assertEqual(self.store.get_context(), selected)
        with self.assertRaisesRegex(StoreError, "changed"):
            self.store.revise_note(first["id"], "Stale", "Must not replace", expected_version=1)
        self.assertEqual(self.store.get_record(first["id"]), revised)
        snap = self.store.create_snapshot()
        restored = Store(self.store.restore_snapshot(snap["id"])["path"])
        self.assertEqual(restored.versions(first["id"]), self.store.versions(first["id"]))
        self.assertEqual(restored.get_record(first["id"], 2), revised)

    def test_revisions_reject_imports_and_invalid_metadata_without_writing(self):
        imported = self.store.import_text("source.md", "# Original source\nMust remain exact.")
        for ident in [imported["record_ids"][0], self.store.get_record(imported["record_ids"][0])["metadata"]["source"]["id"]]:
            with self.assertRaisesRegex(StoreError, "Only local notes"):
                self.store.revise_note(ident, "Replacement", "No")
        note = self.store.add_note("Note", "Retain")
        for metadata in ([], {"bad": float("nan")}, {"bad": object()}):
            with self.assertRaises(StoreError):
                self.store.revise_note(note["id"], "Bad", "No", metadata=metadata)
        for version in (True, 0, -1, "1"):
            with self.assertRaises(StoreError):
                self.store.get_record(note["id"], version)
        with self.assertRaises(KeyError):
            self.store.get_record(note["id"], 99)
        with self.assertRaises(KeyError):
            self.store.versions("absent")
        self.assertEqual(self.store.get_record(note["id"]), note)


if __name__ == "__main__":
    unittest.main()
