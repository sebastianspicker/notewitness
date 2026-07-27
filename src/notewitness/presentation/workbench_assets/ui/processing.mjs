import {
  escapeHTML,
  list,
  modalityLabel,
} from "/assets/ui/utils.mjs";

export function renderProcessing(state) {
  const runtime = state.processing?.runtime || {};
  const jobs = list(state.processing?.jobs);
  const activeJob = jobs.find((job) => ["queued", "running", "cancelling"].includes(job.state));
  const modalities = runtime.modalities || {};
  const modalityReady = (name, fallback = false) => modalities[name] === true || (fallback && runtime[fallback] === true);
  const speechReady = modalityReady("speech_transcription", "transcription_ready");
  const analysisReady = runtime.analysis_ready === true;
  const completeReady = runtime.complete_ready === true;
  const available = speechReady || analysisReady;
  const missingComplete = list(runtime.missing_complete_modalities).length
    ? list(runtime.missing_complete_modalities)
    : ["speech_transcription", "activity_segmentation", "anonymous_diarization", "note_transcription", "instrument_diarization"]
      .filter((name) => !modalityReady(name, name === "speech_transcription" ? "transcription_ready" : false));
  const defaultKind = completeReady
    ? "complete" : speechReady ? "transcription" : "analysis";
  return `<section class="rail-section processing-section" id="processing-panel">
    <p class="side-label">Local engines</p>
    <div class="section-heading"><div><h2>Process recording</h2></div>
      <span class="readiness ${available ? "ready" : ""}">${available ? "Ready" : "Setup needed"}</span></div>
    <ul class="engine-status quiet-list" aria-label="Local engine readiness">
      ${renderEngineStatus("Speech transcription", speechReady)}
      ${renderEngineStatus("Speech/music activity", modalityReady("activity_segmentation"))}
      ${renderEngineStatus("Anonymous speaker diarization", modalityReady("anonymous_diarization"))}
      ${renderEngineStatus("Note transcription", modalityReady("note_transcription"))}
      ${renderEngineStatus("Instrument detection", modalityReady("instrument_detection"))}
      ${renderEngineStatus("Instrument diarization", modalityReady("instrument_diarization"))}
    </ul>
    <label class="field-label" for="run-kind">Run</label>
    <select id="run-kind" class="full-select" data-run-kind ${activeJob ? "disabled" : ""}>
      <option value="complete" ${completeReady ? "" : "disabled"} ${defaultKind === "complete" ? "selected" : ""}>Complete local pass</option>
      <option value="transcription" ${speechReady ? "" : "disabled"} ${defaultKind === "transcription" ? "selected" : ""}>Speech transcription</option>
      <option value="analysis" ${analysisReady ? "" : "disabled"} ${defaultKind === "analysis" ? "selected" : ""}>Configured analysis pass</option>
    </select>
    <button class="primary-button full-button" data-action="enqueue-job"
      ${available && state.activeSourceId && !activeJob ? "" : "disabled"}>Start local processing</button>
    ${available ? (completeReady ? "" : `<p class="setup-copy">Full pass needs: ${missingComplete.map(modalityLabel).map(escapeHTML).join(", ")}.</p>`) : `<p class="setup-copy">Start the workbench with an owner-private runtime configuration to enable local models.</p>`}
    ${renderJobs(jobs)}
  </section>`;
}

function renderEngineStatus(label, ready) {
  return `<li class="engine-row">
    <span class="state-mark ${ready ? "ready" : ""}" aria-hidden="true"></span>
    <span class="engine-label">${escapeHTML(label)}</span>
    <span class="engine-state ${ready ? "ready" : ""}">${ready ? "Ready" : "Off"}</span>
  </li>`;
}

function renderJobs(jobs) {
  if (!jobs.length) return '<p class="supporting jobs-empty">No processing runs yet.</p>';
  return `<ol class="job-list">${jobs.slice(0, 5).map((job) => {
    const running = ["queued", "running", "cancelling"].includes(job.state);
    const resumable = Boolean(job.retryable)
      && ["failed", "interrupted", "cancelled"].includes(job.state);
    return `<li data-job-card="${escapeHTML(job.job_id)}">
      <div><strong>${escapeHTML(job.label || job.kind)}</strong>
        <span class="job-state" data-state="${escapeHTML(job.state)}">${escapeHTML(job.state)}</span></div>
      <p>${escapeHTML(job.status_message, "Waiting for a local worker")}</p>
      ${Number.isFinite(Number(job.progress_percent)) ? `<progress max="100" value="${Number(job.progress_percent)}">${Number(job.progress_percent)}%</progress>` : ""}
      <div class="row-actions">
        ${running ? `<button class="text-button" data-cancel-job="${escapeHTML(job.job_id)}">Cancel</button>` : ""}
        ${resumable ? `<button class="text-button" data-retry-job="${escapeHTML(job.job_id)}">${job.state === "cancelled" ? "Resume" : "Retry"}</button>` : ""}
      </div>
    </li>`;
  }).join("")}</ol>`;
}

export function processingTerminalTransition(previousJobs, nextJobs) {
  const previous = new Map(list(previousJobs).map((job) => [job.job_id, job.state]));
  const changed = list(nextJobs).filter((job) => {
    return ["completed", "failed", "cancelled", "interrupted"].includes(job.state)
      && previous.get(job.job_id) !== job.state;
  });
  if (changed.some((job) => job.state === "completed")) return "completed";
  if (changed.some((job) => list(job.completed_steps).length > 0)) return "partial";
  return null;
}
