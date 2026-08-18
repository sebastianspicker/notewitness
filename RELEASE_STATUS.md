# Public alpha status

Historical evidence dates: 2026-08-09 and 2026-08-14

Candidate version: `0.1.0a0`

Status: historical local-audit evidence, not ready to publish

This page records dated audit snapshots. It does not state the current working
tree condition or identify a releasable revision; establish both from Git when
preparing a release.

## 2026-08-14 verification addendum

This addendum describes a checkout rechecked on 2026-08-14. It supersedes only
the dated non-isolated-checkout row below; the remaining 2026-08-09 candidate
evidence also remains historical.

- `bash scripts/verify.sh` passed in that checkout: public hygiene,
  JSON and CLI checks, 342 Python tests, JavaScript contracts, and the
  deterministic Pages build all pass.
- Current `.gitignore` rules exclude `.repowise/`, `.claude/`, and `.mcp.json`;
  `git check-ignore -v` confirms those local-tool paths no longer block public
  hygiene.
- `git diff --check` passes.
- Wheel/install proof remains unavailable because this host has Python 3.14.6,
  lacks Python 3.11 and Hatchling, and no dependency installation was
  authorized. Rendered browser and assistive-technology proof also remains
  unavailable because the in-app browser exposes no backend.
- Asset-license confirmation, a verified private vulnerability-reporting
  channel, screenshot-manifest evidence, and remote Actions/Pages results are
  still publication blockers.

## 2026-08-09 candidate identity

- Branch: `agent/remediate-codacy-findings`
- HEAD: `65aa70273a6ca06ffa5fd5ed770ba1cd574c9476`
- Tracked files: 206
- Tracked modifications: 28
- Staged files: 0
- Configured origin: present locally; remote state was not queried or changed
- Upstream tracking branch: gone

HEAD did not identify the working-tree candidate represented by this snapshot.

## Implemented scope

The current package provides a dependency-free Python evidence workbench for private
music-teaching research projects. Implemented paths cover project creation, evidence validation,
media ingest, operator-supplied transcription and analysis adapters, durable local jobs,
append-only human review, rights-gated exports, an optional explicitly authorized OpenAI text
path, and a session-authenticated loopback browser workbench.

The repository does not establish model accuracy, fairness, pedagogical effectiveness, corpus
suitability, or compatibility with a particular external runtime.

## 2026-08-09 local verification

These results apply only to the 2026-08-09 dirty-working-tree candidate described above.

| Command or check | Result |
|---|---|
| `bash scripts/verify.sh` in the non-isolated checkout | Blocked before tests because the fail-closed public-hygiene scan correctly saw untracked local `.repowise/` databases and `.mcp.json`. Those local-tool paths were outside the audit candidate and were preserved. |
| `bash scripts/verify.sh` in an isolated candidate copy excluding only `.repowise/`, `.claude/`, and `.mcp.json` | Passed. Public hygiene, JSON validation, 303 Python tests in 118.010 seconds, CLI smoke checks, JavaScript syntax and contract checks, and the deterministic Pages artifact build all passed. |
| Focused facade and transcription tests | Passed. 38 tests completed in 50.006 seconds after the analyzer-driven cleanup. |
| Configured local Codacy scan | Completed across all 153 Python files with Ruff 0.12.7, Bandit 1.8.3, and Pylint 3.3.9. All tools succeeded with no crashes. The scan reported 1,527 findings, dominated by documentation and policy-threshold noise; no Bandit High finding affected `src/`. |
| Focused Codacy rescan of the three remediated Python files | Ruff reported 0 issues. Pylint reported 15 remaining documentation and complexity findings. The targeted F401/W0611 findings were absent. |
| Pages artifact generation and privacy scan | Passed locally from the real renderer and synthetic fixture. The generated artifact was served on loopback, but rendered interaction proof was blocked because the in-app Browser runtime had no available browser backend. |
| GitHub workflow reference check | Passed. Every external `uses:` reference in both workflows is pinned to a 40-character commit SHA, and both workflow files parse as YAML. |
| `git diff --check` | Passed after the remediation batches. |
| Wheel build and installed-package smoke test | Blocked. The available Python is 3.14.6, Python 3.11 is unavailable, and Hatchling is not installed. The documented isolated build would resolve/install the backend, which this audit did not authorize. |
| Coverage ingestion | Blocked. `coverage.py` is not installed, and the repository does not produce a coverage artifact in its maintained gate. |
| GitHub Actions and Pages deployment | Not run. No commit, push, deployment, or remote mutation was authorized. |

## Screenshot evidence

The README references three 1440 by 900 design-reference screenshots. They remain working-tree
design references, not release evidence, because `docs/screenshots/manifest.json` does not exist
and that candidate was not rendered in an available browser backend during this audit.

## Publication blockers

1. Confirm and document license terms for schemas, documentation, screenshots, and the synthetic
   fixture.
2. Configure and independently verify a private vulnerability-reporting channel, then update
   `SECURITY.md` and the code-of-conduct contact guidance.
3. Review or explicitly scope the remaining configured-analyzer policy debt. Do not suppress it
   merely to obtain a clean count.
4. Build and install a wheel in an approved Python 3.11 environment with the reviewed Hatchling
   backend, then run the installed-package checks in `docs/RELEASING.md`.
5. Obtain rendered browser and assistive-technology evidence for the workbench and static Pages
   artifact, then create the required screenshot manifest.
6. Create one immutable candidate only after approval, rerun the complete gate on that exact
   commit, and obtain passing GitHub Actions and Pages results.

## Accepted alpha limitations

- No models, model weights, external executables, media, scores, or empirical corpus are bundled.
- External-tool execution is supported only on macOS and does not provide filesystem confinement
  for approved tools.
- Browser, codec, microphone, capture-device, and external-runtime compatibility is not
  established.
- Session authentication cannot defend against a malicious process with the same user's
  filesystem authority.
- Model quality and research conclusions remain unevaluated.
- Public APIs, schemas, and project data formats may change during the alpha series.

## Release control

Publication and Git history changes require explicit maintainer approval. Nothing in this local
audit authorizes staging, committing, pushing, tagging, publishing, or deployment.
