# Contributing to NoteWitness

Thanks for helping. NoteWitness is a local-first, privacy-preserving tool, so
contributions need to be narrow, evidence-backed, and careful with other
people's data.

## Before you change code

1. Read [README.md](README.md) and the relevant architecture or operator guide.
2. Open an issue before changing material behavior, schemas, the privacy
   boundary, or dependencies.
3. Keep lesson media, participant identifiers, credentials, model artifacts,
   runtime project directories, restricted scores, and private diagnostics out
   of issues and patches.
4. Ask a maintainer before adding a production dependency.

## Development

Python 3.11 or newer and Node.js are required for the documented local gate. The
core runtime has no production dependencies.

```sh
PYTHONPATH=src python3 -m notewitness --version
bash scripts/verify.sh
```

Add focused tests for the contract or failure mode that motivates your change.
Keep automatic model output, normalized hypotheses, accepted annotations, and
summaries as separate layers. Put networked or model-specific behavior behind an
explicit adapter, and make it fail closed.

## Pull requests

Keep patches narrow. Explain the user-visible behavior and any privacy
implications, list every check you ran, and name skipped checks or environmental
blockers. A pull request is not release approval: tags, packages, and GitHub
releases follow [docs/RELEASING.md](docs/RELEASING.md).
