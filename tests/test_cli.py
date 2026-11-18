# SPDX-License-Identifier: MPL-2.0
import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mella_core.cli import export_record, reader_html
from mella_core.store import Store


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / "data"

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, "-m", "mella_core", "--data-dir", str(self.data),
                                 *map(str, args)], text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0 if ok else 1, result.stdout + result.stderr)
        return result

    def test_cli_full_preservation_cycle_and_no_overwrites(self):
        self.cli("list", ok=False)
        self.assertFalse(self.data.exists())
        self.cli("init")
        self.cli("init", ok=False)
        source = self.root / "source.md"
        original = "# A record\n\nUnicode π, exact spacing.\n\n"
        source.write_text(original, encoding="utf-8")
        imported = json.loads(self.cli("import", source).stdout)
        record_id = imported["record_ids"][0]
        self.assertEqual(self.cli("read", record_id, "--format", "original").stdout, original)
        snapshot = json.loads(self.cli("snapshot").stdout)
        self.assertTrue(json.loads(self.cli("verify", snapshot["path"]).stdout)["verified"])
        dest = self.root / "recovered"
        self.cli("restore", snapshot["path"], "--to", dest)
        self.assertEqual(Store(dest).get_record(record_id)["content"], original)
        self.cli("restore", snapshot["path"], "--to", dest, ok=False)
        output = self.root / "record.json"
        self.cli("export", "--output", output)
        before = output.read_bytes()
        self.cli("export", "--output", output, ok=False)
        self.assertEqual(output.read_bytes(), before)
        self.cli("reader", "--output", self.root / "reader.html")

    def test_export_retains_all_revisions_and_exact_bytes(self):
        store = Store(self.data)
        note = store.add_note("Plan", "Before")
        store.revise_note(note["id"], "Plan", "After", expected_version=1)
        exported = export_record(store)
        versions = [r for r in exported["records"] if r["record_id"] == note["id"]]
        self.assertEqual([r["version"] for r in versions], [1, 2])
        self.assertEqual([r["value"]["content"] for r in versions], ["Before", "After"])
        for record in versions:
            self.assertEqual(json.loads(base64.b64decode(record["raw_base64"])), record["value"])

    def test_offline_reader_escapes_imported_markup_and_has_no_network_assets(self):
        store = Store(self.data)
        store.add_note('<img src="https://example.invalid/x">', '<script>alert("x")</script>')
        rendered = reader_html(export_record(store))
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("default-src 'none'", rendered)
        self.assertNotIn("<link", rendered)

    def test_bad_snapshot_is_a_clean_error(self):
        archive = self.root / "bad.zip"
        archive.write_bytes(b"not a snapshot")
        result = self.cli("verify", archive, ok=False)
        self.assertNotIn("Traceback", result.stderr)

    def test_reader_preserves_non_object_and_binary_records(self):
        store = Store(self.data)
        payloads = [b'["an unfamiliar record"]', b'42', b'"a scalar"', b'\xff\x00\x01']
        with store._open() as container:
            for index, raw in enumerate(payloads):
                container.add_record("custom_" + str(index), "opaque", raw)
        exported = export_record(store)
        rendered = reader_html(exported)
        for index, raw in enumerate(payloads):
            self.assertIn("custom_" + str(index), rendered)
            self.assertIn(base64.b64encode(raw).decode("ascii"), rendered)


if __name__ == "__main__":
    unittest.main()
