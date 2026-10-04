# Changelog

This file records user-visible changes. The project uses semantic versioning with Python prerelease
identifiers.

## [Unreleased]

- Harden local boundaries found by the repository security scan: port-scoped
  workbench header sessions and source-bound media capabilities, bounded
  pre-authentication HTTP handlers, captured media-copy budgets, trusted SQLite
  ancestry, formula-safe CSV refusal, symlink-free Pages inputs, and chunked,
  shape-bounded PANNs inference.
- Redesign the workbench and Pages demo: machine suggestions render
  pencilled (graphite, dashed, open sigil) and accepted evidence inked (solid,
  filled sigil); bundled OFL fonts (Newsreader, Atkinson Hyperlegible Next) served from the loopback asset
  table; serif tabular timecodes in an edition-style margin; a three-step
  listen/read/decide inspector; dedicated tablet and phone layouts that keep
  import, processing, and studio tools reachable; and a theme-aware mark.
  Routes, data contracts, and `data-*` hooks are unchanged.
- Rewrite the documentation set for a general GitHub audience, plain language,
  and consistent structure.
- Add a curated screenshot tour to the README and a `tour.html` page to the
  Pages artifact, captured from the synthetic demo, and allow image files under
  `docs/screenshots/` in the public-hygiene gate.
- Replace the static Pages walkthrough with an interactive, session-only
  Evidence Ledger demo backed exclusively by deterministic synthetic lesson data.
- Require a per-process workbench session before private API, job, media, or mutation access.
- Make SQLite sidecar permission handling safe when transient WAL files disappear during concurrent
  workbench operations.
- Add `notewitness --version` and extend installed-package CI smoke checks.
- Include the complete AGPL-3.0 license text.
- Remove rejected browser captures and imports from `runs/` again; since the
  feature reorganization a failed capture or import left its private staging
  file behind.
- Reorganize the package internals (see `docs/architecture.md` and ADR 0002):
  analysis feature subpackages, one implementation of each private-storage
  primitive, and workbench and lessons modules named by responsibility. The
  `code_surface` module paths printed by `notewitness capabilities` changed
  accordingly; commands, file formats, protocols, and existing routes did not.
- Add `POST /api/review/reject` to record a human rejection of a machine
  evidence suggestion as an append-only revision. Rejected suggestions leave the
  review queue, and a suggestion that was already accepted or rejected can no
  longer be accepted ("was already reviewed"). The review panel offers a
  reject action next to accept.
- Return 422 instead of dropping the connection when a workbench actor already
  exists or a transcript export filename is already taken.
- Derive the durable analysis-job runtime fingerprint from an explicit list of analysis-suite
  modules; jobs enqueued before this change must be re-enqueued rather than resumed.

## [0.1.0a0] - Unreleased

Planned first alpha of the local-first evidence workbench. The current scope
and limitations are documented in [`docs/capabilities.md`](docs/capabilities.md).
