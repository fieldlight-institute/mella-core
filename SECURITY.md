# Security scope and reporting

Mella Core is an early local record and recovery library with a local stdio MCP adapter. It has no network listener, model service, hosted account system, or remote multi-user isolation layer.

## Trust boundary

Run it under an operating-system account you control. A local process with access to the container directory can read its contents. Admitting an MCP host makes the deliberately included shared library available to that host; the host may transmit retrieved content to a model provider according to its own behavior and settings. A self-reported client name is not authenticated identity.

The MCP `--read-only` mode exposes no mutation tools, but it is not a filesystem-level read-only mount. Opening the store may create lock/support files. Tool argument validation and bounded operations reduce accidental or unsupported actions. They do not isolate an untrusted process running as the same operating-system user. The low-level current-access list is supplied by the integrating caller; it is not a grant-issuance or identity-verification service.

Imported instructions are source data. The core does not execute imported code. Integrations must maintain the separation between source text, assistant interpretations, and actual authority to act.

The decision reader validates reserved envelopes, contiguous sequence, source hashes, exact quotes and character ranges, original human-event linkage, participant labels, and scope-bounded relations before replay. One-turn tasks are retained separately from standing directions and cannot alter them. It does not authenticate the recorded speaker or attest that the interpretation matches their intent. A local database owner can construct another internally consistent record. Neither a current direction nor a claimed speaker grants permission to act.

Decision reading sets `query_only=ON` before accessing stored SQLite content and includes committed WAL data. It tries read-only admission first. Some SQLite builds require read/write admission to create missing WAL/SHM sidecars; that fallback still forbids data SQL writes and cannot create a missing database. Incidental SQLite sidecars are possible. If filesystem admission fails, only when there is no WAL and database bytes match its adjacent snapshot manifest may the reader create a private temporary frozen copy and read that copy with immutable mode. It never uses immutable mode on a live database or silently drops live WAL contents. Its exports include the complete decision journal and referenced originals across scopes, not a redacted sharing view; outputs are created with mode 0600 and never overwrite existing paths. Offline HTML escapes source content and has no scripts or external assets.

## Stored and recovered data

Container files, original event payloads, exports, and snapshots may contain sensitive material supplied by their owner or host. The library does not automatically scrub credentials from imported text or event envelopes. Keep secrets outside the record and review what a host submits.

Snapshots are unencrypted. Integrity checks detect inconsistencies with their manifest; they do not authenticate an archive's creator or protect against an attacker replacing both the database and its manifest. Protect storage and backups accordingly. Restoration uses a new destination, rejects unsupported archive layouts and schemas, and does not activate historical grants or launch archived operations.

Use the supported import and snapshot size limits documented in [FORMAT.md](docs/FORMAT.md). Do not expose the stdio adapter through a remote gateway without separately designing and testing authentication, authorization, transport protection, and resource controls.

## Report a vulnerability

Email `anni@fieldlight.com` with the subject **Mella Core security report**. Begin with the affected version, a short description, and a synthetic reproduction if possible. Do not send passwords, API keys, personal conversation exports, or production containers. Ask for a private transfer method if more sensitive evidence is necessary.

The project has no guaranteed security-response service level. Supported fixes and affected release details will be recorded when a report is addressed. For ordinary bugs without a security consequence, use a public issue with synthetic data.
