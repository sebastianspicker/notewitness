# Releasing NoteWitness

This procedure separates local candidate preparation from publication. Do not stage, commit, tag,
push, upload a package, or create a GitHub release without explicit maintainer approval.

## 1. Freeze the candidate

1. Confirm that `pyproject.toml`, `src/notewitness/__init__.py`, `CHANGELOG.md`, release notes, and
   `RELEASE_STATUS.md` use the same version.
2. Review `git status --short --ignored` and the complete candidate file list.
   Exclude private data, runtime project directories, models, caches, local
   tool state, and private working notes.
3. Confirm the license terms for code, documentation, schemas, fixtures, screenshots, and every
   bundled asset.
4. Capture screenshots only from deterministic synthetic state after the UI is final. Record the
   exact commit and image hashes in the screenshot manifest.
5. Verify the private vulnerability-reporting channel that will be shown on the public repository.

## 2. Verify the working tree

```sh
bash scripts/verify.sh
python3 -m pip wheel --no-deps . --wheel-dir dist
```

The wheel command creates an isolated build environment and may resolve the declared Hatchling
backend. Run it only in an approved release environment with reviewed network and build inputs.
Record the resolved build backend version.

Install the wheel into a fresh Python 3.11 environment without using the source checkout on
`PYTHONPATH`. From outside the repository, verify:

```sh
notewitness --version
notewitness --help
notewitness capabilities
notewitness validate /absolute/path/to/checkout/fixtures/synthetic_lesson/project.json
notewitness-provider-bridge --help
notewitness-mt3-events-bridge --help
```

The two bridge commands currently reject `--help` with exit status 2 because
their installed protocol accepts only `--request request.json`. The package
smoke check must also import `notewitness.presentation.workbench_server` and
confirm that the six top-level entry assets checked by CI are present.

Record exact results and environment blockers in `RELEASE_STATUS.md`. Do not generalize from a
subset pass.

## 3. Create an immutable candidate

After explicit approval, create one intentional commit containing the reviewed public file set. Run
the full gate again on that exact commit and in GitHub Actions. Record the commit SHA in the release
notes and screenshot manifest. Any change after verification creates a new candidate and requires
another run.

## 4. Publish

Only after separate explicit approval:

1. Create the annotated tag `v0.1.0a0` on the verified commit.
2. Push the commit and tag.
3. Wait for required GitHub checks.
4. Create a GitHub prerelease from `docs/releases/0.1.0a0.md`.
5. Verify the public file list, rendered README links, screenshots, source archive, and license.

Package-index publication is outside the first alpha procedure unless separately approved.
