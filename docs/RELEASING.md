# Releasing NoteWitness

Release from an immutable commit after the complete local and hosted checks
pass. Publishing a tag, package, or GitHub release requires explicit maintainer
approval.

## Prepare the candidate

1. Confirm that `pyproject.toml`, `src/notewitness/__init__.py`, and
   `CHANGELOG.md` use the intended version.
2. Review `git status --short --ignored` and `git ls-files`. Exclude private
   projects, media, models, credentials, caches, and local tool state.
3. Review the license terms for code, schemas, documentation, examples, and
   bundled assets.
4. Verify the private vulnerability-reporting route described in
   [SECURITY.md](../SECURITY.md).

## Verify and package

```sh
bash scripts/verify.sh
python3 -m pip wheel --no-deps . --wheel-dir dist
```

The wheel build creates an isolated build environment and may resolve the
Hatchling backend declared in `pyproject.toml`. Use a reviewed release
environment and record the resolved backend version with the release evidence.

Install the wheel into a fresh Python 3.11 environment. From outside the
checkout, verify:

```sh
notewitness --version
notewitness --help
notewitness capabilities
notewitness validate /absolute/path/to/checkout/examples/synthetic-lesson/project.json
notewitness-provider-bridge --help
notewitness-mt3-events-bridge --help
```

The bridge commands currently reject `--help` with exit status 2 because their
protocol accepts only `--request request.json`. Also import
`notewitness.workbench.server` from the installed package and confirm the
top-level browser assets checked by CI are present.

Run the complete gate again on the exact candidate commit and require the
GitHub Actions workflow to pass. Any change after verification creates a new
candidate.

## Publish

After approval:

1. Create the annotated version tag on the verified commit.
2. Push the commit and tag.
3. Wait for required GitHub checks.
4. Create a GitHub prerelease from the matching `CHANGELOG.md` entry.
5. Verify the public file list, source archive, package metadata, and license.

Package-index publication is a separate release action and is not implied by a
GitHub prerelease.
