# Changelog

This file records user-visible changes. The project uses semantic versioning with Python prerelease
identifiers.

## [Unreleased]

- Require a per-process workbench session before private API, job, media, or mutation access.
- Make SQLite sidecar permission handling safe when transient WAL files disappear during concurrent
  workbench operations.
- Add `notewitness --version` and extend installed-package CI smoke checks.
- Include the complete AGPL-3.0 license text.

## [0.1.0a0] - Unreleased candidate

Proposed first alpha of the local-first evidence workbench. Scope and limitations are recorded in
[`docs/releases/0.1.0a0.md`](docs/releases/0.1.0a0.md).
