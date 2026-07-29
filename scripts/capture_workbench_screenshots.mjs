#!/usr/bin/env node
/**
 * Capture the curated public workbench screenshots at 1440×900.
 *
 * Uses the real workbench UI modules + CSS with deterministic synthetic state
 * exported from fixtures/synthetic_lesson (plus a synthetic review suggestion).
 * Requires Google Chrome (macOS default path) or CHROME_PATH.
 */
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { registerHooks } from "node:module";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const assetsRoot = path.join(root, "src/notewitness/presentation/workbench_assets");
const outDir = path.join(root, "docs/screenshots");
const chrome = process.env.CHROME_PATH
  || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("/assets/")) {
      const filePath = path.join(assetsRoot, specifier.slice("/assets/".length));
      return { shortCircuit: true, url: pathToFileURL(filePath).href };
    }
    return nextResolve(specifier, context);
  },
});

function expandCss(filePath, seen = new Set()) {
  if (seen.has(filePath)) return "";
  seen.add(filePath);
  let text = fs.readFileSync(filePath, "utf8");
  text = text.replace(/@import url\("\/assets\/styles\/([^"]+)"\);\s*/g, (_, name) => {
    return expandCss(path.join(assetsRoot, "styles", name), seen);
  });
  return `${text}\n`;
}

function exportSnapshot() {
  const result = spawnSync(
    process.env.PYTHON || "python3",
    [path.join(here, "export_screenshot_state.py")],
    {
      cwd: root,
      env: { ...process.env, PYTHONPATH: path.join(root, "src") },
      encoding: "utf8",
    },
  );
  if (result.status !== 0) {
    throw new Error(result.stderr || result.stdout || "export_screenshot_state.py failed");
  }
  return JSON.parse(result.stdout);
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function baseState(snapshot, panel) {
  const sourceId = snapshot.source_id;
  const durationSeconds = Number(snapshot.duration_us || 0) / 1e6;
  const data = clone({
    actors: snapshot.actors,
    media: snapshot.media,
    metronome: snapshot.metronome,
    project: snapshot.project,
    lesson: snapshot.lesson,
    timeline: snapshot.timeline,
    csrf_token: "screenshot",
  });
  return {
    activePanel: panel,
    activeSourceId: sourceId,
    authorId: "actor:researcher",
    dialog: null,
    importing: false,
    mediaDurations: { [sourceId]: durationSeconds || 30 },
    media: null,
    metronome: null,
    notice: null,
    processing: {
      jobs: [],
      runtime: {
        analysis_ready: false,
        transcription_ready: false,
        complete_ready: false,
        modalities: {},
      },
    },
    query: "",
    recorder: null,
    reviewKind: "all",
    tempo: 72,
    tuner: null,
    visibleLaneKinds: new Set(
      (snapshot.timeline?.lanes || []).map((lane) => lane.kind),
    ),
    data,
  };
}

function writeCaptureHtml(name, bodyHtml, css) {
  const htmlPath = path.join(outDir, `.${name}.html`);
  const markPath = pathToFileURL(path.join(assetsRoot, "notewitness-mark.svg")).href;
  const body = bodyHtml.replaceAll(
    'src="/assets/notewitness-mark.svg"',
    `src="${markPath}"`,
  );
  const html = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>NoteWitness screenshot · ${name}</title>
  <style>${css}</style>
</head>
<body>${body}</body>
</html>`;
  fs.writeFileSync(htmlPath, html);
  return htmlPath;
}

function screenshot(htmlPath, pngPath) {
  const result = spawnSync(chrome, [
    "--headless=new",
    "--disable-gpu",
    "--hide-scrollbars",
    "--window-size=1440,900",
    `--screenshot=${pngPath}`,
    "--default-background-color=FFFFFFFF",
    pathToFileURL(htmlPath).href,
  ], { encoding: "utf8" });
  if (result.status !== 0) {
    throw new Error(result.stderr || result.stdout || `Chrome failed for ${pngPath}`);
  }
}

const { renderWorkbench } = await import("/assets/workbench_ui.mjs");

const snapshot = exportSnapshot();
const css = expandCss(path.join(assetsRoot, "app.css"));

const primaryLanes = new Set([
  "activity",
  "transcript",
  "performance",
  "score",
  "episodes",
]);

const captures = [
  {
    name: "workbench-overview",
    panel: "review",
    tune(state) {
      // Overview: timeline + glance without a review queue competing for focus.
      state.activePanel = "review";
      state.data.lesson.transcript_suggestions = [];
      state.visibleLaneKinds = primaryLanes;
      state.media = { currentTime: 5.0, paused: true };
    },
  },
  {
    name: "review-boundary",
    panel: "review",
    tune(state) {
      state.activePanel = "review";
      state.visibleLaneKinds = new Set(["activity", "transcript", "episodes"]);
      state.media = { currentTime: 1.0, paused: true };
    },
  },
  {
    name: "lesson-notes",
    panel: "lesson",
    tune(state) {
      state.activePanel = "lesson";
      state.visibleLaneKinds = primaryLanes;
      state.media = { currentTime: 5.0, paused: true };
    },
  },
];

fs.mkdirSync(outDir, { recursive: true });

for (const capture of captures) {
  const state = baseState(snapshot, capture.panel);
  capture.tune(state);
  const body = renderWorkbench(state);
  const htmlPath = writeCaptureHtml(capture.name, body, css);
  const pngPath = path.join(outDir, `${capture.name}.png`);
  screenshot(htmlPath, pngPath);
  fs.unlinkSync(htmlPath);
  const stat = fs.statSync(pngPath);
  console.log(`wrote ${path.relative(root, pngPath)} (${stat.size} bytes)`);
}

console.log("workbench screenshots captured at 1440×900");
