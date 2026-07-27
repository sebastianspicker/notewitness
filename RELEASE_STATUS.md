# Public alpha status

Status reviewed: 2026-07-24

Candidate version: `0.1.0a0`

Status: local working-tree candidate, not ready to publish

The repository has no commits, tags, configured remote, or tracked files. The
current filesystem has no immutable release identity.

## Implemented scope

The current package provides a dependency-free Python evidence workbench for
private music-teaching research projects. Implemented paths cover project
creation, evidence validation, media ingest, operator-supplied transcription
and analysis adapters, durable local jobs, append-only human review,
rights-gated exports, an optional explicitly authorized OpenAI text path, and a
session-authenticated loopback browser workbench.

The repository does not establish model accuracy, fairness, pedagogical
effectiveness, corpus suitability, or compatibility with a particular external
runtime.

## Local validation

The following results were measured on 2026-07-24 with Python 3.14.6 and Node.js
22.23.1:

| Command or check | Result |
|---|---|
| `bash scripts/verify.sh` | Passed. 295 Python tests passed in 26.775 seconds. Public hygiene, JSON validation, CLI smoke tests, JavaScript syntax checks, tuner checks, and workbench UI contracts also passed. |
| Documented project creation, validation, and inspection example | Passed against a new private project under `/private/tmp`. |
| Prospective 196-file checkout copy | Passed from an isolated `/private/tmp` copy with fresh Git metadata. The 295 Python tests passed in 28.145 seconds; the remaining verification steps and documented checkout commands also passed. |
| Active Markdown local-target scan | Passed. Every referenced local file exists. |
| External documentation link check | Seventy-two URLs returned successful responses. The OpenAI API endpoint returned the expected authentication-required response. Two TELMI pages timed out during direct requests but remained available through current search indexes. |
| Screenshot file check | Passed. All three current PNG files are 1440 by 900 pixels. |
| `pyright src tests` | Failed with 204 errors, including unresolved optional provider packages and source/test typing findings. Pyright is installed locally but is not configured as a repository gate. |
| `python3 -m pip wheel . --no-deps --no-build-isolation` | Failed after metadata preparation because `hatchling.build` is unavailable in the local environment. |
| Formatter and linter | Not run. Ruff, Black, mypy, and Hatch are not installed, and `pyproject.toml` defines no formatter or linter. |
| Python 3.11 installed-package check | Not run. Python 3.11 and Hatchling are not installed locally. |
| GitHub Actions | Not run. The repository has no remote or immutable candidate. |

The broad gate is the maintained repository check. The Pyright result remains
useful diagnostic evidence, but it is not part of that gate.

## Screenshot evidence

The README references these files:

| File | SHA-256 |
|---|---|
| `docs/screenshots/lesson-notes.png` | `4babbd25a5044315c9e444fdf4e93fb53667a4ffea865687b5804c664606f7ca` |
| `docs/screenshots/review-boundary.png` | `4efcb111dd10159f8cd8f47b7a9a4755f3b16ac3d40b00097a6b0265a3e4d914` |
| `docs/screenshots/workbench-overview.png` | `77c37132be786d9ceccfa91b05a0471bfd15935050b1e8b7d0eac81adf708936` |

The files match the required dimensions. Their exact browser version, capture
time, and candidate commit cannot be verified from the repository because
`docs/screenshots/manifest.json` does not exist. Treat them as working-tree
design references, not release evidence.

## Publication blockers

1. Confirm and document license terms for schemas, documentation, screenshots,
   and the synthetic fixture.
2. Configure and verify a private vulnerability-reporting channel, then update
   `SECURITY.md` and the code-of-conduct contact guidance.
3. Resolve or explicitly scope the current Pyright findings.
4. Build a wheel in a reviewed environment, install it into a clean Python 3.11
   environment, and run the installed-package checks in
   [docs/RELEASING.md](docs/RELEASING.md).
5. Create an immutable candidate only after approval, rerun the complete gate on
   that exact commit, and obtain passing GitHub Actions results.
6. Recapture or verify screenshots against that candidate and add the required
   manifest.

## Accepted alpha limitations

- No models, model weights, external executables, media, scores, or empirical
  corpus are bundled.
- External-tool execution is supported only on macOS and does not provide
  filesystem confinement for approved tools.
- Browser, codec, microphone, capture-device, and external-runtime
  compatibility is not established.
- Session authentication cannot defend against a malicious process with the
  same user's filesystem authority.
- Model quality and research conclusions remain unevaluated.
- Public APIs, schemas, and project data formats may change during the alpha
  series.

## Release control

Publication and Git history changes require explicit maintainer approval.
