# Mella Core

**A persistent record that you can inspect, recover, and carry forward.**

Mella Core is the open-source record and recovery foundation of Mella, a user-side workspace for continuing work across AI participants. It preserves original sources, conversation branches, attribution, revisions, and work events in a local container. Its tools work without a Mella subscription, model, hosted account, or network connection.

Created by Anni McHenry at [Fieldlight Institute](https://fieldlight.com/).

This first release is an early developer preview for macOS and Linux with Python 3.9 or newer. It is extracted from Mella's working local application. The full Mella app and resident intelligence are developed separately; Core does not run a model or promise autonomous system-wide control.

## Try a complete example

From a checkout, no third-party Python runtime dependencies are needed:

```sh
git clone https://github.com/fieldlight-institute/mella-core.git
cd mella-core
python3 -m examples.roundtrip --output ./demo-output
```

Open `demo-output/reader.html` in a browser. It is a standalone, offline reading copy of a fictional community-garden project. The example checks exact original text, two conversation branches, unfamiliar metadata, an older note revision, pinned context, a recorded conversation, and an independently restored container. It uses synthetic events and makes no model calls.

`demo-output/result.json` records the result. Use a different output directory to rerun: the demonstration never replaces an existing one.

## Your own local record

```sh
python3 -m mella_core --data-dir ./data init
python3 -m mella_core --data-dir ./data import path/to/project.md
python3 -m mella_core --data-dir ./data list
python3 -m mella_core --data-dir ./data reader --output ./my-record.html
python3 -m mella_core --data-dir ./data export --output ./my-record.json
python3 -m mella_core --data-dir ./data snapshot
```

The snapshot command prints its archive path. Validate and restore it into a new directory:

```sh
python3 -m mella_core verify path/to/snapshot.zip
python3 -m mella_core restore path/to/snapshot.zip --to ./recovered
python3 -m mella_core --data-dir ./recovered list
```

Use `read RECORD_ID --version 1` to inspect an older version, or `read RECORD_ID --format original` to recover the exact original imported file. The JSON export contains every stored revision and event, with exact payload bytes alongside readable values. Snapshots are the supported restore input. HTML and JSON exports are private reading/interchange copies, not uploads.

An optional local installation provides `mella-core` and `mella-core-mcp` commands:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/mella-core --help
```

## What is in Core

| Capability | Behavior |
|---|---|
| Original imports | Exact UTF-8 source retained alongside a derived reading view; supported conversation JSON keeps alternate branches and unknown metadata. |
| Versioned records | Revisions append. A context pinned to version 1 continues to reference version 1 after version 2 is added. |
| Conversation journal | Original event objects and received partial work are retained; a consumer can reconstruct the reading view after reopening. |
| Independent reading | CLI, complete JSON export, and an escaped, script-free offline HTML reader. |
| Recovery | Consistent SQLite snapshots, lossless compression, manifests, integrity checks, and restoration to a fresh destination. |
| Reference connection | Local MCP tools and resources for an explicitly selected container; optional read-only mode. |

All deliberately included records are available to an admitted local client. Selected context is a loading preference. The current adapter trusts local processes and self-reported participant labels; it is not an authenticated multi-user permission service.

## Connect an agent

Configure a compatible MCP host to start this command from an installed environment:

```sh
mella-core-mcp --data-dir /absolute/path/to/container --read-only
```

Or run `python3 -m mella_core.mcp` from the checkout with the same arguments. The adapter supports the MCP `2025-06-18` stdio lifecycle, tools, and resources subset. Read-only mode exposes listing, retrieval, and existing context; omitting it also permits the documented local capture/context/snapshot operations. A host may transmit retrieved records to its model provider. Core itself makes no network requests, runs no model, and does not automatically capture conversations held in that host.

See [the record and connection format](docs/FORMAT.md) for schemas and API details.

## Preservation boundaries

- Snapshots contain the stored record, including historical revisions and provider events that were supplied. A media reference is not its file: missing attachments remain declared dependencies.
- Snapshots do not package model weights, provider installations, or outside session files. This release does not collect credentials; it also does not scrub secrets already present in an imported source.
- Snapshots and reading exports are **not encrypted**. Checksums detect corruption and inconsistent contents; they do not authenticate the author of a deliberately rewritten archive.
- Imports are limited to 20 MiB. Each uncompressed snapshot member, including the complete database, is limited to 64 MiB. Oversized capture fails explicitly.
- Restoration never executes archived code or reactivates historical permissions. It creates an independent copy and never overwrites a destination.
- The current release targets local macOS/Linux use. Windows support, scheduled preservation, and a full permission broker remain future work.

## Open foundation and commercial product

Core's record format, preservation, reader, and reference connections are open under **MPL-2.0**. The complete Mella workspace and resident's planning, relevance, and coordination implementation remain separate commercial development. The person's accumulated record and working state remain recoverable independently.

[The boundary](docs/BOUNDARY.md) explains the separation, including future authority enforcement and the distinction between a resident's implementation and the user's retained state. Mella's name and visual identity are not licensed for use as another product's identity.

## Development

```sh
python3 -m unittest discover -s tests -v
```

Tests cover preservation failures, source fidelity, revisions and stale-write rejection, event replay, concurrent turn admission, MCP protocol behavior, CLI recovery, and escaped offline reading. See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md). The source is intentionally free of private working records and provider credentials.

For product, collaboration, or investment interest: [anni@fieldlight.com](mailto:anni@fieldlight.com).

[License](LICENSE) · [Notices](NOTICE) · [Architecture boundary](docs/BOUNDARY.md)
