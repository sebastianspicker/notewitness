# Documentation

New here? Start with the root [README](../README.md) for what NoteWitness is,
how to install it, and how to run the workbench. The pages below go deeper into
specific contracts and procedures.

## Start here

- [Capability matrix](capabilities.md) — what is implemented today, and what is
  explicitly out of scope.
- [Product guidance](product.md) — who NoteWitness is for and the interface
  rules it follows.
- [Architecture](architecture.md) — components, dependency direction, and trust
  boundaries.

## Run and operate

- [Operator guide](operator-guide.md) — the local workflow end to end.
- [Runtime example](workbench-runtime.example.json) — the JSON that approves
  local engines for workbench jobs.
- [Releasing](RELEASING.md) — preparing, verifying, and publishing an alpha.

## Extend

- [Analysis protocol](analysis-suite-protocol.md) — the JSON CLI contract for an
  external analysis suite.
- [Provider bridges](provider-bridges.md) — the packaged Basic Pitch, pyannote,
  and PANNs adapters.
- [OpenAI endpoint](openai-endpoint.md) — the optional, text-only remote
  suggestion path.

## See it first

- [Screenshot tour](https://sebastianspicker.github.io/notewitness/tour.html) and
  the [interactive mock-data demo](https://sebastianspicker.github.io/notewitness/).

The README, capability matrix, and `notewitness capabilities` output describe
current behavior. [RESEARCH_REPORT.md](../RESEARCH_REPORT.md) is a dated research
landscape and design proposal; it is not an implementation or release contract.
