import { encodeId, formatTime, humanActor } from "/assets/workbench_ui.mjs";

const MAX_CAPTURE_BYTES = 512 * 1024 * 1024;
const MAX_CAPTURE_MILLISECONDS = 2 * 60 * 60 * 1000;

export function createCaptureActions(c) {
  async function importMedia(file) {
    if (!file || c.state.importing) return;
    c.state.importing = true; c.renderPreservingPlayback();
    c.setNotice(`Importing ${file.name || "recording"} into the private project…`, "info");
    try {
      const result = await c.request("/api/imports", { method: "POST", headers: { ...c.actionHeaders(file.type || "application/octet-stream"), "X-Media-Name": encodeId(file.name || "lesson-recording") }, body: file });
      c.state.activeSourceId = result.source_id; await c.load({ quiet: true });
      c.setNotice("Recording imported locally. It is ready for playback and configured processing.", "success");
    } catch (error) { c.setNotice(`Recording could not be imported: ${error.message}`, "error"); }
    finally { c.state.importing = false; c.renderPreservingPlayback(); }
  }

  async function toggleRecording() {
    if (c.state.recorder?.state === "recording") { c.state.captureState = "saving"; c.state.recorder.stop(); refreshRecordingUI(); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      Object.assign(c.state, { chunks: [], captureBytes: 0, captureStartedAt: Date.now(), captureTooLarge: false, captureDiscarded: false, captureState: "recording" });
      c.state.recorder = new MediaRecorder(stream);
      c.state.recorder.ondataavailable = (event) => {
        if (!event.data.size) return;
        c.state.captureBytes += event.data.size;
        if (c.state.captureBytes > MAX_CAPTURE_BYTES) { c.state.captureTooLarge = true; if (c.state.recorder.state === "recording") c.state.recorder.stop(); return; }
        c.state.chunks.push(event.data);
      };
      c.state.recorder.onstop = uploadCapture; c.state.recorder.start(1000);
      c.state.captureTimeout = window.setTimeout(() => { if (c.state.recorder?.state === "recording") c.state.recorder.stop(); }, MAX_CAPTURE_MILLISECONDS);
      c.state.captureInterval = window.setInterval(refreshRecordingUI, 250); refreshRecordingUI();
    } catch (error) { c.state.captureState = "idle"; c.setNotice(`Recording is unavailable: ${error.message}`, "error"); }
  }

  function cancelRecording() { if (c.state.recorder?.state === "recording") { c.state.captureDiscarded = true; c.state.recorder.stop(); refreshRecordingUI(); } }
  function refreshRecordingUI() {
    const recording = c.state.recorder?.state === "recording";
    const button = c.app.querySelector('[data-action="record"]'); const label = c.app.querySelector("[data-record-label]");
    const duration = c.app.querySelector("[data-record-duration]"); const discard = c.app.querySelector('[data-action="cancel-recording"]');
    button?.classList.toggle("is-recording", recording); if (button) button.setAttribute("aria-label", recording ? "Stop and save recording" : "Start local recording");
    if (label) label.textContent = c.state.captureState === "saving" ? "Saving recording…" : recording ? "Recording locally" : "New local take";
    if (duration) duration.textContent = recording ? formatTime((Date.now() - c.state.captureStartedAt) / 1000, false) : "Microphone off";
    if (discard) discard.hidden = !recording;
  }

  async function uploadCapture() {
    window.clearTimeout(c.state.captureTimeout); window.clearInterval(c.state.captureInterval);
    const recorder = c.state.recorder; const stream = recorder?.stream; const author = humanActor(c.state);
    try {
      if (c.state.captureDiscarded) { c.setNotice("Recording discarded. No project file was created.", "info"); return; }
      if (c.state.captureTooLarge) throw new Error("Capture exceeded the 512 MiB local limit.");
      if (!author) throw new Error("The selected reviewer is no longer eligible.");
      const blob = new Blob(c.state.chunks, { type: recorder?.mimeType || "audio/webm" }); if (!blob.size) throw new Error("The microphone returned an empty recording.");
      c.state.captureState = "saving";
      const result = await c.request("/api/captures", { method: "POST", headers: { ...c.actionHeaders(blob.type), "X-Capture-Author": author.id, "X-Capture-Duration-Ms": String(Math.max(0, Date.now() - c.state.captureStartedAt)), "X-Capture-Name": `Local take ${new Date(c.state.captureStartedAt).toLocaleTimeString()}`, "X-Capture-Started-At": new Date(c.state.captureStartedAt).toISOString() }, body: blob });
      c.state.activeSourceId = result.source_id; await c.load({ quiet: true }); c.setNotice("Local take saved and selected as the active source.", "success");
    } catch (error) { c.setNotice(`Recording could not be saved: ${error.message}`, "error"); }
    finally { stream?.getTracks().forEach((track) => track.stop()); Object.assign(c.state, { recorder: null, chunks: [], captureState: "idle" }); refreshRecordingUI(); }
  }
  return { importMedia, toggleRecording, cancelRecording, refreshRecordingUI, uploadCapture };
}
