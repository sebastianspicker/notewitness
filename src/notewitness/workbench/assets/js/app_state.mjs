function initialTheme() {
  try {
    const stored = globalThis.localStorage?.getItem("notewitness-theme");
    if (stored === "light" || stored === "dark") return stored;
    if (globalThis.matchMedia?.("(prefers-color-scheme: dark)").matches) return "dark";
  } catch {}
  return "light";
}

export function createWorkbenchState() {
  return { data: null, processing: { runtime: {}, jobs: [] }, media: null, mediaDurations: {}, recorder: null,
    chunks: [], tuner: null, metronome: null, tempo: 72, authorId: "", activeSourceId: "", activePanel: "review",
    activeReviewId: "", reviewKind: "all", theme: initialTheme(), transcriptExportFormat: "html", query: "", visibleLaneKinds: new Set(), notice: null,
    dialog: null, busy: new Set(), importing: false, captureBytes: 0, captureStartedAt: 0, captureTimeout: 0,
    captureInterval: 0, captureTooLarge: false, captureDiscarded: false, captureState: "idle", jobPoll: 0 };
}
