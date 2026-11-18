# Mella Core contributor instructions

This is the public open-source record, recovery, and reference-connection core of Mella. Read README.md, docs/FORMAT.md, docs/BOUNDARY.md, and SECURITY.md before changing behavior.

- Keep this repository independently usable. Do not import the private Mella application, depend on a developer's checkout, or add personal filesystem paths.
- Preserve original UTF-8 imports, unfamiliar metadata, branches, attribution, immutable revisions, and received work from interrupted conversations. Reading views are derived.
- Keep schema identifiers stable. Format changes require explicit compatibility and migration decisions.
- Preserve `mella.decisions/1`, exact original anchors, attributed readings, explicit scope and relation history. The pure reducer replays recorded meaning; do not add keyword extraction or authority grants. Keep source validators and offline replay usable without a private writer, provider, or model.
- An attachment reference is not an included file. Snapshot receipts must distinguish recovered records from unresolved external dependencies.
- Restore only into fresh destinations. Never overwrite a live container as part of verification or a migration test.
- All deliberately included records belong to the shared interior. Context selection is loading priority. Current-access inputs and MCP client labels are not an authenticated grant or identity service.
- Keep model execution, resident planning, proprietary UI, provider credentials, and private product material outside this repository. Do not reintroduce them through examples or tests.
- The reference MCP adapter is local stdio with an explicit data directory. It must not silently contact a network service, collect host conversations, or launch a model.
- Use Python's standard library for runtime functionality unless a reviewed change establishes a specific dependency need. Target macOS and Linux; do not claim Windows support without implementation and tests.
- Use synthetic fixtures and temporary directories. Keep databases, snapshots, logs, credentials, and model files out of commits.
- Run `python3 -m unittest discover -s tests -v` and relevant documented smoke checks. Verify failure behavior as well as successful round trips.
- Source files use SPDX-License-Identifier: MPL-2.0. Preserve notices and the separate brand boundary.
- Distinguish a local pass, CI pass, pushed commit, and published release. Report only the state actually observed.
