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
  return { name, markup: renderPanel(demoState(snapshot, name)) };
});
const payload = JSON.stringify({
  panels,
  workbench: renderWorkbench(initial),
});
console.log(Buffer.from(payload, "utf8").toString("base64"));
