# SPDX-License-Identifier: MPL-2.0
"""Portable source and journal validation, without an interpreter or writer.

The resolver returns {kind, record} for an exact (record_id, version), after
checking its stored hash. Validation proves internal consistency, not that an
interpretation is correct, a person issued it, or any action is authorized.
Unknown source metadata is retained by the reader, not normalized away here.
"""
from __future__ import annotations

import hashlib
import json

from .decision_core import (DecisionError, PREFIX, KINDS, RELATIONS,
                            normalize_scope, quote_range, scope_contains,
                            text, validate_event)


def _object(value, label):
    if not isinstance(value, dict):
        raise DecisionError(label + " must be an object.")
    return value


def _list(value, label):
    if not isinstance(value, list):
        raise DecisionError(label + " must be a list.")
    return value


def validate_anchor(anchor, resolve, *, human=False):
    """Check exact Unicode character offsets against the original payload."""
    _object(anchor, "Source anchor")
    ident = text(anchor.get("record_id"), "Source identity", 200)
    version = anchor.get("version")
    if type(version) is not int or version < 1 or ident.startswith(PREFIX):
        raise DecisionError("An anchor requires a positive version of an original source.")
    loaded = _object(resolve(ident, version), "Resolved source")
    record = _object(loaded.get("record"), "Source payload")
    if human:
        if loaded.get("kind") != "source" or version != 1:
            raise DecisionError("A human anchor must reference an original conversation event.")
        try:
            event = _object(json.loads(record["text"]), "Original event")
        except (KeyError, TypeError, ValueError) as exc:
            raise DecisionError("A human anchor requires a readable original event.") from exc
        content = event.get("text")
        expected_id = "agent_event_{}_{}".format(event.get("conversation_id"), str(event.get("seq")).zfill(12))
        if (event.get("type") != "turn_started" or type(event.get("seq")) is not int or
                ident != expected_id or anchor.get("message_id") != event.get("message_id") or
                anchor.get("original_event") != event or anchor.get("full_text") != content or
                anchor.get("context") != content or anchor.get("observed_path") != "local_workspace_input"):
            raise DecisionError("Human anchor differs from its preserved conversation event.")
        origin = event.get("input_origin", {"route": "legacy_unspecified"})
        if (not isinstance(origin, dict) or origin.get("route") not in
                {"composer_input", "relayed", "legacy_unspecified"} or anchor.get("input_origin") != origin):
            raise DecisionError("Observed input route differs from the source record.")
    else:
        content = record.get("text") if loaded.get("kind") == "source" else record.get("content")
    if not isinstance(content, str):
        raise DecisionError("An anchor requires preserved text.")
    quote_range(content, anchor.get("quote"), anchor.get("start"), anchor.get("end"))
    if not human and anchor.get("content_sha256") != hashlib.sha256(content.encode("utf-8")).hexdigest():
        raise DecisionError("Supporting text hash differs from the exact source version.")
    return content


def validate_journal(events, resolve, container_id):
    """Validate complete reserved history before replay, including other scopes.

No semantic extraction occurs. This recognizes the documented /1 provenance
and relation structure; an arbitrary note containing instructions is not one
of these events. A database owner can still forge an internally valid record.
"""
    runs, entries, subjects, reviewed_supports = {}, {}, {}, {}
    for seq, event in enumerate(events, 1):
        validate_event(event)
        if type(event["seq"]) is not int or event["seq"] != seq:
            raise DecisionError("Decision journal must be contiguous and ordered.")
        typ = event["type"]
        run_id = text(event.get("run_id"), "Run identity", 200)
        if typ == "turn_registered":
            if run_id in runs or event.get("container_id") != container_id:
                raise DecisionError("Duplicate run or different container provenance.")
            for key in ("work_id", "conversation_id", "provider"):
                text(event.get(key), key, 200)
            anchor = event.get("human_anchor")
            validate_anchor(anchor, resolve, human=True)
            original = anchor["original_event"]
            if any(original.get(key) != event.get(key) for key in ("conversation_id", "provider")) or original.get("turn_id") != run_id:
                raise DecisionError("Registered run differs from the saved human turn.")
            runs[run_id] = event
            continue
        run = runs.get(run_id)
        if run is None or any(event.get(key) != run[key] for key in ("work_id", "conversation_id")):
            raise DecisionError("Decision event requires its registered run and scope.")
        if typ == "understanding":
            entry = _object(event.get("entry"), "Interpretation")
            if entry.get("id") != "direction_" + str(seq).zfill(12) or entry.get("event_id") != event["event_id"]:
                raise DecisionError("Interpretation identity differs from its event.")
            if entry.get("kind") not in KINDS or entry.get("certainty") not in {"clear", "ambiguous"}:
                raise DecisionError("Unknown interpretation kind or certainty.")
            if type(entry.get("pending")) is not bool:
                raise DecisionError("Pending disposition must be explicit.")
            text(entry.get("interpretation"), "Interpretation", 16000)
            scope = normalize_scope(entry.get("scope"), run["work_id"], run["conversation_id"], container_id)
            if scope != entry["scope"]:
                raise DecisionError("Interpretation scope is not canonical.")
            expected_issuer = {"provider": run["provider"], "run_id": run_id, "conversation_id": run["conversation_id"]}
            if entry.get("issuer") != expected_issuer:
                raise DecisionError("Interpretation attribution differs from its registered participant.")
            source = entry.get("source")
            validate_anchor(source, resolve, human=True)
            if any(source.get(k) != v for k, v in run["human_anchor"].items() if k not in {"quote", "start", "end"}):
                raise DecisionError("Interpretation must anchor its own registered human message.")
            subject = _object(entry.get("subject"), "Subject")
            subject_id = text(subject.get("id"), "Subject identity", 150)
            text(subject.get("label"), "Subject label", 300)
            if entry.get("subject_id") != subject_id or entry.get("subject_label") != subject["label"]:
                raise DecisionError("Subject identity and label differ.")
            if subject_id in subjects and subjects[subject_id] != subject:
                raise DecisionError("Subject identity has conflicting definitions.")
            for key in ("parent_ids", "checked_subject_ids"):
                values = _list(subject.get(key, []), key)
                if any(not isinstance(v, str) or v not in subjects for v in values):
                    raise DecisionError("Subject relationship names an unknown earlier subject.")
            subjects[subject_id] = subject
            for anchor in _list(entry.get("supports", []), "Supports"):
                validate_anchor(anchor, resolve)
            relations = _list(entry.get("relations", []), "Relations")
            if entry["kind"] == "task" and relations:
                raise DecisionError("A one-turn task cannot alter governing interpretations.")
            if relations:
                text(entry.get("reason"), "Relation reason", 16000)
            for relation in relations:
                _object(relation, "Relation")
                target = entries.get(relation.get("target_id"))
                rel = relation.get("type")
                if rel not in RELATIONS or target is None or target["kind"] == "task" or not scope_contains(target["scope"], scope):
                    raise DecisionError("Relation must name an earlier reading and retain or narrow its scope.")
                if rel in {"corrects", "supersedes", "revokes"} and entry["kind"] != "direction":
                    raise DecisionError("A recommendation or observation cannot replace a direction.")
                if rel == "adopts" and (entry["kind"] != "direction" or target["kind"] != "recommendation"):
                    raise DecisionError("Adoption requires a direction and recommendation target.")
            entries[entry["id"]] = entry
        elif typ == "source_link":
            if event.get("old_record_id") == event.get("new_record_id"):
                raise DecisionError("A source link names two different original records.")
            text(event.get("reason"), "Source link reason", 16000)
            for prefix in ("old", "new"):
                ident, version = event.get(prefix + "_record_id"), event.get(prefix + "_version")
                if not isinstance(ident, str) or ident.startswith(PREFIX) or type(version) is not int or version < 1:
                    raise DecisionError("Source link requires original versioned records.")
                resolve(ident, version)
            if event.get("source") is not None:
                validate_anchor(event["source"], resolve, human=True)
                if event["source"].get("record_id") != run["human_anchor"]["record_id"]:
                    raise DecisionError("Source link must refer to its own human input.")
            elif not event.get("pending"):
                raise DecisionError("A source link without human wording remains pending.")
        elif typ == "support_review":
            target = entries.get(event.get("target_id"))
            if target is None or target["kind"] == "task":
                raise DecisionError("Support review requires an earlier reading.")
            own_scope = {"kind": "conversation", "id": run["conversation_id"], "work_id": run["work_id"]}
            support_ids = {s["record_id"] for s in target.get("supports", [])}
            support_ids.update(reviewed_supports.get(target["id"], []))
            if event.get("old_record_id") not in support_ids or not scope_contains(target["scope"], own_scope):
                raise DecisionError("Support review must refer to existing support in this scope.")
            validate_anchor(event.get("new_anchor"), resolve)
            text(event.get("reason"), "Support review reason", 16000)
            if not event.get("pending"):
                reviewed_supports.setdefault(target["id"], set()).add(event["new_anchor"]["record_id"])
        if typ in {"source_link", "support_review"} and type(event.get("pending")) is not bool:
            raise DecisionError("Review disposition must be explicit.")
        if typ in {"source_link", "support_review"} and event.get("issuer") != {"provider": run["provider"], "run_id": run_id}:
            raise DecisionError("Source review attribution differs from the registered participant.")
    return events
