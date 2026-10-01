# 0001: Use a feature-modular monolith

Status: accepted

## Context

NoteWitness runs as one local process, but the code once spread cohesive
workflows across nominal domain, application, adapter, infrastructure,
presentation, and provider layers. The application layer turned into a
dependency hub, and project persistence and local-tool policy were scattered
across package roots.

## Decision

Organize the package around six product responsibilities: `core`, `projects`,
`analysis`, `lessons`, `workbench`, and `interfaces`. Enforce their dependency
direction with an AST contract test. Keep `projects` as the only authority for
canonical evidence-document mutation, and keep analysis jobs distinct from
browser queue state. Use small protocols only where capture and analysis-engine
substitution actually happens.

## Consequences

Every concept has one home, and feature workflows can change without crossing
artificial horizontal layers. The package stays dependency-free at runtime and
deploys as one process. Internal Python paths are not compatibility contracts;
the documented CLI, file, protocol, and loopback UI contracts are the external
surface.
