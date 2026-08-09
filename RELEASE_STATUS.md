# Public alpha status

Status reviewed: 2026-08-09

Candidate version: `0.1.0a0`

Status: local dirty-working-tree candidate, not ready to publish

## Candidate identity

- Branch: `agent/remediate-codacy-findings`
- HEAD: `65aa70273a6ca06ffa5fd5ed770ba1cd574c9476`
- Tracked files: 206
- Tracked modifications: 28
- Staged files: 0
- Configured origin: present locally; remote state was not queried or changed
- Upstream tracking branch: gone

HEAD does not identify the working-tree candidate. The exact candidate-content digest, status
fingerprint, exclusions, and remediation evidence are recorded in `AUDIT_LEDGER.md`.

## Implemented scope

The current package provides a dependency-free Python evidence workbench for private
music-teaching research projects. Implemented paths cover project creation, evidence validation,
media ingest, operator-supplied transcription and analysis adapters, durable local jobs,
append-only human review, rights-gated exports, an optional explicitly authorized OpenAI text
path, and a session-authenticated loopback browser workbench.

The repository does not establish model accuracy, fairness, pedagogical effectiveness, corpus
suitability, or compatibility with a particular external runtime.

## Current local verification

These results apply only to the 2026-08-09 dirty-working-tree candidate described above.

| Command or check | Result |
|---|---|
| `bash scripts/verify.sh` in the live checkout | Blocked before tests because the fail-closed public-hygiene scan correctly sees untracked local `.repowise/` databases and `.mcp.json`. Those local-tool paths are outside the audit candidate and were preserved. |
| `bash scripts/verify.sh` in an isolated candidate copy excluding only `.repowise/`, `.claude/`, and `.mcp.json` | Passed. Public hygiene, JSON validation, 303 Python tests in 118.010 seconds, CLI smoke checks, JavaScript syntax and contract checks, and the deterministic Pages artifact build all passed. |
| Focused facade and transcription tests | Passed. 38 tests completed in 50.006 seconds after the analyzer-driven cleanup. |
| Configured local Codacy scan | Completed across all 153 Python files with Ruff 0.12.7, Bandit 1.8.3, and Pylint 3.3.9. All tools succeeded with no crashes. The scan reported 1,527 findings, dominated by documentation and policy-threshold noise; no Bandit High finding affects `src/`. Exact dispositions are in `AUDIT_LEDGER.md`. |
| Focused Codacy rescan of the three remediated Python files | Ruff reported 0 issues. Pylint reported 15 remaining documentation and complexity findings. The targeted F401/W0611 findings were absent. |
| RepoWise current-tree inventory | All 188 enumerated source, test, script, and workflow targets resolved. No target was unindexed. Coverage data remains unavailable. |
| Pages artifact generation and privacy scan | Passed locally from the real renderer and synthetic fixture. The generated artifact was served on loopback, but rendered interaction proof was blocked because the in-app Browser runtime had no available browser backend. |
| GitHub workflow reference check | Passed. Every external `uses:` reference in both workflows is pinned to a 40-character commit SHA, and both workflow files parse as YAML. |
| `git diff --check` | Passed after the remediation batches. |
| Wheel build and installed-package smoke test | Blocked. The available Python is 3.14.6, Python 3.11 is unavailable, and Hatchling is not installed. The documented isolated build would resolve/install the backend, which this audit did not authorize. |
| Coverage ingestion | Blocked. `coverage.py` is not installed, and the repository does not produce a coverage artifact in its maintained gate. |
| GitHub Actions and Pages deployment | Not run. No commit, push, deployment, or remote mutation was authorized. |

## Screenshot evidence

The README references three 1440 by 900 design-reference screenshots. They remain working-tree
design references, not release evidence, because `docs/screenshots/manifest.json` does not exist
and the current candidate was not rendered in an available browser backend during this audit.

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
