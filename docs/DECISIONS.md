# Portable decisions and explanation

The format preserves **what was said**, **how a participant interpreted it**, and **why that reading applies now** as separate objects. The open implementation consists of a deterministic reducer, original-source validators, a read-only container adapter, and a standalone offline reader. No account, model, network, private application module, or semantic extractor is required to inspect a record.

## Compatibility

`mella.decisions/1` adds record conventions within `mella.container-proof/1`; it does not add tables or change `mella.snapshot-proof/1`. Each event is an immutable version-1 record with:

- `record_id`: `mella_decision_event_` followed by a 12-digit sequence beginning at 1.
- `kind`: `decision_event`.
- `raw`: the ordinary JSON library envelope, with `content` containing the JSON event.
- `metadata`: exactly `{"schema":"mella.decisions/1","origin":"mella_decision_route"}`.
- The normal record SHA-256 binding the exact envelope bytes.

Every event has `schema`, `event_id`, `seq`, `created_at`, `type`, and `run_id`. Events after registration also retain `work_id` and `conversation_id`. Gaps, reordered events, revised reserved records, unknown event types, and mismatched envelopes fail explicitly. A regular note or imported JSON containing these words remains ordinary source material. Unknown metadata in original source records is retained without becoming an instruction.

## Events

| Type | Retained meaning and principal fields |
|---|---|
| `turn_registered` | `run_id`, `provider`, `work_id`, `conversation_id`, `container_id`, exact `human_anchor`, registration head, optional correction target. The anchor belongs to a saved `turn_started` event. |
| `understanding` | `entry`, `expected_head`, `observed_head`. Records one participant's interpretation and source linkage, including pending disposition. |
| `guidance_supplied` | `head`, `complete`, optional `start`, `end`, `total`. Records what the host says it delivered, separately from storage acknowledgment. |
| `extraction_ack` | Request identity/digest and `result`, including extracted entry IDs and dispositions. An empty extraction still has an acknowledgment. |
| `turn_finished` | Turn `status` and optional `reason`. Finishing a conversation is not proof extraction or guidance delivery completed. |
| `source_link` | Old/new record IDs and exact versions, attributed `issuer`, reason, optional human source anchor, expected/observed heads, and pending status. A claimed link without human wording remains pending. |
| `support_review` | Target reading ID, old supporting record ID, exact `new_anchor`, issuer, reason, expected/observed heads, and pending status. Preserves original evidence while recording contextual review. |

The offline export keeps all event fields, including host request metadata and delivery receipts. Core replays the recorded state; it does not independently prove a provider read or understood delivered material.

## Interpretation entry

An entry contains:

- `id` (`direction_` plus its event sequence), `event_id`.
- `subject_id`, `subject_label`, and `subject`: stable ID, label, earlier parent IDs and checked subject IDs. Subjects describe the reasoning structure; labels do not filter applicable directions.
- `interpretation`: the participant's reading, separate from the quoted human words.
- `kind`: `direction`, `recommendation`, `observation`, or `task`; `certainty`: `clear` or `ambiguous`. A `task` records a one-turn request, not standing guidance or completion.
- `scope`: `{kind, id}` for container/work, or `{kind:"conversation", id, work_id}`.
- `source`: exact human-message anchor with original event, input route, and context.
- `issuer`: recorded provider, run, and conversation IDs. These are provenance labels, not authenticated human identity.
- `supports`: exact source-version anchors; `relations`: explicit typed links to earlier readings; `reason` for those links.
- `pending` and `pending_reason`: preserve incomplete/stale interpretations without silently promoting them.

The reducer derives `status`, `review_required`, `effective_supports`, and an ordered `explanation` of `{rule,event_id,detail}`. Derived views never replace the source entry.

## Exact source anchors

Every anchor identifies `record_id`, positive integer `version`, `start`, `end`, and `quote`. Offsets are **Unicode character positions**, start-inclusive/end-exclusive, not UTF-8 bytes or JavaScript UTF-16 code units. The quote must equal the exact slice. Repeated wording requires explicit offsets.

A human anchor also contains `message_id`, `full_text`, `context`, `observed_path`, `input_origin`, and `original_event`. The source is a version-1 `source` record named `agent_event_{conversation_id}_{sequence}`. Its `text` contains the original JSON `turn_started` event. The validator compares the quote, full text, message, original event, route, and registration against those preserved bytes. `input_origin.route` is `composer_input`, `relayed`, or `legacy_unspecified`; a `claimed_speaker` is retained as a claim, not inferred from wording.

A supporting anchor contains the original title and `content_sha256`. The validator checks its quote against that exact version's `content` (or `text` for a source record) and checks the text hash. A derived decision envelope cannot stand in for original evidence. A changed source does not silently move the anchor to a newer version.

`validate_anchor(anchor, resolver, human=False)` accepts a resolver returning `{kind, record}` for `(record_id,version)` after checking the stored record hash. `validate_journal(events, resolver, container_id)` validates linkage and structure before deterministic replay. The SQLite reader supplies a hash-checking resolver. A separate consumer using the pure reducer is responsible for validating its own input first.

## Replay rules

`reduce_events(events, work_id="default", conversation_id=None, current_versions=None)` is pure Python. It performs no I/O, interpretation, model request, or permission change.

1. Container guidance applies throughout that container. Work guidance applies within that work. Conversation guidance applies only to the named conversation within its work. No subject-label heuristic suppresses otherwise applicable guidance.
2. Clear directions begin current; recommendations and ambiguous readings are provisional. Recorded pending readings stay pending review.
3. Explicit `corrects`, `supersedes`, and `revokes` relations retain or narrow the target's scope. A narrow exception does not remove broad guidance in other conversations. Recency alone does not replace anything.
4. `adopts` records a new human-linked direction adopting a recommendation; the original recommendation remains attributed as a recommendation. `supports` preserves a supporting relation without replacing its target.
5. `conflicts` keeps both readings visible. A relay cannot silently correct, supersede, or revoke direct input: it becomes a visible conflict. Explicit correction/replacement/revocation of the conflicting reading can resolve that conflict while preserving its original trace. Unrelated later text does not resolve it.
6. A revocation is an event, not a new positive instruction. Pending or provisional readings cannot overwrite current guidance.
7. Supporting revisions or claimed source links flag contextual review. Original human direction and original evidence remain intact. A completed support review changes effective support while retaining its lineage.
8. The guidance `head` hashes relevant semantic events and their current evidence dependencies. Unrelated topics in the same applicable scope are still included. Operational delivery/finish events do not themselves rewrite meaning.
9. One-turn tasks appear in history with status `task` (a recorded request), without changing the governing head, evidence dependencies, or standing subject list. They cannot carry relations that mutate governing readings. The ordinary conversation retains the work and its outcome; this status does not assert completion.

The projection contains `directions` (all live readings, including provisional/conflict/pending), inactive `history`, `conflicts`, `provisional`, `subjects`, `coverage`, `head`, and an inspectable text rendition. Consumers should not label every element of `directions` as a confirmed current direction; check its `status`.

## Independent reading and export

```sh
python3 -m mella_core --data-dir ./recovered explain --work-id default
python3 -m mella_core --data-dir ./recovered explain --work-id default --format html --output ./explanation.html
python3 -m mella_core.decision_reader ./recovered/container.sqlite3 --work-id default --json ./decision-record.json
```

The package-level `explain` command prints the complete document by default. The focused `decision_reader` command prints the guidance projection by default; `--json` writes the complete document and `--html` writes the full offline reading copy. Outputs refuse existing files and are created with mode 0600. Neither path uses the mutating Store API to open an existing database.

The full document includes container metadata, relevant guidance, **the complete journal**, all current version numbers, and all referenced original records, including other scopes and inactive history. A scope filter is not a privacy filter. Use the general Core export when every unrelated library record and revision is also wanted. Snapshot restoration remains the supported recovery path.

Live databases are read at a consistent committed transaction, including WAL. SQL is query-only. The reader tries read-only admission first; some SQLite builds require read/write admission to materialize missing WAL/SHM sidecars even for a query. That fallback sets `query_only=ON` before accessing stored content and cannot create a missing database. It can create incidental sidecars but does not issue data writes. If filesystem admission fails, a manifest-verified temporary frozen copy is allowed only when no live WAL exists; immutable mode is used only for that private copy. Tests check unchanged source database bytes and visibility of uncheckpointed committed WAL. HTML escapes all supplied content, has a restrictive content policy, and loads no scripts or outside assets.

## What validation does not establish

Hash and linkage checks establish internal consistency. They do not authenticate a person, prove that an interpretation is faithful, establish that a source relationship is semantically correct, or authorize actions. A local owner can forge another consistent database. Host writing/extraction logic, real participant delivery, and semantic quality require their own evidence. The portable format makes these claims inspectable without requiring the original interpreter to remain available.
