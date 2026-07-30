#!/usr/bin/env node
/** Build the static GitHub Pages walkthrough from the real workbench renderer. */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { registerHooks } from "node:module";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const assetsRoot = path.join(root, "src/notewitness/presentation/workbench_assets");
const outDir = path.join(root, "_site");

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("/assets/")) {
      const filePath = path.join(assetsRoot, specifier.slice("/assets/".length));
      return { shortCircuit: true, url: pathToFileURL(filePath).href };
    }
    return nextResolve(specifier, context);
  },
});

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

function demoState(snapshot, activePanel = "review") {
  const sourceId = snapshot.source_id;
  const durationSeconds = Number(snapshot.duration_us || 0) / 1e6;
  const primaryLaneKinds = new Set([
    "activity",
    "transcript",
    "performance",
    "score",
    "episodes",
  ]);
  return {
    activePanel,
    activeSourceId: sourceId,
    authorId: "actor:researcher",
    captureState: "idle",
    dialog: null,
    importing: false,
    mediaDurations: { [sourceId]: durationSeconds || 30 },
    media: { currentTime: 5, paused: true },
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
      (snapshot.timeline?.lanes || [])
        .map((lane) => lane.kind)
        .filter((kind) => primaryLaneKinds.has(kind)),
    ),
    data: clone({
      actors: snapshot.actors,
      media: snapshot.media,
      metronome: snapshot.metronome,
      project: snapshot.project,
      lesson: snapshot.lesson,
      timeline: snapshot.timeline,
      csrf_token: "static-demo",
    }),
  };
}

function staticMarkup(markup) {
  return markup
    .replaceAll("/assets/", "./assets/")
    .replace(
      /<p class="privacy-state">.*?<\/p>/,
      '<p class="privacy-state">Static demo · synthetic fixture · no project data is loaded</p>',
    )
    .replace(
      '<div class="project-heading project">',
      '<div class="project-heading project"><p class="demo-disclosure">Read-only interface walkthrough</p>',
    );
}

const { renderPanel, renderWorkbench } = await import("/assets/workbench_ui.mjs");
const snapshot = exportSnapshot();
const initial = demoState(snapshot);
const workbench = staticMarkup(renderWorkbench(initial));
const panels = ["review", "transcript", "lesson"].map((name) => {
  const state = demoState(snapshot, name);
  return `<template data-demo-panel="${name}">${staticMarkup(renderPanel(state))}</template>`;
}).join("\n");

fs.rmSync(outDir, { recursive: true, force: true });
fs.mkdirSync(path.join(outDir, "assets"), { recursive: true });
fs.cpSync(path.join(assetsRoot, "styles"), path.join(outDir, "assets/styles"), { recursive: true });
fs.copyFileSync(path.join(assetsRoot, "notewitness-mark.svg"), path.join(outDir, "assets/notewitness-mark.svg"));
const appCss = fs.readFileSync(path.join(assetsRoot, "app.css"), "utf8")
  .replaceAll('/assets/styles/', './styles/');
fs.writeFileSync(path.join(outDir, "assets/app.css"), appCss);
fs.copyFileSync(path.join(here, "pages_demo.css"), path.join(outDir, "assets/pages-demo.css"));
fs.copyFileSync(path.join(here, "pages_demo_client.js"), path.join(outDir, "assets/pages-demo.js"));
fs.writeFileSync(path.join(outDir, ".nojekyll"), "");

const html = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="light">
    <meta name="theme-color" content="#ffffff">
    <meta name="description" content="Static, synthetic walkthrough of the NoteWitness local evidence workbench.">
    <title>NoteWitness · static interface demo</title>
    <link rel="icon" href="./assets/notewitness-mark.svg" type="image/svg+xml">
    <link rel="stylesheet" href="./assets/app.css">
    <link rel="stylesheet" href="./assets/pages-demo.css">
  </head>
  <body>
    <a class="skip-link" href="#workbench-main">Skip to workspace</a>
    <div id="app">${workbench}</div>
    ${panels}
    <noscript>This static walkthrough requires JavaScript for tabs and simulated controls.</noscript>
    <script type="module" src="./assets/pages-demo.js"></script>
  </body>
</html>`;

fs.writeFileSync(path.join(outDir, "index.html"), html);
console.log("built _site from the real workbench renderer and synthetic fixture");
