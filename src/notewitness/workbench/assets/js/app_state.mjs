export function createWorkbenchState() {
  return { data: null, processing: { runtime: {}, jobs: [] }, media: null, mediaDurations: {}, recorder: null,
    chunks: [], tuner: null, metronome: null, tempo: 72, authorId: "", activeSourceId: "", activePanel: "review",
    reviewKind: "all", transcriptExportFormat: "html", query: "", visibleLaneKinds: new Set(), notice: null,
    dialog: null, busy: new Set(), importing: false, captureBytes: 0, captureStartedAt: 0, captureTimeout: 0,
    captureInterval: 0, captureTooLarge: false, captureDiscarded: false, captureState: "idle", jobPoll: 0 };
}
