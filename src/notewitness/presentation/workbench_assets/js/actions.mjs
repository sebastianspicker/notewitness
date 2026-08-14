/** Compatibility composer for the workbench action families. */
import { createAudioActions } from "/assets/js/audio_actions.mjs";
import { createCaptureActions } from "/assets/js/capture_actions.mjs";
import { createExportActions } from "/assets/js/export_actions.mjs";
import { createReviewActions } from "/assets/js/review_actions.mjs";

export function createActions(c) {
  const review = createReviewActions(c);
  const capture = createCaptureActions(c);
  const audio = createAudioActions(c);
  const exports = createExportActions(c);
  const handlers = new Map([
    ["play", audio.togglePlayback], ["seek-back", () => c.seek((c.state.media?.currentTime || 0) - 5)],
    ["seek-forward", () => c.seek((c.state.media?.currentTime || 0) + 5)], ["open-bookmark", review.openBookmarkDialog],
    ["dismiss-notice", () => c.setNotice("")], ["close-dialog", review.closeDialog], ["record", capture.toggleRecording],
    ["cancel-recording", capture.cancelRecording], ["tuner", audio.toggleTuner], ["metronome", audio.toggleMetronome],
    ["tempo-down", () => audio.setTempo(-1)], ["tempo-up", () => audio.setTempo(1)], ["enqueue-job", c.enqueueJob],
    ["export-csv", () => exports.exportMusic("csv")], ["export-midi", () => exports.exportMusic("midi")],
    ["export-transcript", exports.exportTranscript], ["reload", c.load],
  ]);
  async function action(name) {
    const handler = handlers.get(name);
    if (!handler) return;
    try { await handler(); } catch (error) { c.setNotice(`Action failed: ${error.message}`, "error"); }
  }
  return { ...review, ...capture, ...audio, ...exports, action };
}
