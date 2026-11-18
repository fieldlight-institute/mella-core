# Record and snapshot format

Mella Core retains the existing versioned SQLite format so that records and snapshots remain readable independently of the commercial workspace. Application package versions and storage schema versions are separate.

## Version identifiers

| Location | Identifier | Meaning |
|---|---|---|
| Database `meta.schema` | `mella.container-proof/1` | Container tables, record revisions, contexts, and journal |
| Database `meta.app_schema` | `mella.local/1` | Library and conversation payload conventions |
| Snapshot `archive_schema` | `mella.snapshot-proof/1` | Manifest and archive layout |
| Reading export `export_schema` | `mella.core-export/1` | Complete JSON view with exact payload bytes |

The historical `proof` labels are compatibility identifiers, not descriptions of the package release stage. Do not rename them without an explicit migration. Unsupported schemas are rejected; there is no implicit migration that discards unfamiliar material.

## Container layout

A working directory contains `container.sqlite3`, with SQLite journal files as needed, plus directories for snapshots and independently restored copies. Initialization uses a local lock. Protect the whole directory; copying a live database file alone can miss committed data in its journal. Use the capture operation to obtain a consistent backup.

The database has four tables:

| Table | Fields | Purpose |
|---|---|---|
| `meta` | `key`, `value` | Schema, stable container identity, library name, creation time, active context |
| `records` | `record_id`, `version`, `kind`, `sha256`, `raw` | Immutable payload revisions keyed by identity and version |
| `contexts` | `context_id`, `descriptor` | Immutable ordered selections of exact record versions |
| `events` | `seq`, `body` | Contiguous journal binding record and context additions to the stored objects |

Each revision has its own SHA-256 digest. Stable record identity is separate from the digest: a changed record receives a new version rather than pretending to be unrelated material. The application-level `raw` payload is UTF-8 JSON; the lower-level container accepts byte payloads. No archived code is executed during restoration.

## Sources and reading views

A supported text import stores its exact UTF-8 source text as a `source` record. Its derived library records retain a source reference with identity, filename, SHA-256, byte count, and encoding. A Markdown title or normalized conversation view does not replace that original.

Conversation imports accept supported mapping- or message-based JSON structures. Their original objects, unfamiliar fields, and branches remain in `metadata.original`, and the complete original import remains in the source record. The human-readable view and text search follow the selected active path. They do not imply that every original branch was searched.

Library record payloads carry title, provider label, content, creation/update timestamps, messages, branches, and metadata. Source dates and imported labels are retained as source claims. A label derived from an export's shape is distinguished from one explicitly present in the source.

Attachment references are recorded and surfaced as missing payloads when the files were not supplied. The current text/JSON importer does not fetch attachments or traverse the user's filesystem. Retaining an attachment URL is not equivalent to retaining that file.

## Context descriptors

A context descriptor contains its identity, ordered record references, title, creation time, and requested reasoning metadata. Each reference pins `record_id`, `version`, and `sha256`. Changing the selection produces another descriptor. Loading priority does not revoke access to other included records.

The lower-level loader also requires a current allowed-record list supplied by its caller. Archived permission statements do not supply that input. This is an enforcement primitive inside a trusted local integration, not an authenticated permission service. A recorded reasoning setting does not invoke a model.

## Live conversation events

The conversation store records each supplied event as an immutable source record. Events have sequence, conversation identity, time, type, and relevant participant/turn information. They can retain original provider fields alongside normalized events. Checkpoints are derived conversation records; later events can reconstruct received work after the checkpoint.

An interrupted turn preserves received text and its unfinished status. Recovery must not fabricate successful completion. Display views omit internal session mappings and raw envelopes, while the underlying container still retains what was supplied to it. Integrators must keep secrets out of event payloads; the store is not a credential scrubber.

## Reference MCP interface

The local stdio adapter implements the MCP `2025-06-18` lifecycle, tools, and resources subset. It requires an explicit data directory and does not launch a model or record a host conversation automatically. Its default tools list/read records, read/set working context, append a note, and create a snapshot. Record reads accept an optional pinned version.

The `--read-only` tool mode exposes only listing, reading, and context retrieval and requires an existing container. It suppresses tool mutations; opening the store can still create ordinary local lock/support files. The optional `--agent` restriction also excludes its active conversation and internal event sources to avoid recursive reads. Neither mode authenticates the client or isolates it from the operating system.

## Snapshot archive

A snapshot ZIP contains exactly two entries:

- `manifest.json`
- `container.sqlite3`

The manifest contains the archive schema, capture time, container schema and identity, every record revision's identity/kind/hash/size, context digests, journal boundary and digest, database size and digest, and declared external dependencies. The snapshot captures one committed state using SQLite's backup operation. It retains history present at that boundary, including records not selected as active context.

Capture checks record payloads, pinned context references, journal continuity, and correspondence between the journal and stored revisions. Restoration checks the archive layout, database digest, SQLite integrity, allowed schema objects, and the manifest's inventory against the restored database.

Restoration creates a fresh destination containing `container.sqlite3`, `snapshot-manifest.json`, and `restoration.json`. It refuses an existing destination and symlink paths. The final restoration receipt is the completion marker; the three final file moves are not one atomic transaction. Ordinary publication failures trigger cleanup.

The receipt distinguishes included content from unresolved dependencies and states that restoration has not activated historical grants or started execution. A complete database restore does not establish that external media, model weights, credentials, or provider sessions have been recovered.

## Limits and guarantees

The CLI also produces an independent JSON reading/interchange export. It includes all metadata, record versions, contexts, and journal events in one consistent read transaction. Exact raw payloads appear as `raw_base64` alongside decoded JSON values. This export is not a credential-scrubbed sharing format. The snapshot ZIP remains the supported restoration input.

The offline HTML reader includes every stored record version and source object, placing reading views ahead of raw source events. Text and metadata are escaped, no scripts or external assets are loaded, and original objects can be expanded for inspection. It does not run a model or require an account. Both export commands refuse to overwrite an existing output file.

- Text/JSON imports are limited to 20 MiB of UTF-8 source content.
- Each uncompressed snapshot member is limited to 64 MiB, including the complete database and manifest. Capture and restore apply the same member limit.
- MCP input messages have a 25 MiB bound; tool parameters have additional schema limits.
- The first release targets macOS and Linux. Its locking and filesystem behavior are not a claim of Windows support.
- Snapshots are losslessly compressed and unencrypted. Hashes detect inconsistency with the manifest; an attacker able to replace both can construct a different consistent archive. Authenticity and encryption are separate requirements.
- The schema preserves stored user work. It does not automatically collect other applications, import missing files, synchronize devices, or make backups outside the chosen directory.
