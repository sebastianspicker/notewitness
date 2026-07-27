import {
  encodeId,
  escapeHTML,
  humanActor,
  itemSource,
  list,
} from "/assets/ui/utils.mjs";

export function renderAtAGlance(state, lesson) {
  const summary = lesson.summary || {};
  const next = list(lesson.practice_plan?.tasks).find((task) => !task.completed);
  return `<section class="rail-section glance-section">
    <p class="side-label">Lesson at a glance</p>
    <h2>Current episode</h2>
    <p class="episode-summary note">${escapeHTML(summary.overview, "Evidence is ready for review.")}</p>
    ${list(summary.topics).length ? `<p class="topic-line"><strong>Focus</strong> <em>${escapeHTML(summary.topics[0].label)}</em></p>` : ""}
    ${next ? `<div class="next-task"><span>Next practice task</span><p class="note">${escapeHTML(next.text)}</p></div>` : '<p class="supporting foot">No open evidence-backed practice task.</p>'}
    <button class="text-button linkish" data-tab="lesson">Open all lesson notes</button>
  </section>`;
}

export function renderQuickPractice(state, lesson) {
  const actor = humanActor(state);
  const tasks = list(lesson.practice_plan?.tasks);
  return `<section class="rail-section">
    <div class="section-heading">
      <div><p class="side-label">Practice</p><h2>Plan progress</h2></div>
      <span class="count-label">${tasks.filter((item) => item.completed).length}/${tasks.length}</span>
    </div>
    <ul class="quick-plan quiet-list">${tasks.slice(0, 4).map((item) => `<li><label><input type="checkbox" data-practice="${encodeId(item.task_id)}"
      ${item.completed ? "checked" : ""} ${actor ? "" : "disabled"}><span>${escapeHTML(item.text)}</span></label></li>`).join("")
      || '<li class="supporting">No plan has been projected.</li>'}</ul>
  </section>`;
}

export function renderMusicExport(state, lesson) {
  const notes = [
    ...list(lesson.transcript_suggestions),
    ...list(lesson.full_transcript),
  ].filter((item) => item.content_kind === "note"
    && state.activeSourceId
    && itemSource(item) === state.activeSourceId);
  const busy = state.busy?.has("music-export");
  const canExport = Boolean(state.activeSourceId && notes.length && !busy);
  const transcriptFormat = state.transcriptExportFormat || "html";
  const transcriptReady = Boolean(state.activeSourceId && [
    ...list(lesson.transcript_suggestions),
    ...list(lesson.full_transcript),
  ].some((item) => ["speech", "speech_over_music"].includes(item.content_kind)
    && itemSource(item) === state.activeSourceId));
  const inlineControls = transcriptFormat !== "webvtt";
  return `<section class="rail-section export-section" id="exports-panel">
    <div class="section-heading"><div><p class="side-label">Export</p>
      <h2>Music transcript</h2></div><span class="count-label">${notes.length}</span></div>
    <p class="supporting note">Export timed note evidence as research CSV or playable MIDI.</p>
    <label class="consent-check"><input type="checkbox" data-export-rights>
      <span>I am authorized to export this lesson evidence.</span></label>
    <label class="consent-check"><input type="checkbox" data-export-losses>
      <span>I understand that external formats cannot retain the complete evidence graph.</span></label>
    <div class="dialog-actions">
      <button class="secondary-button" data-action="export-csv" data-busy-key="music-export"
        ${canExport ? "" : "disabled"}>Export CSV</button>
      <button class="secondary-button" data-action="export-midi" data-busy-key="music-export"
        ${canExport ? "" : "disabled"}>Export MIDI</button>
    </div>
    ${notes.length ? "" : '<p class="privacy-note foot">Run note transcription first to enable export.</p>'}
    <hr><p class="side-label">Transcript evidence</p><h2>Transcript export</h2>
    <p class="supporting note">Accepted evidence is the default. Including machine suggestions is explicitly labeled and remains unreviewed.</p>
    <label>Format <select data-transcript-format><option value="html" ${transcriptFormat === "html" ? "selected" : ""}>HTML</option><option value="text" ${transcriptFormat === "text" ? "selected" : ""}>TXT</option><option value="webvtt" ${transcriptFormat === "webvtt" ? "selected" : ""}>WebVTT</option></select></label>
    <label>Evidence layer <select data-transcript-layer><option value="accepted_only">Accepted evidence only</option><option value="include_machine_suggestions">Include machine suggestions (unreviewed)</option></select></label>
    ${inlineControls ? `<label class="consent-check"><input type="checkbox" data-transcript-timestamps checked><span>Show inline timestamps</span></label><label>Timestamp interval (ms)<input type="number" min="1" value="60000" data-transcript-interval></label><label>Pause marker <select data-transcript-pause><option value="">Off</option><option value="1000">1 second</option><option value="2000">2 seconds</option><option value="3000">3 seconds</option></select></label>` : '<p class="privacy-note">WebVTT always carries cue timing; inline timestamps and pause markers are not rendered.</p>'}
    <label class="consent-check"><input type="checkbox" data-transcript-rights><span>I am authorized to export this lesson evidence.</span></label>
    <label class="consent-check"><input type="checkbox" data-transcript-losses><span>I acknowledge documented format losses.</span></label>
    <button class="secondary-button" data-action="export-transcript" data-busy-key="transcript-export" ${transcriptReady ? "" : "disabled"}>Export transcript</button>
  </section>`;
}

export function renderIntegrity(state, lesson) {
  const remote = lesson.contains_remote_derived_evidence;
  return `<section class="rail-section integrity-section">
    <p class="side-label">Research integrity</p>
    <h2>Evidence state</h2>
    <dl class="definition-grid">
      <dt>Processing</dt><dd>${remote === true ? "Contains remote-derived evidence" : remote === false ? "Local evidence only" : "Location not fully recorded"}</dd>
      <dt>Schema</dt><dd>${escapeHTML(lesson.schema_version)}</dd>
      <dt>Assessment</dt><dd>${lesson.statistics?.assessment_free ? "Descriptive, not evaluative" : "Review required"}</dd>
    </dl>
    <p class="privacy-note foot">Machine output stays separate until a named human accepts it.</p>
  </section>`;
}
