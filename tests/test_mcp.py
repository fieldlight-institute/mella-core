# SPDX-License-Identifier: MPL-2.0
"""Subprocess checks for the actual local stdio server, with a temporary store."""
import json
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mella_core.store import Store


class StdioClient:
    def __init__(self, data_dir, *extra):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "mella_core.mcp", "--data-dir", str(data_dir), *extra],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1, cwd=ROOT)
        self.next_id = 0

    def write(self, value):
        self.proc.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def read(self):
        with selectors.DefaultSelector() as poller:
            poller.register(self.proc.stdout, selectors.EVENT_READ)
            if not poller.select(timeout=10):
                raise AssertionError("MCP server did not respond in 10 seconds")
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError("MCP exited before responding: " + self.proc.stderr.read())
        return json.loads(line)

    def request(self, method, params=None):
        self.next_id += 1
        request_id = self.next_id
        self.write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        response = self.read()
        if response.get("id") != request_id:
            raise AssertionError("Unexpected response: " + repr(response))
        return response

    def initialize(self, version="2025-06-18"):
        result = self.request("initialize", {"protocolVersion": version, "capabilities": {},
                                              "clientInfo": {"name": "Mella test agent", "version": "1"}})
        self.write({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def tool(self, name, arguments=None):
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def close(self):
        if not self.proc.stdin.closed:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        diagnostics = self.proc.stderr.read()
        self.proc.stdout.close()
        self.proc.stderr.close()
        return diagnostics


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name).resolve() / "data"
        self.client = StdioClient(self.data_dir)

    def tearDown(self):
        diagnostics = self.client.close()
        self.temp.cleanup()
        self.assertEqual(diagnostics, "", "Unexpected server error diagnostics")

    def initialize(self):
        result = self.client.initialize()["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        return result

    def test_lifecycle_negotiates_supported_version_and_lists_tools(self):
        early = self.client.request("tools/list")
        self.assertEqual(early["error"]["code"], -32002)
        result = self.client.initialize("future-version")["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertIn("tools", result["capabilities"])
        self.assertNotIn("sampling", result["capabilities"])
        self.assertEqual(self.client.request("ping")["result"], {})
        tools = self.client.request("tools/list")["result"]["tools"]
        self.assertEqual({tool["name"] for tool in tools}, {
            "list_records", "read_record", "get_context", "set_context", "capture_note", "create_snapshot"})
        read = next(item for item in tools if item["name"] == "read_record")
        write = next(item for item in tools if item["name"] == "capture_note")
        self.assertTrue(read["annotations"]["readOnlyHint"])
        self.assertFalse(write["annotations"]["readOnlyHint"])
        self.assertFalse(write["annotations"]["idempotentHint"])

    def test_capture_context_snapshot_and_second_client_share_store(self):
        self.initialize()
        result = self.client.tool("capture_note", {"title": "A shared thought", "content": "Mella persists. 🌿"})["result"]
        self.assertFalse(result["isError"])
        record = result["structuredContent"]
        self.assertEqual(record["provider"], "MCP: Mella test agent")
        self.assertEqual(Store(self.data_dir).get_record(record["id"])["content"], "Mella persists. 🌿")
        selected = self.client.tool("set_context", {"record_ids": [record["id"]], "reasoning": "maximum", "title": "Morning"})["result"]["structuredContent"]
        self.assertEqual(selected["record_ids"], [record["id"]])
        self.assertEqual(selected["reasoning"], "maximum")
        self.assertIn("Mella persists.", self.client.tool("get_context")["result"]["structuredContent"]["text"])
        snap = self.client.tool("create_snapshot")["result"]["structuredContent"]
        self.assertGreater(snap["bytes"], 0)
        self.assertTrue(Store(self.data_dir).snapshot_path(snap["id"]).is_file())
        second = StdioClient(self.data_dir)
        try:
            second.initialize()
            detail = second.tool("read_record", {"record_id": record["id"]})["result"]["structuredContent"]
            self.assertEqual(detail["content"], record["content"])
            self.assertEqual(second.tool("get_context")["result"]["structuredContent"]["id"], selected["id"])
        finally:
            self.assertEqual(second.close(), "")

    def test_full_original_other_branch_and_attachment_reference_remain_readable(self):
        self.initialize()
        original = {"id": "source-conversation", "title": "Branched example", "current_node": "chosen",
                    "unfamiliar_metadata": {"retain": True}, "mapping": {
                        "root": {"id": "root", "parent": None, "message": None},
                        "chosen": {"id": "chosen", "parent": "root", "message": {"author": {"role": "user"}, "content": {"parts": ["Active path"]}}},
                        "other": {"id": "other", "parent": "root", "message": {"author": {"role": "assistant"}, "content": {"parts": ["Another branch", {"asset_pointer": "file-service://not-provided"}]}}}}}
        raw_text = " \n" + json.dumps({"conversations": [original], "wrapper": "also retained"}, indent=2) + "\n\n"
        imported = Store(self.data_dir).import_text("test.json", raw_text)
        ident = imported["record_ids"][0]
        detail = self.client.tool("read_record", {"record_id": ident})["result"]["structuredContent"]
        self.assertNotIn("Another branch", detail["content"])
        self.assertEqual(detail["metadata"]["original"], original)
        self.assertEqual(detail["metadata"]["attachments"][0]["status"], "referenced_only")
        raw = self.client.tool("read_record", {"record_id": detail["metadata"]["source"]["id"]})["result"]["structuredContent"]
        self.assertEqual(raw["content"], raw_text)
        resource = self.client.request("resources/read", {"uri": "mella://records/" + ident})["result"]["contents"][0]
        self.assertEqual(json.loads(resource["text"])["metadata"]["original"], original)

    def test_resources_and_library_search_pagination(self):
        self.initialize()
        store = Store(self.data_dir)
        for index in range(3):
            store.add_note("Match " + str(index), "plain content")
        store.add_note("Different", "Something else")
        deep = store.add_note("Long record", "A" * 400 + " hidden keyword ")
        deep_search = self.client.tool("list_records", {"query": "hidden keyword"})["result"]["structuredContent"]
        self.assertEqual([item["id"] for item in deep_search["records"]], [deep["id"]])
        found = self.client.tool("list_records", {"query": "match", "limit": 2})["result"]["structuredContent"]
        self.assertEqual(found["total"], 3)
        self.assertEqual(len(found["records"]), 2)
        self.assertEqual(found["next_offset"], 2)
        second = self.client.tool("list_records", {"query": "match", "limit": 2, "offset": 2})["result"]["structuredContent"]
        self.assertEqual(len(second["records"]), 1)
        self.assertIsNone(second["next_offset"])
        self.assertEqual(len(self.client.request("resources/list")["result"]["resources"]), 2)
        self.assertIn("resourceTemplates", self.client.request("resources/templates/list")["result"])
        for uri in ("mella://library", "mella://context"):
            self.assertEqual(self.client.request("resources/read", {"uri": uri})["result"]["contents"][0]["uri"], uri)

    def test_invalid_requests_do_not_mutate_store(self):
        self.initialize()
        for name, args in (("capture_note", {"title": "missing content"}),
                           ("set_context", {"record_ids": [], "reasoning": "unbounded"}),
                           ("set_context", {"record_ids": ["x", "x"]}),
                           ("list_records", {"limit": True}),
                           ("list_records", {"limit": 201}),
                           ("capture_note", {"title": "x", "content": "x", "path": "/tmp/no"}),
                           ("unknown_tool", {})):
            with self.subTest(name=name, args=args):
                response = self.client.tool(name, args)
                self.assertEqual(response["error"]["code"], -32602)
        self.assertEqual(Store(self.data_dir).state()["records"], [])
        self.assertEqual(self.client.request("shell/execute", {"command": "whoami"})["error"]["code"], -32601)
        self.assertEqual(self.client.request("resources/read", {"uri": "file:///etc/passwd"})["error"]["code"], -32002)

    def test_unknown_record_is_tool_error_and_notifications_have_no_side_effect(self):
        self.initialize()
        missing = self.client.tool("read_record", {"record_id": "does-not-exist"})["result"]
        self.assertTrue(missing["isError"])
        self.client.write({"jsonrpc": "2.0", "method": "tools/call", "params": {
            "name": "capture_note", "arguments": {"title": "Not a request", "content": "Must not be written"}}})
        self.assertEqual(self.client.request("ping")["result"], {})
        self.assertEqual(Store(self.data_dir).state()["records"], [])

    def test_invalid_json_recovers_without_stdout_noise(self):
        self.client.proc.stdin.write("{broken json}\n")
        self.client.proc.stdin.flush()
        error = self.client.read()
        self.assertEqual(error["error"]["code"], -32700)
        self.client.write({"jsonrpc": "2.0", "id": {"not": "an ID"}, "method": "ping"})
        invalid = self.client.read()
        self.assertIsNone(invalid["id"])
        self.assertEqual(invalid["error"]["code"], -32600)
        self.initialize()
        self.assertEqual(self.client.request("ping")["result"], {})

    def test_read_only_connection_excludes_mutations_and_reads_pinned_version(self):
        self.initialize()
        store = Store(self.data_dir)
        note = store.add_note("Direction", "Version one")
        revised = store.revise_note(note["id"], "Direction", "Version two", expected_version=1)
        reader = StdioClient(self.data_dir, "--read-only")
        try:
            reader.initialize()
            names = {tool["name"] for tool in reader.request("tools/list")["result"]["tools"]}
            self.assertEqual(names, {"list_records", "read_record", "get_context"})
            first = reader.tool("read_record", {"record_id": note["id"], "version": 1})["result"]["structuredContent"]
            self.assertEqual(first, note)
            rejected = reader.tool("capture_note", {"title": "Forbidden", "content": "Not saved"})
            self.assertEqual(rejected["error"]["code"], -32602)
            self.assertEqual(len(store.state()["records"]), 1)
            self.assertEqual(store.get_record(note["id"]), revised)
        finally:
            self.assertEqual(reader.close(), "")

    def test_explicit_path_required_and_read_only_cannot_create_container(self):
        absent = Path(self.temp.name).resolve() / "must-not-create"
        missing_path = subprocess.run([sys.executable, "-m", "mella_core.mcp"],
                                      cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(missing_path.returncode, 2)
        self.assertEqual(missing_path.stdout, "")
        result = subprocess.run([sys.executable, "-m", "mella_core.mcp", "--data-dir", str(absent), "--read-only"],
                                cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertFalse(absent.exists())


if __name__ == "__main__":
    unittest.main()
