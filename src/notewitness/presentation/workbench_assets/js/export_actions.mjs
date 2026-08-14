import { list } from "/assets/workbench_ui.mjs";

export function createExportActions(c) {
  async function exportMusic(format) {
    const rights = Boolean(c.app.querySelector("[data-export-rights]")?.checked);
    const losses = Boolean(c.app.querySelector("[data-export-losses]")?.checked);
    if (!rights || !losses) return c.setNotice("Confirm both export decisions before creating a file from private lesson evidence.", "error");
    const filename = `notewitness-notes-${new Date().toISOString().replaceAll(":", "-")}.${format === "midi" ? "mid" : "csv"}`;
    let result = null;
    const saved = await c.runMutation("music-export", `Creating ${format.toUpperCase()} export`, async () => {
      result = await c.request("/api/exports/music", { method: "POST", headers: c.actionHeaders(), body: JSON.stringify({ format, filename, source_id: c.state.activeSourceId, authorize_local_export: rights, acknowledge_export_losses: losses }) });
    });
    if (saved) { const count = list(result?.documented_losses).length; c.setNotice(`${result?.filename || filename} saved locally with ${result?.record_count || 0} notes${count ? ` and ${count} documented projection loss${count === 1 ? "" : "es"}` : ""}.`, "success"); }
  }

  async function exportTranscript() {
    const options = readTranscriptOptions();
    if (!options.rights || !options.losses) return c.setNotice("Confirm rights authorization and format-loss acknowledgement before export.", "error");
    let result = null;
    const saved = await c.runMutation("transcript-export", "Creating transcript export", async () => {
      result = await c.request("/api/exports/transcript", { method: "POST", headers: c.actionHeaders(), body: JSON.stringify(transcriptPayload(options)) });
    });
    if (saved) c.setNotice(`${result?.filename || "Transcript"} saved locally from ${result?.evidence_layer === "include_machine_suggestions" ? "accepted and machine-suggested" : "accepted"} evidence.`, "success");
  }

  function selected(selector, fallback) { return c.app.querySelector(selector)?.value || fallback; }
  function checked(selector) { return Boolean(c.app.querySelector(selector)?.checked); }
  function readTranscriptOptions() { return { format: selected("[data-transcript-format]", "html"), evidenceLayer: selected("[data-transcript-layer]", "accepted_only"), rights: checked("[data-transcript-rights]"), losses: checked("[data-transcript-losses]"), pauseValue: selected("[data-transcript-pause]", ""), interval: Number(selected("[data-transcript-interval]", 60000)), visibleTimestamps: checked("[data-transcript-timestamps]") }; }
  function transcriptPayload(options) { const extension = options.format === "webvtt" ? "vtt" : options.format === "text" ? "txt" : "html"; return { format: options.format, filename: `notewitness-transcript-${new Date().toISOString().replaceAll(":", "-")}.${extension}`, source_id: c.state.activeSourceId, evidence_layer: options.evidenceLayer, authorize_local_export: options.rights, acknowledge_export_losses: options.losses, visible_timestamps: options.format === "webvtt" ? false : options.visibleTimestamps, timestamp_interval_ms: Number.isInteger(options.interval) && options.interval > 0 ? options.interval : 60000, pause_threshold_ms: options.format === "webvtt" || !options.pauseValue ? null : Number(options.pauseValue) }; }
  return { exportMusic, exportTranscript };
}
