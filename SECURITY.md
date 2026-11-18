# Security scope and reporting

Mella Core is an early local record and recovery library with a local stdio MCP adapter. It has no network listener, model service, hosted account system, or remote multi-user isolation layer.

## Trust boundary

Run it under an operating-system account you control. A local process with access to the container directory can read its contents. Admitting an MCP host makes the deliberately included shared library available to that host; the host may transmit retrieved content to a model provider according to its own behavior and settings. A self-reported client name is not authenticated identity.

The MCP `--read-only` mode exposes no mutation tools, but it is not a filesystem-level read-only mount. Opening the store may create lock/support files. Tool argument validation and bounded operations reduce accidental or unsupported actions. They do not isolate an untrusted process running as the same operating-system user. The low-level current-access list is supplied by the integrating caller; it is not a grant-issuance or identity-verification service.

Imported instructions are source data. The core does not execute imported code. Integrations must maintain the separation between source text, assistant interpretations, and actual authority to act.

## Stored and recovered data

Container files, original event payloads, exports, and snapshots may contain sensitive material supplied by their owner or host. The library does not automatically scrub credentials from imported text or event envelopes. Keep secrets outside the record and review what a host submits.

Snapshots are unencrypted. Integrity checks detect inconsistencies with their manifest; they do not authenticate an archive's creator or protect against an attacker replacing both the database and its manifest. Protect storage and backups accordingly. Restoration uses a new destination, rejects unsupported archive layouts and schemas, and does not activate historical grants or launch archived operations.

Use the supported import and snapshot size limits documented in [FORMAT.md](docs/FORMAT.md). Do not expose the stdio adapter through a remote gateway without separately designing and testing authentication, authorization, transport protection, and resource controls.

## Report a vulnerability

Email `anni@fieldlight.com` with the subject **Mella Core security report**. Begin with the affected version, a short description, and a synthetic reproduction if possible. Do not send passwords, API keys, personal conversation exports, or production containers. Ask for a private transfer method if more sensitive evidence is necessary.

The project has no guaranteed security-response service level. Supported fixes and affected release details will be recorded when a report is addressed. For ordinary bugs without a security consequence, use a public issue with synthetic data.
