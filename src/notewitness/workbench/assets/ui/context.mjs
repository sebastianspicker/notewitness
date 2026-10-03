import {
  encodeId,
  escapeHTML,
  formatTime,
  humanActor,
  itemDuration,
  itemSource,
  itemTime,
  list,
  renderActorAttributionOptionsHTML,
  renderConfidence,
  selectedReviewItem,
  sourceDurationSeconds,
  sourceName,
} from "/assets/ui/utils.mjs";

export function renderContextInspector(state) {
  const lesson = state.data?.lesson || {};
  if (state.activePanel === "review") return renderEvidenceInspector(state);
  if (state.activePanel === "transcript") {
    return `${renderSourceVerification(state)}${renderIntegrity(state, lesson)}`;
  }
  return `${renderAtAGlance(state, lesson)}${renderQuickPractice(state, lesson)}${renderMusicExport(state, lesson)}${renderIntegrity(state, lesson)}`;
}

function renderEvidenceInspector(state) {
  const item = selectedReviewItem(state);
  if (!item) {
    return `<section class="rail-section evidence-inspector inspector-empty">
      <p class="side-label">Critical note</p>
      <h2>Queue clear</h2>
      <p class="supporting">No machine suggestion is waiting in this view. Every earlier decision stays in the project's revision record.</p>
    </section>`;
  }
  const actor = humanActor(state);
  const eventId = encodeId(item.event_id);
  const sourceId = itemSource(item);
  const start = itemTime(item);
  const end = start + itemDuration(item);
  const duration = Math.max(sourceDurationSeconds(state, sourceId), end, 0.001);
  const left = Math.min(100, Math.max(0, start / duration * 100));
  const width = Math.max(0.8, Math.min(100 - left, itemDuration(item) / duration * 100));
  const canRevise = typeof item.body_value === "string";
  return `<section class="rail-section evidence-inspector" aria-labelledby="evidence-inspector-title">
    <p class="side-label">Critical note</p>
    <h2 id="evidence-inspector-title">Verify before deciding</h2>
    <ol class="decision-steps">
      <li class="decision-step">
        <p class="step-label"><span class="step-n">1</span>Listen</p>
        <div class="source-span" role="img" aria-label="Selected evidence spans ${formatTime(start, false)} to ${formatTime(end, false)} of this source">
          <span class="source-span-range" style="left:${left}%;width:${width}%"></span>
        </div>
        <div class="source-span-times"><span>${formatTime(start, false)}</span><span>${formatTime(itemDuration(item), false)} span</span><span>${formatTime(end, false)}</span></div>
        <button class="time-button source-span-play" data-seek="${start}" data-source="${escapeHTML(sourceId)}">Play the span from <span class="tc">${formatTime(start, false)}</span></button>
        <p class="supporting source-span-name">${escapeHTML(sourceName(state, sourceId))}</p>
      </li>
      <li class="decision-step">
        <p class="step-label"><span class="step-n">2</span>Read</p>
        <div class="inspector-claim"><span class="tag suggested"><span class="sigil sigil-suggested" aria-hidden="true"></span>Suggested · not yet evidence</span>
          <blockquote>${escapeHTML(item.display_text)}</blockquote></div>
        <dl class="inspector-provenance">
          <dt>Generator</dt><dd class="token">${escapeHTML(item.generator_id, "Not recorded")}</dd>
          <dt>Confidence</dt><dd>${renderConfidence(item.confidence)}</dd>
          <dt>Rights record</dt><dd class="token">${escapeHTML(item.rights_id, "Not recorded")}</dd>
          <dt>Evidence kind</dt><dd>${escapeHTML(String(item.content_kind || "evidence").replaceAll("_", " "))}</dd>
        </dl>
      </li>
      <li class="decision-step">
        <p class="step-label"><span class="step-n">3</span>Decide</p>
        <label class="inspector-attribution">Attribute accepted evidence to
          <select data-attribution="${eventId}">${renderActorAttributionOptionsHTML(state, item.actor_id, true)}</select>
        </label>
        <div class="inspector-actions">
          <button class="primary-button" data-accept="${eventId}" data-busy-key="review-${eventId}" aria-keyshortcuts="A" ${actor ? "" : "disabled"}>Accept as evidence &amp; next <kbd aria-hidden="true">A</kbd></button>
          ${canRevise ? `<button class="secondary-button" data-revise="${eventId}" aria-keyshortcuts="R" ${actor ? "" : "disabled"}>Revise before accepting <kbd aria-hidden="true">R</kbd></button>` : ""}
          <button class="text-button reject-button" data-reject="${eventId}" data-busy-key="review-${eventId}" aria-keyshortcuts="X" ${actor ? "" : "disabled"}>Reject suggestion</button>
        </div>
        ${actor ? "" : '<p class="supporting decision-blocked">Choose who is reviewing before recording a decision.</p>'}
      </li>
    </ol>
    <p class="privacy-note inspector-audit">Each decision is appended to the revision record. The machine's original reading is never overwritten.</p>
  </section>`;
}

export function renderSourceVerification(state) {
  const sourceId = state.activeSourceId;
  const duration = sourceDurationSeconds(state, sourceId);
  const name = sourceName(state, sourceId);
  return `<section class="rail-section source-verification" aria-labelledby="source-verification-title">
    <p class="side-label">Source verification</p>
    <h2 id="source-verification-title">${escapeHTML(name, "No source selected")}</h2>
    ${sourceId ? `<p class="source-span-label">Source span</p>
      <p class="source-span-times"><span>${formatTime(0, false)}</span><span>${formatTime(duration, false)}</span></p>
      <button class="time-button source-start" data-seek="0" data-source="${escapeHTML(sourceId)}">Listen from start</button>`
      : '<p class="supporting">Import or select a local recording to inspect its evidence span.</p>'}
    <p class="privacy-note">Times are positions in the source. No audio amplitude is inferred here.</p>
  </section>`;
}

export function renderAtAGlance(state, lesson) {
  const summary = lesson.summary || {};
  const next = list(lesson.practice_plan?.tasks).find((task) => !task.completed);
  return `<section class="rail-section glance-section">
    <p class="side-label">Lesson at a glance</p>
    <p class="episode-summary">${escapeHTML(summary.overview, "Evidence is ready for review.")}</p>
    ${list(summary.topics).length ? `<p class="topic-line"><strong>Focus</strong> <em>${escapeHTML(summary.topics[0].label)}</em></p>` : ""}
    ${next ? `<div class="next-task"><span>Next practice task</span><p>${escapeHTML(next.text)}</p></div>` : '<p class="supporting">No open practice task backed by evidence.</p>'}
    <button class="text-button" data-tab="lesson">Open all lesson notes</button>
  </section>`;
}

export function renderQuickPractice(state, lesson) {
  const actor = humanActor(state);
  const tasks = list(lesson.practice_plan?.tasks);
  return `<section class="rail-section">
    <div class="section-heading">
      <div><p class="side-label">Practice plan</p></div>
      <span class="count-label">${tasks.filter((item) => item.completed).length} of ${tasks.length} done</span>
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
    <p class="side-label">Export</p>
    <div class="export-block">
    <div class="section-heading"><h2>Music transcript</h2><span class="count-label">${notes.length} ${notes.length === 1 ? "note" : "notes"}</span></div>
    <p class="supporting">Timed note evidence as research CSV or playable MIDI.</p>
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
    ${notes.length ? "" : '<p class="privacy-note">Run note transcription first to enable export.</p>'}
    </div>
    <div class="export-block">
    <h2>Transcript</h2>
    <p class="supporting">Accepted evidence by default. Machine suggestions can be included; they are labelled as unreviewed.</p>
    <label>Format <select data-transcript-format><option value="html" ${transcriptFormat === "html" ? "selected" : ""}>HTML</option><option value="text" ${transcriptFormat === "text" ? "selected" : ""}>TXT</option><option value="webvtt" ${transcriptFormat === "webvtt" ? "selected" : ""}>WebVTT</option></select></label>
    <label>Evidence layer <select data-transcript-layer><option value="accepted_only">Accepted evidence only</option><option value="include_machine_suggestions">Include machine suggestions (unreviewed)</option></select></label>
    ${inlineControls ? `<label class="consent-check"><input type="checkbox" data-transcript-timestamps checked><span>Show inline timestamps</span></label><label>Timestamp interval (ms)<input type="number" min="1" value="60000" data-transcript-interval></label><label>Pause marker <select data-transcript-pause><option value="">Off</option><option value="1000">1 second</option><option value="2000">2 seconds</option><option value="3000">3 seconds</option></select></label>` : '<p class="privacy-note">WebVTT always carries cue timing; inline timestamps and pause markers are not rendered.</p>'}
    <label class="consent-check"><input type="checkbox" data-transcript-rights><span>I am authorized to export this lesson evidence.</span></label>
    <label class="consent-check"><input type="checkbox" data-transcript-losses><span>I acknowledge documented format losses.</span></label>
    <div class="dialog-actions"><button class="secondary-button" data-action="export-transcript" data-busy-key="transcript-export" ${transcriptReady ? "" : "disabled"}>Export transcript</button></div>
    </div>
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
    <p class="privacy-note">Machine output stays pencilled in, separate from evidence, until a named person accepts it.</p>
  </section>`;
}
