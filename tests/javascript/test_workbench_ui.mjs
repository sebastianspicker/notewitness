/**
 * Workbench UI contract checks.
 * Registers a Node resolve hook so browser-absolute `/assets/ui/*` imports
 * map to workbench_assets, then loads the barrel via dynamic import.
 */
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { registerHooks } from "node:module";

const here = path.dirname(fileURLToPath(import.meta.url));
const assetsRoot = path.resolve(
  here,
  "../../src/notewitness/presentation/workbench_assets",
);

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("/assets/")) {
      const filePath = path.join(assetsRoot, specifier.slice("/assets/".length));
      return { shortCircuit: true, url: pathToFileURL(filePath).href };
    }
    return nextResolve(specifier, context);
  },
});

const {
  escapeHTML,
  formatTime,
  humanActors,
  processingTerminalTransition,
  renderProcessing,
  renderWorkbench,
  reviewItems,
  sourceDurationSeconds,
  timelineTicks,
} = await import(
  pathToFileURL(path.join(assetsRoot, "workbench_ui.mjs")).href
);
const { createActions } = await import(
  pathToFileURL(path.join(assetsRoot, "js/actions.mjs")).href
);
const { createReviewActions } = await import(
  pathToFileURL(path.join(assetsRoot, "js/review_actions.mjs")).href
);
const { createCaptureActions } = await import(
  pathToFileURL(path.join(assetsRoot, "js/capture_actions.mjs")).href
);
const { createAudioActions } = await import(
  pathToFileURL(path.join(assetsRoot, "js/audio_actions.mjs")).href
);
const { createExportActions } = await import(
  pathToFileURL(path.join(assetsRoot, "js/export_actions.mjs")).href
);

for (const factory of [createActions, createReviewActions, createCaptureActions, createAudioActions, createExportActions]) {
  assert.equal(typeof factory, "function");
}

assert.equal(escapeHTML(`<script a="b">&'</script>`),
  "&lt;script a=&quot;b&quot;&gt;&amp;&#39;&lt;/script&gt;");
assert.equal(formatTime(65.25), "01:05.250");
assert.equal(formatTime(3661, false), "1:01:01");
assert.deepEqual(timelineTicks(60), [0, 10, 20, 30, 40, 50, 60]);
assert.deepEqual(timelineTicks(0), [0]);

const state = {
  activePanel: "review",
  activeSourceId: "source:second",
  authorId: "actor:researcher",
  dialog: null,
  importing: false,
  mediaDurations: {},
  metronome: null,
  notice: null,
  processing: {
    jobs: [],
    runtime: {
      analysis_ready: true,
      transcription_ready: true,
    },
  },
  query: "c sharp",
  recorder: null,
  reviewKind: "note",
  tempo: 72,
  tuner: null,
  visibleLaneKinds: new Set(["source", "transcript", "performance"]),
  data: {
    actors: [
      { id: "actor:researcher", role: "music analysis researcher", human_evidence_eligible: true },
      { id: "actor:machine", role: "machine", human_evidence_eligible: false },
    ],
    media: [
      { source_id: "source:first", display_name: "First take", duration_us: 10_000_000, url: "/first" },
      { source_id: "source:second", display_name: "Second take", duration_us: 90_000_000, url: "/second" },
    ],
    metronome: { bpm: 72 },
    project: { network_mode: "offline", saved: true, title: "Study" },
    lesson: {
      title: "Teaching study",
      network_mode: "offline",
      contains_remote_derived_evidence: false,
      schema_version: "0.1.0",
      sources: [],
      transcript_suggestions: [
        {
          event_id: "event:note",
          content_kind: "note",
          actor_role: "student",
          display_text: "C sharp, fourth octave",
          review_status: "machine_suggested",
          confidence: { kind: "adapter_reported", value: 0.9 },
          anchors: [{ span: { source_id: "source:second", start_us: 2_000_000, duration_us: 500_000 } }],
        },
        {
          event_id: "event:speech",
          content_kind: "speech",
          display_text: "Try again",
          body_value: "Try again",
          anchors: [{ span: { source_id: "source:second", start_us: 0, duration_us: 1_000_000 } }],
        },
      ],
      full_transcript: [],
      summary: { overview: "One evidence-backed teaching moment.", topics: [], feedback: [], key_moments: [] },
      practice_plan: { tasks: [] },
      bookmarks: [],
      statistics: { assessment_free: true, timeline_extents: [] },
      source_graph: {},
      limitations: [],
    },
    timeline: {
      lanes: [
        {
          kind: "source",
          label: "Source and playback",
          keyboard_shortcut: "1",
          items: [{
            label: "Second take",
            review_status: "source",
            playback: { source_id: "source:second", start_us: 0, duration_us: 90_000_000 },
          }],
        },
      ],
    },
  },
};

assert.equal(humanActors(state).length, 1);
assert.equal(sourceDurationSeconds(state), 90);
assert.deepEqual(reviewItems(state).map((item) => item.event_id), ["event:note"]);
assert.equal(processingTerminalTransition(
  [{ job_id: "job:one", state: "running", completed_steps: [] }],
  [{ job_id: "job:one", state: "cancelled", completed_steps: ["transcription"] }],
), "partial");
assert.equal(processingTerminalTransition(
  [{ job_id: "job:one", state: "running", completed_steps: [] }],
  [{ job_id: "job:one", state: "cancelled", completed_steps: [] }],
), null);
assert.equal(processingTerminalTransition([], [
  { job_id: "job:fast", state: "completed", completed_steps: ["analysis"] },
]), "completed");

const rendered = renderWorkbench(state);
for (const expected of [
  "NoteWitness",
  "Local evidence workbench",
  "/assets/notewitness-mark.svg",
  "Review queue",
  "Full transcript",
  "Lesson notes",
  "Process recording",
  "Tuner",
  "Metronome",
  "Music transcript",
  "Export CSV",
  "Export MIDI",
  "Transcript export",
  "Include machine suggestions (unreviewed)",
  "Second take",
  "C sharp, fourth octave",
  'role="tabpanel"',
  'data-import-file',
  'data-export-rights',
]) {
  assert.ok(rendered.includes(expected), `missing rendered contract: ${expected}`);
}
assert.ok(!rendered.includes("undefined"));
assert.ok(!rendered.includes("window.prompt"));
assert.ok(!rendered.includes("window.alert"));
assert.match(rendered, /data-action="export-transcript"[^>]*>Export transcript<\/button>/);
assert.doesNotMatch(rendered, /data-action="export-transcript"[^>]*disabled/);

state.reviewKind = "all";
state.query = "";
const allReviewEvidence = renderWorkbench(state);
assert.equal((allReviewEvidence.match(/data-revise=/g) || []).length, 1);

state.processing.runtime = {
  analysis_ready: true,
  complete_ready: false,
  transcription_ready: true,
  modalities: {
    speech_transcription: true,
    activity_segmentation: false,
    anonymous_diarization: false,
    note_transcription: true,
    instrument_detection: true,
    instrument_diarization: false,
  },
  missing_complete_modalities: ["activity_segmentation", "anonymous_diarization", "instrument_diarization"],
};
const partialProcessing = renderProcessing(state);
assert.ok(partialProcessing.includes("Speech/music activity"));
assert.ok(partialProcessing.includes("Anonymous speaker diarization"));
assert.ok(partialProcessing.includes("Instrument detection"));
assert.ok(partialProcessing.includes('value="complete" disabled'));
assert.ok(partialProcessing.includes("Configured analysis pass"));
assert.ok(partialProcessing.includes("Full pass needs: speech/music activity segmentation, anonymous speaker diarization, instrument diarization."));

state.processing.jobs = [{
  job_id: "job:resumable",
  kind: "complete",
  label: "Complete local pass",
  state: "cancelled",
  retryable: true,
  completed_steps: ["transcription"],
  progress_percent: 52,
  status_message: "Cancelled after speech transcription; completed evidence remains ready for review",
}];
assert.ok(renderWorkbench(state).includes(">Resume</button>"));

console.log("workbench UI contract verified");
