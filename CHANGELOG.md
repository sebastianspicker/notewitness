# Changelog

This file records user-visible changes. The project uses semantic versioning with Python prerelease
identifiers.

## [Unreleased]

- Honor human review decisions everywhere: `review-accept` refuses a
  suggestion that was already rejected; transcript and music exports skip
  rejected suggestions; rejected speaker alignments no longer label speakers;
  and lesson notes, key moments, and practice tasks show the latest revised
  text.
- Fix browser recordings being lost when the browser language produces a
  non-Latin-1 time string (the capture name is now percent-encoded).
- Return 422 with the probe's reason instead of dropping the connection when a
  workbench import is rejected by ffprobe or a local tool.
- Generator IDs for local analysis and Whisper runs now hash the complete
  generator record, so a second run with different stages, spans, license
  text, code version, or checkpoint name no longer fails at publication. New
  runs get new generator IDs; existing records are unchanged.
- Analysis jobs: re-enqueueing the same job is idempotent, transient lease
  errors are retried, a cancel that arrives after publication no longer marks
  the job cancelled, failures no longer leave jobs running until lease
  expiry, and `analysis-job` lists the newest jobs first.
- Whisper output whose final end time overshoots the media duration by up to
  100 ms is clamped instead of failing the run; transcripts above the
  2,000-segment graph bound still write their requested export and report
  where the transcript was kept.
- Evidence validation now rejects revision parent cycles, revisions of records
  other than events and relations, non-human adjudicators, and timestamps that
  are not RFC 3339.
- Workbench runtime configuration accepts only version 2; version 1 files are
  rejected with `runtime_config_version_unsupported`.
- Workbench UI: key-moment time buttons seek to the right time, retry resumes
  status polling, Esc closes dialogs for good, conflict errors keep their
  reason, the tempo survives reloads, and millisecond timecodes round
  correctly.
- The public-hygiene gate also blocks common video and model-weight formats
  and private folder names at any depth; CI pins Node with `actions/setup-node`.
- Remove unused transcription and analysis contracts (project lexicon,
  speaker-correction, queue-plan and run-ledger types, six unproduced
  hypothesis types) and the `project_domain_lexicon` capability entry.
- Describe exports accurately: they are gated by explicit operator
  authorization and the loss preview; rights records are not interpreted at
  export time.

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
