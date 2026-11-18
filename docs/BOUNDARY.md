# Open core and commercial boundaries

Mella Core supplies an independently usable persistent record, its recovery tools, and a reference connection interface. It is the open foundation of Mella, a continuing workspace whose participants can change while the work remains with its owner.

## Open foundation

The open source in this repository covers:

- Versioned records, exact UTF-8 imports, source metadata, and conversation history.
- Conversation event retention and reconstruction, including interrupted work.
- Pinned context descriptors, explicit record reads, and source attribution.
- Snapshot creation, integrity checks, and restoration into a new destination.
- An independent reader and command interface.
- The portable decision format, exact source validators, deterministic scope/relation replay, and model-free explanation reader.
- A local MCP reference adapter for compatible hosts.
- Synthetic examples and behavior tests.

The format and tools are available under the repository's [MPL 2.0 license](../LICENSE). Using these tools does not require purchasing the commercial Mella application or signing in to a Mella service. External hosts may require their own accounts or charge for computation.

## Commercial product

The commercial Mella application develops the working experience around this foundation: the resident's context selection and interpretation, planning, coordination, result evaluation, the complete interface, integrated installation, and device experiences. Optional operated services may include synchronization, backup, hosted computation, and support.

The commercial application and resident implementation are not distributed in this repository. No model, model weights, provider client, credential, or hosted service is bundled here. The Mella name and mark identify the official product; the software license does not grant trademark rights. See [LICENSE](../LICENSE).

The user's continuing work remains part of the record. Source material, retained conversations, corrections, decisions, assistant notes, and any stored task or resident state must remain recoverable independently of the proprietary logic that produced or used them. An implementation can keep its methods proprietary while exposing the resulting user state in a documented, portable representation. The current snapshot includes all records actually stored in its container; it cannot preserve state left in another application's files.

Decision interpretation and the host's transactional extraction/writing route remain separate from this package. Core provides the resulting record format and executable replay/validation/reading code. The reducer does not decide what a person meant from keywords; it applies recorded explicit relations to source-linked readings and retains the rationale and history. Consumers can inspect or implement their own interpreter without purchasing the commercial workspace.

## Authority boundary

All material deliberately included in a container is available to its admitted clients. Selected context governs loading priority, not a private compartment for each agent. Admission to the shared container and permission to act elsewhere are separate responsibilities.

The architectural direction is to keep enforceable permission checks and their action records in the open foundation. A resident can interpret a request and propose an action; its confidence does not grant the action. This release implements narrow local operations, argument validation, and a current-access input for the low-level context loader. That input is supplied by its caller. It is not an authenticated identity or grant service, a whole-system permission broker, or a remote multi-user isolation boundary.

Client names and imported speaker labels are attribution claims, not independently verified identities. Recorded assistant interpretations do not become human decisions merely by being retained. Integrations must preserve that distinction.

## What is preserved

Snapshots include the container's records, revisions, contexts, and journal at a committed boundary. They also declare known external dependencies. The imported conversation reader follows an active branch; original imports retain their other branches and unknown fields. A referenced image or other attachment is not an included payload unless its bytes were actually stored. Model runtimes, provider credentials, and external provider session files are separate dependencies.

The snapshot is an integrity-checked, unencrypted local archive. Integrity checks establish consistency with the accompanying manifest, not who created it. See [the format](FORMAT.md) and [security scope](../SECURITY.md).

## Contribution boundary

Changes to the open foundation belong here. Private source material, personal conversations, production containers, credentials, and proprietary resident or product code do not. Demonstrations and regression tests use synthetic material. The public repository has its own clean history; private product records are not part of that history.

The public development path welcomes use, independent examination, contributions, and inquiries. The open core is intended to remain useful regardless of any particular commercial application or funding outcome.
