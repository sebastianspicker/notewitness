# Contributing to NoteWitness

Contributions should be narrow, evidence-backed, and consistent with the local-first privacy
boundary and append-only human-review model.

## Before changing code

1. Read `README.md` and the relevant architecture or operator guide.
2. Open an issue for material behavior, schema, privacy-boundary, or dependency changes.
3. Do not include lesson media, participant identifiers, credentials, model
   artifacts, runtime project directories, restricted scores, or private
   diagnostics in an issue or patch.
4. Do not add a production dependency without maintainer approval.

## Development

Python 3.11 or newer and Node.js are required for the documented local gate. The core runtime has
no production dependencies.

```sh
PYTHONPATH=src python3 -m notewitness --version
PYTHONPATH=src python3 -m unittest discover -s tests -t . -v
bash scripts/verify.sh
```

Add focused tests for the contract or failure mode that motivates a change. Keep automatic model
output, normalized hypotheses, accepted annotations, and summaries as separate layers. Networked
or model-specific behavior belongs behind an explicit adapter and must fail closed.

## Pull requests

Keep patches narrow, explain user-visible behavior and privacy implications, list every check run,
and name skipped checks or environmental blockers. A pull request is not release approval; tags,
packages, and GitHub releases follow [`docs/RELEASING.md`](docs/RELEASING.md).
