# SPDX-License-Identifier: MPL-2.0
"""Portable, deterministic interpretation of Mella's decision journal.

This module performs no I/O and grants no powers. Callers supply application-
validated events; hashes establish record integrity, not human identity.
"""
from __future__ import annotations

import copy
import hashlib
import json


SCHEMA = "mella.decisions/1"
PREFIX = "mella_decision_event_"
ORIGIN = "mella_decision_route"
EVENT_TYPES = {"turn_registered", "understanding", "extraction_ack",
               "turn_finished", "guidance_supplied", "source_link", "support_review"}
KINDS = {"direction", "recommendation", "observation", "task"}
RELATIONS = {"adopts", "corrects", "supersedes", "revokes", "conflicts", "supports"}
LIVE = {"current", "provisional", "conflict", "pending_review"}


class DecisionError(ValueError):
    pass


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def text(value, label, limit=100000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DecisionError(label + " must be nonempty text within its size limit.")
    return value


def normalize_scope(scope, work_id, conversation_id, container_id):
    if not isinstance(scope, dict) or scope.get("kind") not in {"container", "work", "conversation"}:
        raise DecisionError("Scope must name container, work, or conversation.")
    kind = scope["kind"]
    expected = {"container": container_id, "work": work_id, "conversation": conversation_id}[kind]
    ident = scope.get("id", expected)
    if ident != expected:
        raise DecisionError("A turn may address only its own container, work, or conversation scope.")
    result = {"kind": kind, "id": expected}
    if kind == "conversation":
        result["work_id"] = work_id
    return result


def applies(scope, work_id, conversation_id):
    return (scope["kind"] == "container" or
            (scope["kind"] == "work" and scope["id"] == work_id) or
            (scope["kind"] == "conversation" and scope["id"] == conversation_id and
             scope.get("work_id", work_id) == work_id))


def scope_contains(outer, inner):
    if outer == inner or outer["kind"] == "container":
        return True
    return outer["kind"] == "work" and inner["kind"] == "conversation" and inner.get("work_id") == outer["id"]


def quote_range(content, quote, start=None, end=None):
    text(quote, "Exact source quote")
    if start is None:
        start = content.find(quote)
        if start >= 0 and content.find(quote, start + 1) >= 0:
            raise DecisionError("Quote occurs more than once; provide exact start/end character offsets.")
        end = start + len(quote)
    if isinstance(start, bool) or not isinstance(start, int) or isinstance(end, bool) or not isinstance(end, int):
        raise DecisionError("Source offsets must be integer character offsets.")
    if start < 0 or end <= start or end > len(content) or end - start != len(quote) or content[start:end] != quote:
        raise DecisionError("Quote must match the preserved source exactly at its stated offsets.")
    return start, end


def validate_event(event):
    if not isinstance(event, dict) or event.get("schema") != SCHEMA or event.get("type") not in EVENT_TYPES:
        raise DecisionError("Invalid reserved decision event.")
    if not isinstance(event.get("seq"), int) or not isinstance(event.get("event_id"), str):
        raise DecisionError("Invalid decision event identity.")
    if event["event_id"] != PREFIX + str(event["seq"]).zfill(12):
        raise DecisionError("Decision event identity and sequence differ.")
    return event


def reduce_events(events, work_id="default", conversation_id=None, current_versions=None):
    """Replay without recency arbitration or semantic keyword matching.

    Related narrow directions replace broad ones only in the narrow view.
    Topic labels never filter otherwise applicable guidance. Revisions retain
    original anchors and mark support review, including unchanged quotations.
    """
    versions = current_versions or {}
    entries, subjects, links, reviews, semantic = {}, {}, [], [], []
    for event in events:
        validate_event(event)
        typ = event["type"]
        if typ == "understanding":
            entry = copy.deepcopy(event["entry"])
            if entry.get("subject"):
                subjects.setdefault(entry["subject_id"], copy.deepcopy(entry["subject"]))
            entries[entry["id"]] = entry
        elif typ == "source_link":
            links.append(event)
        elif typ == "support_review":
            reviews.append(event)

    selected = {ident: entry for ident, entry in entries.items()
                if applies(entry["scope"], work_id, conversation_id)}
    for entry in selected.values():
        entry["status"] = ("task" if entry["kind"] == "task" else "pending_review" if entry.get("pending") else
                           "current" if entry["kind"] == "direction" and entry["certainty"] == "clear" else "provisional")
        entry["review_required"] = bool(entry.get("pending"))
        entry["explanation"] = [{"rule": "source_linked_interpretation", "event_id": entry["event_id"],
                                  "detail": "The exact human message and this participant's reading are retained separately."}]
        if entry["kind"] == "task":
            entry["explanation"].append({"rule": "recorded_one_turn_request", "event_id": entry["event_id"],
                                         "detail": "This records a request for the turn, not a standing instruction or a claim that the task completed. The ordinary conversation carries the task."})
        if entry.get("pending"):
            entry["explanation"].append({"rule": "stale_or_unresolved", "event_id": entry["event_id"],
                                         "detail": entry.get("pending_reason", "Needs review before becoming current guidance.")})

    # Replay relations in journal order. Recency itself never displaces an entry.
    conflict_edges = []
    for entry in selected.values():
        if entry["status"] in {"pending_review", "provisional", "task"}:
            continue
        for relation in entry.get("relations", []):
            target = selected.get(relation["target_id"])
            if target is None:
                continue
            rel = relation["type"]
            if rel == "adopts":
                entry["explanation"].append({"rule": "explicit_adoption", "event_id": entry["event_id"],
                                             "detail": "A new human-linked direction adopts the attributed recommendation; the earlier recommendation remains preserved as such."})
                continue
            if target["status"] not in LIVE:
                entry["status"] = "pending_review"
                entry["review_required"] = True
                entry["explanation"].append({"rule": "inactive_target", "event_id": entry["event_id"],
                                             "detail": "The related reading is no longer current; reconsider this relation."})
                continue
            relayed = entry["source"].get("input_origin", {}).get("route") == "relayed"
            direct = target["source"].get("input_origin", {}).get("route") == "composer_input"
            if rel == "conflicts" or (relayed and direct and rel in {"corrects", "supersedes", "revokes"}):
                entry["status"] = "conflict"
                if target["status"] != "pending_review":
                    target["status"] = "conflict"
                conflict_edges.append((entry["id"], target["id"], entry["event_id"]))
                rule = "relay_does_not_override_direct" if relayed and direct else "explicit_conflict"
                explanation = {"rule": rule, "event_id": entry["event_id"],
                               "detail": "Both readings remain visible; chronology does not resolve this conflict."}
                entry["explanation"].append(explanation)
                target["explanation"].append(copy.deepcopy(explanation))
            elif rel in {"corrects", "supersedes", "revokes"}:
                target["status"] = {"corrects": "corrected", "supersedes": "superseded", "revokes": "revoked"}[rel]
                target["explanation"].append({"rule": rel, "event_id": entry["event_id"],
                                              "detail": entry.get("reason") or "Explicit source-linked relation in this scope."})
                if rel == "revokes":
                    entry["status"] = "revoked"  # Revoke is an event, not a new positive instruction.

    # A later explicit correction can resolve one side of a conflict. Retain
    # that edge in the explanation without leaving its surviving side falsely
    # unresolved forever; no timestamp or unrelated new reading resolves it.
    for entry in selected.values():
        if entry["status"] != "conflict":
            continue
        connected = [other if ident == entry["id"] else ident for ident, other, _ in conflict_edges
                     if entry["id"] in {ident, other}]
        if connected and not any(selected[ident]["status"] in LIVE for ident in connected):
            entry["status"] = "current" if entry["kind"] == "direction" and entry["certainty"] == "clear" else "provisional"
            resolution_id = max(selected[ident]["explanation"][-1]["event_id"] for ident in connected)
            entry["explanation"].append({"rule": "conflict_resolved_by_explicit_relation", "event_id": resolution_id,
                                         "detail": "The conflicting reading was explicitly corrected, replaced, or revoked. The original conflict remains in the trace."})

    for entry in selected.values():
        support_reviews = [e for e in reviews if e["target_id"] == entry["id"] and not e.get("pending")]
        effective = copy.deepcopy(entry.get("supports", []))
        for review in support_reviews:
            effective = [s for s in effective if s["record_id"] != review["old_record_id"]]
            effective.append(copy.deepcopy(review["new_anchor"]))
            entry["explanation"].append({"rule": "support_reviewed", "event_id": review["event_id"],
                                         "detail": review["reason"]})
        entry["effective_supports"] = effective
        for support in effective:
            current = versions.get(support["record_id"])
            changed = current is not None and current != support["version"]
            related = [e for e in links if e["old_record_id"] == support["record_id"] and
                       not any(r["old_record_id"] == e["old_record_id"] and r["seq"] > e["seq"] for r in support_reviews)]
            if changed or related:
                entry["review_required"] = True
                entry["explanation"].append({"rule": "support_revision_review", "event_id": related[-1]["event_id"] if related else entry["event_id"],
                                             "detail": ("A participant proposed a source relationship; it remains pending. " if related and related[-1].get("pending") else "Supporting material changed or was explicitly linked to a revision. ") +
                                                       "The original human direction is retained; its support needs contextual review."})
        # A revision of evidence does not revoke the independent human message.

    relevant_ids = {ident for ident, entry in selected.items() if entry["kind"] != "task"}
    for event in events:
        if event["type"] == "understanding" and event["entry"]["id"] in relevant_ids:
            semantic.append(event)
        elif event["type"] == "support_review" and event["target_id"] in relevant_ids:
            semantic.append(event)
        elif event["type"] == "source_link":
            if any(any(s["record_id"] in {event["old_record_id"], event["new_record_id"]}
                           for s in entry.get("supports", []) + entry.get("effective_supports", []))
                   for ident, entry in selected.items() if ident in relevant_ids):
                semantic.append(event)
    dependencies = {s["record_id"]: versions.get(s["record_id"], s["version"])
                    for ident, entry in selected.items() if ident in relevant_ids
                    for s in entry.get("supports", []) + entry.get("effective_supports", [])}
    head = digest({"schema": SCHEMA, "work_id": work_id, "conversation_id": conversation_id,
                   "events": semantic, "dependencies": dependencies})
    live = [e for e in selected.values() if e["status"] in LIVE]
    history = [e for e in selected.values() if e["status"] not in LIVE]
    visible_subjects = [subject for ident, subject in subjects.items() if any(e["subject_id"] == ident and e["kind"] != "task" for e in selected.values())]
    lines = []
    for entry in live:
        lines.append("[{status}] {id} · {subject_label} · {scope_text}\nHuman words: {quote}\n{provider}'s reading: {interpretation}{review}".format(
            **entry, scope_text=json.dumps(entry["scope"], ensure_ascii=False), quote=entry["source"]["quote"],
            provider=entry["issuer"]["provider"], review="\nSupport/context needs review." if entry["review_required"] else ""))
    return {"schema": SCHEMA, "head": head, "directions": live, "history": history,
            "conflicts": [e for e in live if e["status"] == "conflict"],
            "provisional": [e for e in live if e["status"] in {"provisional", "pending_review"}],
            "subjects": visible_subjects, "coverage": {"work_id": work_id, "conversation_id": conversation_id,
              "container_included": True, "subject_filter_applied": False, "direction_count": len(live)},
            "text": "\n\n".join(lines)}
