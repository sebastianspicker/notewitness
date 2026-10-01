#!/usr/bin/env node
/** Render Pages markup from a synthetic workbench snapshot on stdin. */

import { registerHooks } from "node:module";

const assetsRoot = new URL(
  "../src/notewitness/workbench/assets/",
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

function demoState(snapshot, activePanel = "review", activeReviewId = "") {
  const sourceId = snapshot.source_id;
  const durationSeconds = Number(snapshot.duration_us || 0) / 1e6;
  return {
    activePanel,
    activeReviewId: activeReviewId || encodeURIComponent(
      snapshot.lesson?.transcript_suggestions?.[0]?.event_id || "",
    ),
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
      csrf_token: "mock-data-demo",
    }),
  };
}

function withEmptyQueue(snapshot) {
  const empty = clone(snapshot);
  empty.lesson.transcript_suggestions = [];
  return empty;
}

let snapshotInput = "";
for await (const chunk of process.stdin) snapshotInput += chunk;
const snapshot = JSON.parse(snapshotInput);
const { renderContextInspector, renderPanel, renderWorkbench } = await import("/assets/workbench_ui.mjs");
const initial = demoState(snapshot);
const panels = ["review", "transcript", "lesson"].map((name) => {
  return { name, markup: renderPanel(demoState(snapshot, name)) };
});
panels.push({
  name: "review-empty",
  markup: renderPanel(demoState(withEmptyQueue(snapshot), "review")),
});
const contexts = ["transcript", "lesson"].map((name) => {
  return { name, markup: renderContextInspector(demoState(snapshot, name)) };
});
const reviewContexts = (snapshot.lesson?.transcript_suggestions || []).map((item) => ({
  id: encodeURIComponent(item.event_id),
  markup: renderContextInspector(demoState(snapshot, "review", encodeURIComponent(item.event_id))),
}));
const emptyReviewContext = renderContextInspector(demoState(withEmptyQueue(snapshot), "review"));
const payload = JSON.stringify({
  contexts,
  emptyReviewContext,
  panels,
  reviewContexts,
  workbench: renderWorkbench(initial),
});
console.log(Buffer.from(payload, "utf8").toString("base64"));
