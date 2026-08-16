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
const { createProcessing } = await import(
  pathToFileURL(path.join(assetsRoot, "js/processing.mjs")).href
);
const { createRendering } = await import(
  pathToFileURL(path.join(assetsRoot, "js/app_rendering.mjs")).href
);
const { renderConfidence } = await import(
  pathToFileURL(path.join(assetsRoot, "ui/render_utils.mjs")).href
);

for (const factory of [createActions, createReviewActions, createCaptureActions, createAudioActions, createExportActions]) {
  assert.equal(typeof factory, "function");
}

assert.equal(escapeHTML(`<script a="b">&'</script>`),
  "&lt;script a=&quot;b&quot;&gt;&amp;&#39;&lt;/script&gt;");
assert.equal(renderConfidence({ kind: `<svg onload="alert(1)">&'` }),
  "&lt;svg onload=&quot;alert(1)&quot;&gt;&amp;&#39;");
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

const hostilePayload = `"><script>alert(1)</script><style>body{}</style><svg onload="alert(1)"></svg>javascript:alert(1) data:text/html,&'`;
const escapedHostilePayload = escapeHTML(hostilePayload);
const hostileState = structuredClone(state);
hostileState.activeSourceId = hostilePayload;
hostileState.authorId = hostilePayload;
hostileState.query = hostilePayload;
hostileState.reviewKind = hostilePayload;
hostileState.tempo = hostilePayload;
hostileState.notice = { kind: hostilePayload, message: hostilePayload };
hostileState.dialog = {
  mode: "edit-evidence",
  originalText: hostilePayload,
  reason: hostilePayload,
  sourceName: hostilePayload,
};
hostileState.data.actors = [{
  id: hostilePayload,
  role: hostilePayload,
  instrument_role: hostilePayload,
  human_evidence_eligible: true,
}];
hostileState.data.media = [{
  source_id: hostilePayload,
  display_name: hostilePayload,
  duration_us: 1_000_000,
  url: hostilePayload,
}];
hostileState.data.project.network_mode = hostilePayload;
hostileState.data.lesson = {
  title: hostilePayload,
  schema_version: hostilePayload,
  contains_remote_derived_evidence: false,
  sources: [{ source_id: hostilePayload, uri: hostilePayload }],
  transcript_suggestions: [{
    event_id: hostilePayload,
    content_kind: hostilePayload,
    actor_id: hostilePayload,
    actor_role: hostilePayload,
    body_value: hostilePayload,
    display_text: hostilePayload,
    review_status: hostilePayload,
    confidence: { kind: hostilePayload },
    anchors: [{ span: { source_id: hostilePayload, start_us: 0, duration_us: 1_000_000 } }],
  }],
  full_transcript: [{
    event_id: hostilePayload,
    content_kind: hostilePayload,
    actor_id: hostilePayload,
    actor_role: hostilePayload,
    body_value: hostilePayload,
    display_text: hostilePayload,
    review_status: hostilePayload,
    anchors: [{ span: { source_id: hostilePayload, start_us: 0, duration_us: 1_000_000 } }],
  }],
  summary: {
    overview: hostilePayload,
    topics: [{ label: hostilePayload }],
    feedback: [{
      text: hostilePayload,
      actor_role: hostilePayload,
      review_status: hostilePayload,
      playback: { source_id: hostilePayload, start_us: 0 },
    }],
    key_moments: [{
      relation_type: hostilePayload,
      label: hostilePayload,
      anchors: [{ span: { source_id: hostilePayload, start_us: 0 } }],
    }],
  },
  relation_suggestions: [{
    relation_id: hostilePayload,
    relation_type: "local:assigned_for_practice",
    label: hostilePayload,
    anchors: [{ span: { source_id: hostilePayload, start_us: 0 } }],
  }],
  practice_plan: { tasks: [{ task_id: hostilePayload, text: hostilePayload, review_status: hostilePayload }] },
  bookmarks: [{ label: hostilePayload, playback: { source_id: hostilePayload, start_us: 0 } }],
  statistics: { assessment_free: true, [hostilePayload]: hostilePayload },
  source_graph: { [hostilePayload]: hostilePayload },
  limitations: [hostilePayload],
};
hostileState.processing = {
  runtime: {
    analysis_ready: true,
    complete_ready: false,
    transcription_ready: true,
    missing_complete_modalities: [hostilePayload],
  },
  jobs: [{
    job_id: hostilePayload,
    kind: hostilePayload,
    label: hostilePayload,
    state: hostilePayload,
    status_message: hostilePayload,
    progress_percent: 50,
    retryable: false,
  }],
};

for (const activePanel of ["review", "transcript", "lesson"]) {
  hostileState.activePanel = activePanel;
  const hostileMarkup = renderWorkbench(hostileState);
  assert.ok(hostileMarkup.includes(escapedHostilePayload), `${activePanel} must encode hostile state text`);
  assert.ok(hostileMarkup.includes(`value="${escapedHostilePayload}"`), `${activePanel} must encode hostile attribute values`);
  assert.ok(!hostileMarkup.includes(hostilePayload), `${activePanel} must not emit raw hostile payload`);
  assert.doesNotMatch(hostileMarkup, /<(?:script|style|svg)\b/i);
  assert.doesNotMatch(hostileMarkup, /["']\s+on(?:load|error|click)\s*=/i);
  assert.doesNotMatch(hostileMarkup, /\b(?:src|href|action)\s*=\s*["']\s*(?:javascript|data):/i);
}

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

const originalWindow = globalThis.window;
const originalCSS = globalThis.CSS;
globalThis.window = { clearInterval() {}, setInterval() { return 1; } };
globalThis.CSS = { escape: (value) => String(value) };
try {
  async function assertJobActionFailure(request, expectedMessage) {
    const processingState = {
      busy: new Set(),
      data: {},
      jobPoll: 0,
      notice: null,
      processing: { jobs: [] },
    };
    const app = { querySelector: () => null, querySelectorAll: () => [] };
    const controller = {
      actionHeaders: () => ({}),
      app,
      load: async () => {},
      refreshProcessing: () => {},
      request,
      state: processingState,
    };
    Object.assign(controller, createRendering(controller));
    const processing = createProcessing(controller);

    await processing.jobAction("job:failure", "cancel");

    assert.deepEqual(processingState.notice, { message: expectedMessage, kind: "error" });
    assert.equal(processingState.busy.size, 0);
  }

  await assertJobActionFailure(
    async () => { throw new Error("action failed"); },
    "Cancelling local processing could not be completed: action failed",
  );

  let requestCount = 0;
  await assertJobActionFailure(
    async () => {
      requestCount += 1;
      if (requestCount === 1) return {};
      throw new Error("poll failed");
    },
    "Cancelling local processing could not be completed: poll failed",
  );
} finally {
  if (originalWindow === undefined) delete globalThis.window;
  else globalThis.window = originalWindow;
  if (originalCSS === undefined) delete globalThis.CSS;
  else globalThis.CSS = originalCSS;
}

console.log("workbench UI contract verified");
