# Contributing to Mella Core

Mella Core is the open record, recovery, and connection foundation of Mella. Contributions should make that foundation independently useful and preserve its documented compatibility.

## Before changing behavior

Read [README](README.md), [the format](docs/FORMAT.md), and [the product boundary](docs/BOUNDARY.md). Describe the concrete problem and expected behavior in an issue or pull request. A small, clear change with a reproducible example is easier to review than an unrelated feature bundle.

Preserve original imports, unknown metadata, alternate conversation branches, source attribution, immutable revisions, and interrupted work. A derived view or summary must not silently replace its source. Selection is loading priority inside the shared container, not an inferred permission rule.

Changes affecting on-disk formats need compatibility fixtures and an explicit migration plan. Do not rename schema identifiers to match a package version. Restoration must continue to use a new destination, and failures must not be presented as completed recovery.

## Local checks

Use the setup and commands in [README](README.md). Run the unit and integration suite before submitting:

```sh
python3 -m unittest discover -s tests -v
```

Add behavior tests when changing preservation, parsing, permissions, protocol handling, or failure recovery. Use temporary directories and synthetic records. A test that imports the private Mella application or needs a real account is not a standalone core test.

## Privacy and licensing

Do not include personal archives, real conversations, credentials, local database files, model weights, private paths, or private product material in issues, fixtures, logs, or commits. Use a small invented example that reproduces the problem. Inspect the complete diff before publishing it.

New source files should carry `SPDX-License-Identifier: MPL-2.0`. Contributions must be compatible with the repository's [license](LICENSE), and you must have the right to contribute them. Preserve applicable third-party notices. Introducing a dependency also requires documenting its license and why it is needed.

A contribution to this repository does not authorize use of the Mella name or mark for an unofficial product. Commercial product features outside the [open-core boundary](docs/BOUNDARY.md) should be discussed separately.

## Security reports

Follow [SECURITY.md](SECURITY.md) for suspected vulnerabilities. Please do not post sensitive records or an exploitable disclosure in a public issue.
