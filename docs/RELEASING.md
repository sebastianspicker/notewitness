# Releasing NoteWitness

Release from an immutable commit after the complete local and hosted checks
pass. Publishing a tag, package, or GitHub release needs explicit maintainer
approval.

## Prepare the candidate

1. Confirm the intended version in `pyproject.toml`,
   `src/notewitness/__init__.py`, and `CHANGELOG.md`.
2. Review `git status --short --ignored` and `git ls-files`. Exclude private
   projects, media, models, credentials, caches, and local tool state.
3. Review the license terms for code, schemas, documentation, examples, and
   bundled assets.
4. Verify the private vulnerability-reporting route in
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

Run the complete gate again on the exact candidate commit, and require the
GitHub Actions workflow to pass. Any change after verification creates a new
candidate.

## Publish

After approval:

1. Create the annotated version tag on the verified commit.
2. Push the commit and tag.
3. Wait for the required GitHub checks.
4. Create a GitHub prerelease from the matching `CHANGELOG.md` entry.
5. Verify the public file list, source archive, package metadata, and license.

Package-index publication is a separate release action; a GitHub prerelease does
not imply it.

## Publish the GitHub Pages demo

The Pages workflow publishes a browser-only mock-data application, not a local
workbench instance. Prepare it from the same immutable candidate used for the
repository checks:

```sh
demo_root="$(mktemp -d)"
bash scripts/verify.sh
bash scripts/build_pages_demo.sh "$demo_root/site"
python3 -m http.server 8000 --bind 127.0.0.1 --directory "$demo_root/site"
```

Before deployment, inspect `release.json` and confirm its revision and
synthetic-fixture digest match the candidate. Exercise selection, source-span
navigation, accept, revise, reject, filters, theme, and transport in desktop and
narrow layouts. Reload must restore the original mock lesson, and the browser
must make no network or device-access attempts.

The README and the generated `tour.html` embed the committed captures under
`docs/screenshots/`. If the interface changed, recapture them from this candidate
and review every image for private content before staging, because the
public-hygiene gate checks filenames but not image contents.

A push to `main` that affects the demo inputs builds and deploys the artifact. A
maintainer may instead use the workflow's manual dispatch. After deployment, open
the environment URL reported by GitHub Actions, confirm the published
`release.json` names the candidate revision, and repeat the primary interaction
smoke test. Preparing the artifact locally does not authorize a push or Pages
deployment.
