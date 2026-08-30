# Architecture

NoteWitness is a local-first modular application for turning private music
lesson recordings into provenance-linked evidence, review decisions, lesson
projections, and exports. It is one installable process, not a collection of
services. SQLite and project files are local implementation details of that
process.

## System flow

```text
private media / optional score / rights metadata
                    |
             projects (ingest and identity)
                    |
       analysis (approved local executables)
                    |
       core evidence graph and hypotheses
                    |
        lessons (human review and exports)
                    |
       workbench HTTP/UI or interfaces CLI
```

Raw provider output, normalized machine hypotheses, append-only human review,
and derived exports remain separate records. This is the central product
invariant: conclusions can be traced back to source material and the process
that produced them.

## Feature boundaries

The Python package is organized by product responsibility:

- `core` owns dependency-free evidence, time, audio, transcription, analysis,
  lesson, and export value types. It performs no I/O.
- `projects` owns project creation, evidence-graph persistence, private
  artifacts, media import, hashing, file permissions, and atomic mutation.
  It is the only feature allowed to mutate the canonical evidence document.
- `analysis` owns local provider protocols, tool discovery and containment,
  transcription and music-analysis execution, durable analysis jobs, raw
  artifacts, normalization, and run integration.
- `lessons` owns append-only review, actor attribution, lesson projections,
  transcript and music exports, and the separately authorized remote text
  suggestion boundary.
- `workbench` owns the loopback HTTP server, session and request security,
  browser assets, workbench projections, runtime configuration, capture, and
  the browser-requested job queue.
- `interfaces` is the composition boundary. It owns the CLI and installed
  provider-bridge entry points and may depend on every feature.

Dependencies point in one direction:

```text
core
  ^
projects
  ^
analysis
  ^
lessons
  ^
workbench
  ^
interfaces
```

A feature may also depend on any earlier feature in that sequence. Earlier
features must never import later ones, and no feature imports `interfaces`.
`tests/contract/test_architecture.py` enforces this graph from the Python AST.

Private underscore-prefixed modules split large implementations inside a
feature; they are not cross-feature extension points. The deliberate
`notewitness.evidence` module is the sole old public import adapter. New code
uses `notewitness.core.evidence` directly.

## State and side effects

Each project directory owns `project.json`, imported media, rights records,
private raw and export artifacts, and SQLite job stores. Project writes use
owner-only permissions, no-follow opens where relevant, atomic replacement,
and content-hash compare-and-swap checks. Project-scoped runtime writes use a
pinned private-directory capability: direct file creation, staging, sidecar
handling, and unlinking are directory-FD-relative and revalidate the original
device/inode. Analysis leases and checkpoints protect long-running provider
work. The workbench queue is intentionally a separate store because it
represents browser requests and lifecycle, not provider-stage execution.

Python's standard `sqlite3` API cannot open relative to a directory FD, and a
`/dev/fd` SQLite URI is not usable on macOS. Both job stores therefore pin the
parent FD and revalidate its device/inode immediately before and after the
pathname-only SQLite lifecycle. Replacement is detected and the operation is
rejected, although SQLite may touch the replacement target before that
detection. This cannot eliminate a race by a malicious process running as the
same user; that actor is outside the workbench's trust model.

External executables are operator supplied and explicitly identified. The
macOS runner denies network access and bounds process execution, but it does
not confine filesystem access beyond the permissions of the current user.
Provider code, model artifacts, settings, licenses, raw output, and normalized
results retain separate identities.

The optional OpenAI path is not part of local analysis. It sends only selected
text after project policy, rights, and per-call consent checks, uses a fixed
endpoint and `store: false`, and records only machine-suggested relations.

## External interfaces

The supported external surface is intentionally small:

- the `notewitness` CLI and its documented exit statuses;
- `notewitness-provider-bridge` and `notewitness-mt3-events-bridge`;
- the evidence graph, project layout, runtime configuration, analysis-suite
  JSON, and bridge formats documented under `schemas/` and `docs/`;
- the authenticated loopback workbench routes used by the bundled browser UI.

Python modules below those entry points are internal alpha implementation.
Internal paths may change when the feature boundaries remain coherent.

## Workbench trust boundary

The server binds only to `127.0.0.1`. A single-use launch token creates an
`HttpOnly`, host-only, `SameSite=Strict` session cookie. Private reads require
the session; mutations additionally validate Host, Origin, and CSRF data.
Project actor IDs provide evidence attribution, not authentication or
authorization. The process is for one local user and must not be exposed
through a proxy or tunnel.

## Architectural decisions

The rationale for the modular-monolith feature graph is recorded in
[decisions/0001-feature-modular-monolith.md](decisions/0001-feature-modular-monolith.md).
The architecture deliberately avoids generic service layers, shared utility
packages, dependency-injection frameworks, and protocol abstractions unless a
real replaceable boundary needs them.
