# 0002: Keep authority layers; give each concept one implementation

Status: accepted

## Context

ADR 0001 organized the package into six packages. Working in that shape
showed where it held and where it did not:

- The package graph stayed acyclic and the security invariants stayed easy to
  state against it, because the packages follow the authority an operation
  needs: pure values, the only project mutator, machine hypotheses, human
  review and exports, then delivery.
- Inside the packages, concepts were not in one place. The private-file and
  SQLite hardening was copied into up to ten modules with subtly different
  checks; transcription and analysis-suite runs duplicated their run-directory
  code; analysis-job orchestration lived in the CLI and was partly repeated in
  the workbench; features were split into underscore modules reachable only
  through forwarding facades, and two of those "private" modules were imported
  across packages.
- Most behavior tests had been deleted, so these copies could drift unnoticed.

A vertical split (transcription, analysis suite, review, exports, workbench)
was considered. Runs, review, and the evidence graph are shared by both
pipelines, so a vertical layout would recreate a shared kernel plus
cross-feature edges and give up the structural enforcement of the invariants.

## Decision

- Keep the six packages and their dependency direction.
- Inside `analysis`, use feature subpackages: `transcription/`, `suite/`, and
  the shared `runs/` lifecycle; keep `local_tools/` as the execution boundary.
- Give each shared primitive one home at the lowest layer that owns its
  meaning: strict JSON in `core/strict_json.py`; owner-private directories,
  no-follow walks, content identities and SQLite files in
  `projects/private_fs.py` and `projects/private_sqlite.py`; the project-media
  path rule in `projects/media.py`; operator-artifact trust in
  `analysis/local_tools`; the run workspace in `analysis/runs`. Where callers
  differ on purpose (error types and messages, create versus replace, repair
  versus reject, nested versus flat media paths, which stat fields form an
  identity), the difference is an explicit parameter or a separate named
  function, never a silent unification.
- Some look-alike checks deliberately stay local: `ProjectStore` keeps its own
  owner and mode errors because their messages are user-facing; the local-tool
  launcher stays self-contained because it runs outside the package; portable
  document reads and the CLI's operator-supplied paths follow symlinks by
  design; each runtime keeps its own status-record fields.
- Name modules by responsibility; package initializers do not re-export, and
  underscore-prefixed modules stay inside their package.
- Enforce these rules mechanically in `tests/contract/test_architecture.py`
  and `tests/contract/test_repository.py`, and keep behavior protected by
  characterization tests restored from the earlier suite.

## Consequences

Each name has one import path and each shared primitive one implementation,
so a reviewer can compare a security check in one place. Internal module paths changed again;
they remain non-contracts, and the `code_surface` strings in
`notewitness capabilities` follow them. Resumable analysis jobs fingerprint an
explicit list of analysis-suite modules, so jobs enqueued before an upgrade
must be re-enqueued.
