# SPDX-License-Identifier: MPL-2.0
"""Durable workspace conversations using the container's existing record journal.

Provider events are immutable source records; the reading view is checkpointed
at turn boundaries. A crash can therefore replay every received text delta
without creating a complete duplicate transcript for each token.
"""
from __future__ import annotations

import copy
import json

from . import preservation
from .store import StoreError, _id, _string


ACTIVE = {"running", "stopping"}


def provider_name(provider):
    return {"codex": "ChatGPT", "claude": "Claude", "mella": "Mella"}.get(provider, provider or "Unknown participant")


class ConversationStore:
    def __init__(self, store):
        self.store = store

    def create(self, title=None):
        title = _string(title or "New conversation", "Title", empty=False, limit=200).strip()
        ident, stamp = _id("conversation"), preservation.now()
        value = {"title": title, "provider": "Mella", "content": "", "created_at": stamp,
                 "updated_at": stamp, "messages": [], "branches": [],
                 "metadata": {"origin": "mella_workspace", "workspace": {
                     "status": "idle", "sessions": {}, "cursor": 0, "error": None,
                     "activity": None, "turn_id": None, "provider": "codex"}}}
        with self.store._open() as container, container.db:
            self.store._insert(container, ident, "conversation", value)
        return self.get(ident)

    def _base(self, container, ident):
        record = self.store._get(container, ident)
        if record["metadata"].get("origin") != "mella_workspace":
            raise KeyError("Workspace conversation not found.")
        state = copy.deepcopy(record["metadata"]["workspace"])
        state.update({"id": ident, "title": record["title"], "created_at": record["created_at"],
                      "updated_at": record["updated_at"], "messages": record["messages"]})
        return state

    def _events(self, container, ident, after=0):
        prefix = "agent_event_" + ident + "_"
        rows = container.db.execute(
            "SELECT sha256,raw FROM records WHERE record_id GLOB ? ORDER BY record_id", (prefix + "*",)).fetchall()
        events = []
        for expected, raw in rows:
            if preservation.digest(raw) != expected:
                raise StoreError("A saved conversation event failed its integrity check.")
            events.append(json.loads(json.loads(raw)["text"]))
        return [event for event in events if event["seq"] > after]

    def _project(self, container, ident):
        state = self._base(container, ident)
        for event in self._events(container, ident, state["cursor"]):
            self._apply(state, event)
        return state

    @staticmethod
    def _apply(state, event):
        state["cursor"], state["updated_at"] = event["seq"], event["created_at"]
        kind = event["type"]
        provider = event.get("provider", state.get("provider", "codex"))
        if kind == "turn_started":
            state.update(status="running", error=None, turn_id=event["turn_id"], provider=provider,
                         activity={"label": "Connecting to " + provider_name(provider), "status": "running"})
            state["messages"].append({"id": event["message_id"], "role": "user", "provider": "You",
                                      "content": event["text"], "created_at": event["created_at"],
                                      "status": "completed", "turn_id": event["turn_id"]})
            if state["title"] == "New conversation":
                state["title"] = " ".join(event["text"].split())[:75]
        elif kind == "session":
            state["sessions"].setdefault(provider, {})["id"] = event["session_id"]
        elif kind in {"text_delta", "message"}:
            ident = provider + ":" + str(event.get("item_id") or event.get("turn_id"))
            message = next((m for m in state["messages"] if m["id"] == ident), None)
            if message is None:
                message = {"id": ident, "role": "assistant", "provider": provider, "content": "",
                           "created_at": event["created_at"], "status": "streaming", "turn_id": event["turn_id"]}
                state["messages"].append(message)
            if kind == "text_delta":
                message["content"] += event.get("delta", "")
            else:
                message["content"] = event.get("text", "")
                message["status"] = "completed"
            if event.get("phase"):
                message["phase"] = event["phase"]
            state["activity"] = {"label": "Writing", "status": "running"}
        elif kind == "activity":
            state["activity"] = {"label": event.get("label", "Working"), "status": event.get("status", "running")}
        elif kind == "model":
            state["model"], state["reasoning"] = event.get("model"), event.get("reasoning")
        elif kind == "stop_requested":
            state["activity"] = {"label": "Stopping…", "status": "stopping"}
        elif kind == "turn_finished":
            state.update(status=event["status"], error=event.get("error"), activity=None)
            for message in state["messages"]:
                if message.get("turn_id") == event["turn_id"] and message["role"] == "assistant" and message["status"] == "streaming":
                    message["status"] = event["status"]
            confirmed_participation = event["status"] == "completed" or any(
                m["role"] == "assistant" and m.get("turn_id") == event["turn_id"] for m in state["messages"])
            if confirmed_participation and state["sessions"].get(provider, {}).get("id") and state["messages"]:
                state["sessions"][provider]["last_seen_message_id"] = state["messages"][-1]["id"]

    def append(self, ident, event):
        with self.store._open() as container, container.db:
            container.db.execute("BEGIN IMMEDIATE")
            event = self._append(container, ident, event)
        return event

    def _append(self, container, ident, event):
        """Caller holds the database write transaction."""
        event = copy.deepcopy(event)
        self._base(container, ident)  # Reject non-conversation identities.
        prefix = "agent_event_" + ident + "_"
        last = container.db.execute("SELECT MAX(record_id) FROM records WHERE record_id GLOB ?", (prefix + "*",)).fetchone()[0]
        event.update(seq=(int(last.rsplit("_", 1)[1]) if last else 0) + 1,
                     created_at=preservation.now(), conversation_id=ident)
        source_id = "agent_event_{}_{}".format(ident, str(event["seq"]).zfill(12))
        text = json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        self.store._insert(container, source_id, "source", {
            "filename": "Mella agent event {}.json".format(event["seq"]), "text": text,
            "created_at": event["created_at"], "encoding": "UTF-8",
            "sha256": preservation.digest(text.encode("utf-8"))})
        return event

    def start(self, ident, text, provider):
        text = _string(text, "Message", empty=False, limit=100000)
        provider = _string(provider, "Provider", empty=False, limit=100)
        with self.store._open() as container, container.db:
            container.db.execute("BEGIN IMMEDIATE")
            if self._project(container, ident)["status"] in ACTIVE:
                raise StoreError("This conversation already has a running turn.")
            turn_id = _id("turn")
            self._append(container, ident, {"type": "turn_started", "turn_id": turn_id, "message_id": _id("message"),
                                           "text": text, "provider": provider})
        return turn_id

    @staticmethod
    def public(state):
        return {key: value for key, value in state.items() if key != "sessions"}

    def get(self, ident, *, internal=False):
        with self.store._open() as container:
            container.db.execute("BEGIN")
            state = self._project(container, ident)
        return state if internal else self.public(state)

    def listing(self):
        with self.store._open() as container:
            ids = [r["id"] for r in self.store._records(container) if r["id"].startswith("conversation_")]
        items = [self.get(ident) for ident in ids]
        keys = {"id", "title", "created_at", "updated_at", "status", "provider"}
        return sorted(({k: v for k, v in item.items() if k in keys} for item in items),
                      key=lambda item: item["updated_at"], reverse=True)

    def events(self, ident, after=0):
        if not isinstance(after, int) or after < 0:
            raise StoreError("Event cursor must be a nonnegative integer.")
        with self.store._open() as container:
            container.db.execute("BEGIN")
            state = self._project(container, ident)
            # Raw provider envelopes remain local source records; UI gets only
            # normalized display events, avoiding token/auth/log disclosure.
            events = [{k: v for k, v in e.items() if k not in {"raw", "metadata"}}
                      for e in self._events(container, ident, after) if e["type"] != "metadata"]
            return {"events": events, "cursor": state["cursor"], "conversation": self.public(state)}

    def checkpoint(self, ident):
        with self.store._open() as container, container.db:
            container.db.execute("BEGIN IMMEDIATE")
            state = self._project(container, ident)
            messages = copy.deepcopy(state["messages"])
            for index, message in enumerate(messages):
                message.update(author="You" if message["role"] == "user" else
                               provider_name(message["provider"]),
                               timestamp=message["created_at"], parent_id=messages[index-1]["id"] if index else None)
            value = {"title": state["title"], "provider": "Mella", "created_at": state["created_at"],
                     "updated_at": state["updated_at"], "messages": messages, "branches": [],
                     "content": "\n\n".join(m["author"] + ":\n" + m["content"] for m in messages),
                     "metadata": {"origin": "mella_workspace", "workspace": {
                         k: v for k, v in state.items() if k not in {"id", "title", "created_at", "updated_at", "messages"}},
                         "provider_events": {"record_prefix": "agent_event_" + ident + "_", "count": state["cursor"]}}}
            self.store._insert(container, ident, "conversation", value)
        return self.get(ident)

    def recover_interrupted(self):
        for item in self.listing():
            if item["status"] in ACTIVE:
                state = self.get(item["id"])
                self.append(item["id"], {"type": "turn_finished", "turn_id": state["turn_id"],
                    "provider": state["provider"], "status": "interrupted",
                    "error": "Mella restarted before this turn finished. Received work is preserved; you can continue."})
                self.checkpoint(item["id"])
