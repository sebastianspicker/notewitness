# 0001: Use a feature-modular monolith

Status: accepted

## Context

NoteWitness runs as one local process but previously spread cohesive workflows
across nominal domain, application, adapter, infrastructure, presentation, and
provider layers. The application layer became a dependency hub, while project
persistence and local-tool policy were scattered across package roots.

## Decision

Organize the package around six product responsibilities: `core`, `projects`,
`analysis`, `lessons`, `workbench`, and `interfaces`. Enforce their dependency
direction with an AST contract test. Keep `projects` as the sole canonical
evidence-document mutation authority and keep analysis jobs distinct from
browser queue state. Use small protocols only for actual capture and
analysis-engine substitution.

## Consequences

Each concept has one expected home and feature workflows can be changed without
crossing artificial horizontal layers. The package remains dependency-free at
runtime and deploys as one process. Internal Python paths are not treated as
compatibility contracts; the documented CLI, file, protocol, and loopback UI
contracts remain the external surface.
