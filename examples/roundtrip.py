# SPDX-License-Identifier: MPL-2.0
"""Run with python -m examples.roundtrip --output ./demo-output from the checkout."""
import argparse
import json
from pathlib import Path

from mella_core import preservation
from mella_core.cli import export_record, reader_html, _write_new
from mella_core.conversation_store import ConversationStore
from mella_core.store import Store


def run(output):
    output = preservation.safe_path(output)
    preservation.require_fresh(output)
    output.mkdir(mode=0o700)
    store = Store(output / "working")
    source = (Path(__file__).parent / "project.json").read_text(encoding="utf-8")
    imported = store.import_text("project.json", source)
    original = store.get_record(imported["record_ids"][0])
    note = store.add_note("Opening plan", "Saturday; budget 300.", provider="Example user")
    context = store.set_context([note["id"]], title="Original plan")
    revised = store.revise_note(note["id"], "Opening plan", "Sunday; budget 300. Saturday is superseded.",
                                expected_version=1)
    assert revised["version"] == 2
    assert store.get_record(note["id"], version=1)["content"] == "Saturday; budget 300."
    assert store.get_context() == context, "Pinned history must not silently become the revision."

    # Synthetic events exercise retention only; no model is invoked.
    conversations = ConversationStore(store)
    thread = conversations.create("Synthetic handoff")
    turn = conversations.start(thread["id"], "What date did we agree?", "example-agent")
    conversations.append(thread["id"], {"type": "text_delta", "provider": "example-agent",
        "turn_id": turn, "item_id": "reply", "delta": "The revised plan says Sunday.",
        "raw": {"example_vendor_field": [1, 2, 3]}})
    conversations.append(thread["id"], {"type": "turn_finished", "provider": "example-agent",
        "turn_id": turn, "status": "completed"})
    conversations.checkpoint(thread["id"])
    snapshot = store.create_snapshot()
    archive = store.snapshot_path(snapshot["id"])
    restored_path = output / "restored"
    receipt = preservation.restore(archive, restored_path)
    restored = Store(restored_path)
    recovered = restored.get_record(original["id"])
    assert restored.get_record(original["metadata"]["source"]["id"])["content"] == source
    assert recovered["metadata"]["original"] == json.loads(source)
    assert len(recovered["branches"]) == 2
    assert restored.get_context() == context
    assert restored.get_record(note["id"])["content"] == revised["content"]
    assert ConversationStore(restored).get(thread["id"]) == conversations.get(thread["id"])
    left, right = export_record(store), export_record(restored)
    for key in ("meta", "records", "contexts", "events"):
        assert left[key] == right[key], key
    _write_new(output / "record.json", json.dumps(right, indent=2, ensure_ascii=False) + "\n")
    _write_new(output / "reader.html", reader_html(right))
    result = {"verified": True, "synthetic_only": True, "model_invocations": 0,
              "checks": ["exact source", "alternate branch", "unknown metadata", "prior revision",
                         "pinned context", "conversation replay", "all record/event bytes"],
              "snapshot": str(archive), "reader": str(output / "reader.html"),
              "restored": str(restored_path), "receipt": receipt}
    _write_new(output / "result.json", json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(run(parser.parse_args().output), indent=2))
