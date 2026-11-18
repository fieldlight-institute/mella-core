# SPDX-License-Identifier: MPL-2.0
"""Preservation acceptance checks, independent of either model provider."""
import json
import concurrent.futures
from pathlib import Path
import tempfile
import threading
import unittest

from mella_core.conversation_store import ConversationStore
from mella_core.store import Store, StoreError


class ConversationContinuityTests(unittest.TestCase):
    def test_concurrent_starts_accept_one_turn_without_an_external_manager_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "data"
            original = ConversationStore(Store(root))
            ident = original.create("Concurrent callers")["id"]
            barrier = threading.Barrier(2)

            def start(index):
                conversations = ConversationStore(Store(root))
                barrier.wait(timeout=5)
                try:
                    return conversations.start(ident, "Task " + str(index), "test-provider")
                except StoreError:
                    return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(start, range(2)))
            self.assertEqual(sum(result is not None for result in results), 1)
            self.assertEqual(len(original.get(ident)["messages"]), 1)
            self.assertEqual(original.get(ident)["cursor"], 1)

    def test_snapshot_keeps_both_participants_and_unfinished_received_work(self):
        with tempfile.TemporaryDirectory(prefix="mella-continuity-") as temporary:
            store = Store(Path(temporary).resolve() / "data")
            source = store.add_note("Author direction", "The conversation belongs to Mella.")
            conversations = ConversationStore(store)
            conversation = conversations.create("Across participants")
            ident = conversation["id"]
            first = conversations.start(ident, "Use the author direction.", "codex")
            conversations.append(ident, {"type": "session", "turn_id": first,
                "provider": "codex", "session_id": "fixture-codex-session"})
            conversations.append(ident, {"type": "message", "turn_id": first,
                "provider": "codex", "item_id": "fixture-first-answer",
                "text": "The record stays here as participants change.",
                "raw": {"unknown_future_field": {"source_record": source["id"], "value": "π 🌿"}}})
            conversations.append(ident, {"type": "turn_finished", "turn_id": first,
                "provider": "codex", "status": "completed"})
            conversations.checkpoint(ident)

            second = conversations.start(ident, "Claude, build on that.", "claude")
            conversations.append(ident, {"type": "session", "turn_id": second,
                "provider": "claude", "session_id": "fixture-claude-session"})
            received = conversations.append(ident, {"type": "text_delta", "turn_id": second,
                "provider": "claude", "item_id": "fixture-second-answer",
                "delta": "Keep the provider attribution, too.",
                "raw": {"type": "stream_event", "future_metadata": ["retained", 37]}})
            before = conversations.get(ident, internal=True)
            self.assertEqual(before["status"], "running")
            self.assertEqual([m["provider"] for m in before["messages"]],
                             ["You", "codex", "You", "claude"])

            snapshot = store.create_snapshot()
            restored = store.restore_snapshot(snapshot["id"])
            recovered_store = Store(restored["path"])
            recovered = ConversationStore(recovered_store)
            self.assertEqual(recovered.get(ident, internal=True), before)
            event_id = "agent_event_{}_{}".format(ident, str(received["seq"]).zfill(12))
            raw_event = json.loads(recovered_store.get_record(event_id)["content"])
            self.assertEqual(raw_event["raw"]["future_metadata"], ["retained", 37])
            self.assertEqual(recovered_store.get_record(source["id"])["content"],
                             "The conversation belongs to Mella.")

            recovered.recover_interrupted()
            after = recovered.get(ident, internal=True)
            self.assertEqual(after["status"], "interrupted")
            self.assertEqual([m["content"] for m in after["messages"]],
                             [m["content"] for m in before["messages"]])
            self.assertEqual(after["sessions"]["codex"]["id"], "fixture-codex-session")
            self.assertEqual(after["sessions"]["claude"]["id"], "fixture-claude-session")
            # Recovery must not manufacture a new user or assistant utterance.
            self.assertEqual(len(after["messages"]), 4)
            self.assertEqual(conversations.get(ident)["status"], "running")
            recovered.start(ident, "Continue from the preserved partial response.", "claude")
            self.assertEqual(len(recovered.get(ident)["messages"]), 5)

    def test_ui_event_view_keeps_provider_internals_out_of_display_payload(self):
        with tempfile.TemporaryDirectory(prefix="mella-events-") as temporary:
            conversations = ConversationStore(Store(Path(temporary).resolve() / "data"))
            ident = conversations.create()["id"]
            turn = conversations.start(ident, "A task", "codex")
            conversations.append(ident, {"type": "metadata", "turn_id": turn,
                "provider": "codex", "raw": {"provider_specific": "retained internally"}})
            conversations.append(ident, {"type": "message", "turn_id": turn,
                "provider": "codex", "item_id": "answer", "text": "Visible answer",
                "raw": {"provider_specific": "also retained"}})
            result = conversations.events(ident)
            self.assertNotIn("provider_specific", json.dumps(result))
            self.assertEqual(result["conversation"]["messages"][-1]["content"], "Visible answer")
            self.assertGreater(result["cursor"], len(result["events"]))


if __name__ == "__main__":
    unittest.main()
