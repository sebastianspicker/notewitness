#!/usr/bin/env node
/** Render static Pages markup from a synthetic workbench snapshot on stdin. */

import { registerHooks } from "node:module";

const assetsRoot = new URL(
  "../src/notewitness/presentation/workbench_assets/",
  import.meta.url,
);

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("/assets/")) {
      return {
        shortCircuit: true,
        url: new URL(specifier.slice("/assets/".length), assetsRoot).href,
      };
    }
    return nextResolve(specifier, context);
  },
});

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function visibleLaneKinds(snapshot) {
  const primary = new Set([
    "activity",
    "transcript",
    "performance",
    "score",
    "episodes",
  ]);
  return new Set(
    (snapshot.timeline?.lanes || [])
      .map((lane) => lane.kind)
      .filter((kind) => primary.has(kind)),
  );
}

function demoState(snapshot, activePanel = "review") {
  const sourceId = snapshot.source_id;
  const durationSeconds = Number(snapshot.duration_us || 0) / 1e6;
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
    visibleLaneKinds: visibleLaneKinds(snapshot),
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

let snapshotInput = "";
for await (const chunk of process.stdin) snapshotInput += chunk;
const snapshot = JSON.parse(snapshotInput);
const { renderPanel, renderWorkbench } = await import("/assets/workbench_ui.mjs");
const initial = demoState(snapshot);
const panels = ["review", "transcript", "lesson"].map((name) => {
  return `<template data-demo-panel="${name}">${renderPanel(demoState(snapshot, name))}</template>`;
}).join("\n");

const demoStyles = `
  .demo-bar { min-height: 28px; padding: 6px 16px; border-bottom: 1px solid var(--rule); background: var(--indigo-soft); color: var(--indigo-deep); font-size: 11px; letter-spacing: .02em; text-align: center; }
  .demo-bar strong { font-weight: 700; }
  .app-shell { height: calc(100vh - 28px); max-height: calc(100vh - 28px); }
  [data-demo-command="simulated"]::after, .demo-action-label { display: inline-block; margin-left: 6px; color: currentColor; font-size: 8px; font-weight: 700; letter-spacing: .08em; line-height: 1; text-transform: uppercase; opacity: .72; }
  [data-demo-command="simulated"]::after { content: "Simulated"; }
  .file-button .demo-action-label { margin-left: 4px; }
  .record-group .demo-action-label, .practice-list .demo-action-label, .quick-plan .demo-action-label { margin: 3px 0 0; }
  .demo-command-help { margin: 8px 0 0; color: var(--mute); font-size: 11px; line-height: 1.45; }
  .demo-command-help strong { color: var(--indigo-deep); font-weight: 650; }
  .demo-hidden { display: none !important; }
  .full-select.is-hidden { width: 1px; min-height: 0; }
  @media (max-width: 780px) { .demo-bar { text-align: left; } .app-shell { height: auto; max-height: none; } }
`;

const html = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="light">
    <meta name="theme-color" content="#ffffff">
    <meta name="description" content="Static, synthetic walkthrough of the NoteWitness local evidence workbench.">
    <title>NoteWitness · static interface demo</title>
    <link rel="icon" href="/assets/notewitness-mark.svg" type="image/svg+xml">
    <link rel="stylesheet" href="/assets/app.css">
    <style>${demoStyles}</style>
  </head>
  <body>
    <a class="skip-link" href="#workbench-main">Skip to workspace</a>
    <div class="demo-bar" role="note"><strong>Static demo · synthetic fixture.</strong> Navigation changes this page only; marked actions are simulated and never run commands.</div>
    <div id="app">${renderWorkbench(initial)}</div>
    ${panels}
    <noscript>This static walkthrough requires JavaScript for tabs and simulated controls.</noscript>
    <script type="module" src="/assets/pages-demo.js"></script>
  </body>
</html>`;

process.stdout.write(html);
