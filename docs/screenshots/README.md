# Workbench screenshot policy

Public screenshots must show the real workbench UI modules with synthetic data. They are interface
evidence, not proof that an external model is installed, accurate, licensed, or suitable for a
corpus.

## Curated alpha set

Capture exactly these reviewed PNG files at a 1440 x 900 CSS-pixel viewport:

1. `workbench-overview.png`: source transport, lesson overview, dual clocks, and shared evidence timeline.
2. `review-boundary.png`: a machine suggestion beside the separate human review action.
3. `lesson-notes.png`: source-linked lesson notes, practice state, and provenance cues.

## Capture command

From the repository root, use Python 3.11 or newer, Node.js, and either Google
Chrome at its default macOS application path or `CHROME_PATH` set to the
browser executable:

```sh
PYTHONPATH=src node scripts/capture_workbench_screenshots.mjs
```

For a nondefault browser location:

```sh
CHROME_PATH=/absolute/path/to/browser \
  PYTHONPATH=src node scripts/capture_workbench_screenshots.mjs
```

The capture pipeline:

1. `scripts/export_screenshot_state.py` projects `fixtures/synthetic_lesson/` through the real lesson
   notes and timeline view models.
2. `scripts/capture_workbench_screenshots.mjs` renders the live workbench UI modules, injects a
   deterministic machine-suggestion only for the review capture, and writes the three PNGs.

The fixture contains no playable media and no model run, so the source rail and engines correctly
show local-only synthetic state without ASR or media proof. Do not caption captures as runnable
models or corpus evaluation.

Do not show real names, media paths, home-directory paths, credentials, model paths, timestamps
from private sessions, browser chrome, notifications, or unrelated tabs.

## Manifest

Before publication, add `manifest.json` beside the PNG files with the package version, exact commit
SHA, UTC capture date, viewport, source fixture, browser name/version, and SHA-256 for each image.
Review every capture for visible alpha labeling, local/offline state, accurate enabled/disabled
controls, keyboard focus, clipping, empty/loading/error state accuracy, and accidental private data.

Current capture and manifest status is recorded in the root
[`RELEASE_STATUS.md`](../../RELEASE_STATUS.md). Do not commit a capture without the matching
manifest and exact candidate identity. Working-tree previews may refresh the PNGs without a
manifest while the repository still has no immutable release identity.
