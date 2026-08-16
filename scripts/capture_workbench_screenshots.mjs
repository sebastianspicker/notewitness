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

const scriptPath = fs.realpathSync(fileURLToPath(import.meta.url));
const here = path.dirname(scriptPath);
const root = path.resolve(here, "..");
const assetsRoot = path.join(root, "src/notewitness/presentation/workbench_assets");
const outDir = path.join(root, "docs/screenshots");
const captureNames = new Set([
  "workbench-overview",
  "review-boundary",
  "lesson-notes",
]);

function isWithin(basePath, candidatePath) {
  const relative = path.relative(basePath, candidatePath);
  return relative === "" || (!relative.startsWith(`..${path.sep}`)
    && relative !== ".." && !path.isAbsolute(relative));
}

export function assertExistingPathWithin(baseDir, candidatePath, description) {
  const lexicalBase = path.resolve(baseDir);
  const lexicalCandidate = path.resolve(candidatePath);
  const realBase = fs.realpathSync(lexicalBase);
  if (!isWithin(lexicalBase, lexicalCandidate) && !isWithin(realBase, lexicalCandidate)) {
    throw new Error(`${description} escapes its allowed directory`);
  }
  const realCandidate = fs.realpathSync(lexicalCandidate);
  if (!isWithin(realBase, realCandidate)) {
    throw new Error(`${description} resolves outside its allowed directory`);
  }
  return realCandidate;
}

export function captureFilePath(directory, name, extension) {
  if (!captureNames.has(name)) throw new Error(`Unknown screenshot capture: ${name}`);
  if (!["html", "png"].includes(extension)) throw new Error(`Unsupported capture extension: ${extension}`);
  const realDirectory = fs.realpathSync(directory);
  const candidate = path.resolve(realDirectory, `${name}.${extension}`);
  if (!isWithin(realDirectory, candidate)) throw new Error("Capture file escapes its workspace");
  return candidate;
}

export function createCaptureWorkspace(outputDirectory = outDir, trustedRoot = root) {
  fs.mkdirSync(outputDirectory, { recursive: true });
  const safeOutputDirectory = assertExistingPathWithin(
    trustedRoot,
    outputDirectory,
    "Screenshot output directory",
  );
  return fs.mkdtempSync(path.join(safeOutputDirectory, ".capture-"));
}

export function removeCaptureWorkspace(workspace, outputDirectory) {
  assertExistingPathWithin(outputDirectory, workspace, "Screenshot capture workspace");
  fs.rmSync(workspace, { recursive: true, force: true });
}

export function resolveExecutable(candidate, description) {
  const executable = fs.realpathSync(candidate);
  const stat = fs.statSync(executable);
  if (!stat.isFile() || (stat.mode & 0o111) === 0) {
    throw new Error(`${description} must be an executable file: ${candidate}`);
  }
  return executable;
}

export function assertRegularOutputFile(filePath) {
  if (fs.existsSync(filePath) && !fs.lstatSync(filePath).isFile()) {
    throw new Error(`Screenshot output is not a regular file: ${filePath}`);
  }
}

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("/assets/")) {
      const filePath = path.join(assetsRoot, specifier.slice("/assets/".length));
      return { shortCircuit: true, url: pathToFileURL(filePath).href };
    }
    return nextResolve(specifier, context);
  },
});

export function expandCss(filePath, assetDirectory = assetsRoot, seen = new Set()) {
  const safeFilePath = assertExistingPathWithin(assetDirectory, filePath, "Stylesheet");
  if (seen.has(safeFilePath)) return "";
  seen.add(safeFilePath);
  let text = fs.readFileSync(safeFilePath, "utf8");
  text = text.replace(/@import url\("\/assets\/styles\/([^"]+)"\);\s*/g, (_, name) => {
    return expandCss(path.join(assetDirectory, "styles", name), assetDirectory, seen);
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

export function writeCaptureHtml(workspace, name, bodyHtml, css) {
  const htmlPath = captureFilePath(workspace, name, "html");
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
  fs.writeFileSync(htmlPath, html, { encoding: "utf8", flag: "wx", mode: 0o600 });
  return htmlPath;
}

function screenshot(chrome, htmlPath, pngPath) {
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

async function main() {
  const chrome = resolveExecutable(
    process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "Chrome",
  );
  const { renderWorkbench } = await import("/assets/workbench_ui.mjs");
  const snapshot = exportSnapshot();
  const css = expandCss(path.join(assetsRoot, "app.css"));
  const workspace = createCaptureWorkspace();
  try {
    for (const capture of captures) {
      const state = baseState(snapshot, capture.panel);
      capture.tune(state);
      const body = renderWorkbench(state);
      const htmlPath = writeCaptureHtml(workspace, capture.name, body, css);
      const scratchPngPath = captureFilePath(workspace, capture.name, "png");
      const pngPath = captureFilePath(outDir, capture.name, "png");
      assertRegularOutputFile(pngPath);
      screenshot(chrome, htmlPath, scratchPngPath);
      fs.renameSync(scratchPngPath, pngPath);
      const stat = fs.statSync(pngPath);
      console.log(`wrote ${path.relative(root, pngPath)} (${stat.size} bytes)`);
    }
  } finally {
    removeCaptureWorkspace(workspace, outDir);
  }
  console.log("workbench screenshots captured at 1440×900");
}

if (process.argv[1] && fs.realpathSync(process.argv[1]) === scriptPath) {
  await main();
}
