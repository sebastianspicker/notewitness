import {
  escapeHTML,
  formatTime,
  humanActor,
  humanActors,
  isMultiSource,
  list,
  mediaCount,
  sourceDurationSeconds,
  sourceName,
} from "/assets/ui/utils.mjs";
import { renderProcessing } from "/assets/ui/processing.mjs";
import { renderTimeline } from "/assets/ui/timeline.mjs";
import { renderPanel } from "/assets/ui/panels.mjs";
import {
  renderAtAGlance,
  renderIntegrity,
  renderMusicExport,
  renderQuickPractice,
} from "/assets/ui/context.mjs";
import { renderTransport } from "/assets/ui/transport.mjs";

export function renderWorkbench(state) {
  const lesson = state.data?.lesson || {};
  const multi = isMultiSource(state);
  return `
    <div class="app-shell ${multi ? "is-multi-source" : "is-single-source"}" data-app-shell>
      ${renderHeader(state, lesson)}
      <div class="notice-region" data-notice-region aria-live="polite">
        ${renderNotice(state.notice)}
      </div>
      <div class="workbench-layout body">
        <aside class="tool-rail col col-side col-left" aria-label="Sources, processing, and music tools">
          ${renderSources(state, lesson)}
          ${renderProcessing(state)}
          ${renderUtilities(state)}
        </aside>
        <main class="workspace col-main" id="workbench-main" tabindex="-1">
          ${renderWorkspaceTabs(state)}
          <div data-timeline-root>${renderTimeline(state)}</div>
          <div data-workspace-panel>${renderPanel(state)}</div>
        </main>
        <aside class="context-rail col col-side col-right" aria-label="Lesson overview">
          ${renderAtAGlance(state, lesson)}
          ${renderQuickPractice(state, lesson)}
          ${renderMusicExport(state, lesson)}
          ${renderIntegrity(state, lesson)}
        </aside>
      </div>
      ${renderTransport(state)}
      <audio data-media preload="metadata"></audio>
      ${renderDialog(state)}
    </div>`;
}

function renderHeader(state, lesson) {
  const actor = humanActor(state);
  const project = state.data?.project || {};
  return `
    <header class="app-header">
      <div class="brand-block brand" aria-label="NoteWitness: local evidence workbench">
        <img class="brand-mark" src="/assets/notewitness-mark.svg" alt="" aria-hidden="true">
        <div class="brand-copy">
          <p class="brand-name">NoteWitness</p>
          <p class="brand-purpose">Local evidence workbench</p>
        </div>
      </div>
      <div class="project-heading project">
        <h1 class="project-title">${escapeHTML(lesson.title, "Untitled lesson")}</h1>
        <p class="privacy-state">${escapeHTML(project.network_mode, "offline")} · stays on this device${project.saved ? "" : " · local changes pending"}</p>
      </div>
      <div class="header-actions header-end">
        ${humanActors(state).length ? `<label class="reviewer-picker">Reviewing as
          <select data-author>${renderAuthorOptions(state)}</select></label>`
          : '<button class="secondary-button" data-action="open-reviewer-setup">Set up reviewer</button>'}
        <button class="linkish" data-action="open-bookmark"
          ${actor && state.activeSourceId ? "" : "disabled"}>Bookmark</button>
      </div>
    </header>`;
}

function renderAuthorOptions(state) {
  const actors = humanActors(state);
  const options = actors.map((actor) => {
    const selected = actor.id === state.authorId ? "selected" : "";
    const label = actor.instrument_role
      ? `${actor.role} · ${actor.instrument_role}` : actor.role;
    return `<option value="${escapeHTML(actor.id)}" ${selected}>${escapeHTML(label)}</option>`;
  }).join("");
  return `<option value="" ${humanActor(state) ? "" : "selected"} disabled>Choose reviewer…</option>${options}`;
}

function renderWorkspaceTabs(state) {
  const pending = list(state.data?.lesson?.transcript_suggestions).length;
  const accepted = list(state.data?.lesson?.full_transcript).length;
  const tabs = [
    ["review", "Review queue", pending],
    ["transcript", "Full transcript", accepted],
    ["lesson", "Lesson notes", null],
  ];
  return `<nav class="workspace-tabs review-nav" aria-label="Workspace views">
    <div class="workspace-tabset" role="tablist" aria-label="Workspace views">
      ${tabs.map(([key, label, count]) => `<button id="tab-${key}" class="workspace-tab"
      role="tab" aria-controls="panel-${key}" aria-selected="${state.activePanel === key}"
      tabindex="${state.activePanel === key ? "0" : "-1"}" data-tab="${key}">
      ${escapeHTML(label)}${count === null ? "" : ` <span class="n">${count}</span>`}</button>`).join("")}
    </div>
  </nav>`;
}

function renderSources(state, lesson) {
  const media = list(state.data?.media);
  const activeName = sourceName(state, state.activeSourceId);
  const multi = media.length > 1;
  const count = mediaCount(state);
  return `<section class="rail-section source-section side-block ${multi ? "" : "is-single-source"}" id="sources-panel">
    <p class="side-label">Source${multi ? ` · ${count}` : ""}</p>
    ${media.length ? `
      <p class="source-title">${escapeHTML(activeName, "No source")}</p>
      <p class="source-meta">
        <span>${formatTime(sourceDurationSeconds(state), false)}</span>
        ${multi ? ` · local` : ` · local project`}
      </p>
      ${multi ? `<label class="field-label" for="source-select">Playback source</label>
      <select id="source-select" class="full-select" data-source-select>
        ${media.map((item, index) => `<option value="${escapeHTML(item.source_id)}"
          ${item.source_id === state.activeSourceId ? "selected" : ""}>
          ${escapeHTML(sourceName(state, item.source_id), `Source ${index + 1}`)}</option>`).join("")}
      </select>` : `<select id="source-select" class="full-select is-hidden" data-source-select aria-hidden="true" tabindex="-1">
        <option value="${escapeHTML(state.activeSourceId)}" selected>${escapeHTML(activeName)}</option>
      </select>`}`
      : `<div class="empty-state"><strong>No playable media</strong><p>Import a lesson recording to begin.</p>
      <select id="source-select" class="full-select is-hidden" data-source-select aria-hidden="true" tabindex="-1"></select></div>`}
    <label class="file-button ${state.importing ? "is-busy" : ""}">
      <input type="file" accept="audio/*,video/*" data-import-file ${state.importing ? "disabled" : ""}>
      <span>${state.importing ? "Importing locally…" : multi ? "Import another recording" : "Import recording"}</span>
    </label>
    <p class="privacy-note foot">Stays on this device. Nothing is uploaded.</p>
  </section>`;
}

function renderUtilities(state) {
  const tunerRunning = Boolean(state.tuner);
  const metroRunning = Boolean(state.metronome);
  return `<section class="rail-section utilities-section side-block">
    <p class="side-label">Studio</p>
    <div class="utility-block utility-compact">
      <div class="utility-heading"><h3>Tuner</h3>
        <button class="secondary-button" data-action="tuner">${tunerRunning ? "Stop" : "Start"}</button></div>
      <div class="tuner-reading"><strong data-tuner-note>--</strong><span data-tuner-hz>${tunerRunning ? "Listening…" : "Mic off"}</span></div>
      <div class="tuner-meter" role="meter" aria-label="Tuning offset" aria-valuemin="-50" aria-valuemax="50" aria-valuenow="0">
        <span class="tuner-center" aria-hidden="true"></span><i data-tuner-meter></i></div>
    </div>
    <div class="utility-block utility-compact">
      <div class="utility-heading"><h3>Metronome</h3>
        <button class="secondary-button" data-action="metronome">${metroRunning ? "Stop" : "Start"}</button></div>
      <div class="tempo-control"><button data-action="tempo-down" aria-label="Decrease tempo">−</button>
        <output data-bpm>${escapeHTML(state.tempo)}</output><span>BPM</span>
        <button data-action="tempo-up" aria-label="Increase tempo">+</button></div>
    </div>
  </section>`;
}

export function renderNotice(notice) {
  if (!notice?.message) return "";
  const role = notice.kind === "error" ? "alert" : "status";
  return `<div class="notice" data-kind="${escapeHTML(notice.kind || "info")}" role="${role}">
    <span>${escapeHTML(notice.message)}</span><button data-action="dismiss-notice" aria-label="Dismiss message">Dismiss</button></div>`;
}

function renderDialog(state) {
  const dialog = state.dialog;
  if (!dialog) return '<dialog class="editor-dialog" data-editor-dialog></dialog>';
  if (dialog.mode === "bookmark") {
    return `<dialog class="editor-dialog" data-editor-dialog aria-labelledby="dialog-title">
      <form data-dialog-form><div class="dialog-heading"><div><p class="kicker">Exact-time marker</p><h2 id="dialog-title">Add bookmark</h2></div>
        <button type="button" class="text-button" data-action="close-dialog">Close</button></div>
      <p>Save ${formatTime(dialog.timeSeconds)} in ${escapeHTML(dialog.sourceName)}.</p>
      <label>Bookmark label<input name="label" maxlength="1000" required autofocus value=""></label>
      <div class="dialog-actions"><button type="button" class="secondary-button" data-action="close-dialog">Cancel</button>
        <button class="primary-button" type="submit">Save bookmark</button></div></form></dialog>`;
  }
  if (dialog.mode === "reviewer-setup") {
    return `<dialog class="editor-dialog" data-editor-dialog aria-labelledby="dialog-title">
      <form data-dialog-form><div class="dialog-heading"><div><p class="kicker">Local evidence author</p><h2 id="dialog-title">Set up reviewer</h2></div>
        <button type="button" class="text-button" data-action="close-dialog">Close</button></div>
      <p>Create a private project role before recording or accepting evidence. This is not an account.</p>
      <label>Role<input name="role" maxlength="256" required autofocus placeholder="teacher, researcher, or student"></label>
      <div class="dialog-actions"><button type="button" class="secondary-button" data-action="close-dialog">Cancel</button>
        <button class="primary-button" type="submit">Create local reviewer</button></div></form></dialog>`;
  }
  return `<dialog class="editor-dialog" data-editor-dialog aria-labelledby="dialog-title">
    <form data-dialog-form><div class="dialog-heading"><div><p class="kicker">Human revision</p><h2 id="dialog-title">${dialog.mode === "revise-suggestion" ? "Revise machine suggestion" : "Edit accepted evidence"}</h2></div>
      <button type="button" class="text-button" data-action="close-dialog">Close</button></div>
    <p class="original-evidence"><span>Original</span>${escapeHTML(dialog.originalText)}</p>
    <label>Corrected evidence<textarea name="replacement_text" maxlength="20000" rows="5" required autofocus>${escapeHTML(dialog.originalText, "")}</textarea></label>
    <label>Reason for revision<textarea name="reason" maxlength="4000" rows="2" required>${escapeHTML(dialog.reason || "Corrected during local evidence review.")}</textarea></label>
    <div class="dialog-actions"><button type="button" class="secondary-button" data-action="close-dialog">Cancel</button>
      <button class="primary-button" type="submit">Save revision</button></div></form></dialog>`;
}
