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
  renderContextInspector,
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
      <div class="workbench-layout">
        <aside class="tool-rail" aria-label="Project navigation and source controls">
          ${renderWorkspaceTabs(state)}
          <div class="rail-tools">
            ${renderSources(state)}
            ${renderProcessing(state)}
            ${renderUtilities(state)}
          </div>
          <dl class="keyboard-hint" aria-label="Keyboard shortcuts">
            <dt><kbd>J</kbd><kbd>K</kbd></dt><dd>next · previous</dd>
            <dt><kbd>A</kbd></dt><dd>accept</dd>
            <dt><kbd>R</kbd></dt><dd>revise</dd>
            <dt><kbd>X</kbd></dt><dd>reject</dd>
            <dt><kbd>Space</kbd></dt><dd>play · pause</dd>
          </dl>
        </aside>
        <main class="workspace" id="workbench-main" tabindex="-1">
          <div data-timeline-root>${renderTimeline(state)}</div>
          <div data-workspace-panel>${renderPanel(state)}</div>
        </main>
        <aside class="context-rail" data-context-inspector aria-label="Evidence inspector">
          ${renderContextInspector(state)}
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
  const dark = state.theme === "dark";
  return `
    <header class="app-header">
      <p class="brand">
        ${renderMark()}
        <span class="brand-name">Note<i>Witness</i></span>
        <span class="visually-hidden">: local evidence workbench</span>
      </p>
      <div class="project-heading">
        <h1 class="project-title">${escapeHTML(lesson.title, "Untitled lesson")}</h1>
        <p class="privacy-state"><span class="privacy-mode">${escapeHTML(project.network_mode, "offline")}</span> · stays on this device${project.saved ? "" : ' · <span class="unsaved">local changes pending</span>'}</p>
      </div>
      <div class="header-actions">
        ${humanActors(state).length ? `<label class="reviewer-picker"><span>Reviewing as</span>
          <select data-author>${renderAuthorOptions(state)}</select></label>`
          : '<button class="secondary-button" data-action="open-reviewer-setup">Set up reviewer</button>'}
        <button class="linkish bookmark-action" data-action="open-bookmark"
          ${actor && state.activeSourceId ? "" : "disabled"}>Bookmark</button>
        <button class="theme-toggle" type="button" data-action="toggle-theme" aria-label="Dark theme" aria-pressed="${dark}" title="Dark theme">
          <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><circle cx="10" cy="10" r="6.25" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M10 3.75a6.25 6.25 0 0 1 0 12.5z" fill="currentColor"/></svg>
        </button>
      </div>
    </header>`;
}

function renderMark() {
  return `<svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
    <path d="M4.5 23V9l9 14V9M18.5 9 21 23l3-9 3 9 1.5-14" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2"/>
    <path class="brand-mark-witness" d="M16 5v22" fill="none" stroke-linecap="round" stroke-width="1.6"/>
    <circle cx="16" cy="25" r="2.2" fill="currentColor"/>
  </svg>`;
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
  return `<nav class="workspace-tabs" aria-label="Workspace views">
    <p class="side-label rail-label">Contents</p>
    <div class="workspace-tabset" role="tablist" aria-label="Workspace views">
      ${tabs.map(([key, label, count]) => `<button id="tab-${key}" class="workspace-tab"
      role="tab" aria-controls="panel-${key}" aria-selected="${state.activePanel === key}"
      tabindex="${state.activePanel === key ? "0" : "-1"}" data-tab="${key}">
      <span class="tab-label">${escapeHTML(label)}</span>${count === null ? "" : `<span class="tab-leader" aria-hidden="true"></span><span class="n">${count}</span>`}</button>`).join("")}
    </div>
  </nav>`;
}

function renderSources(state) {
  const media = list(state.data?.media);
  const activeName = sourceName(state, state.activeSourceId);
  const multi = media.length > 1;
  const count = mediaCount(state);
  return `<section class="rail-section source-section ${multi ? "" : "is-single-source"}" id="sources-panel">
    <p class="side-label">Source${multi ? ` · ${count}` : ""}</p>
    ${media.length ? `
      <p class="source-title">${escapeHTML(activeName, "No source")}</p>
      <p class="source-meta">${formatTime(sourceDurationSeconds(state), false)} · ${multi ? "local" : "local project"}</p>
      ${multi ? `<label class="field-label" for="source-select">Playback source</label>
      <select id="source-select" class="full-select" data-source-select>
        ${media.map((item, index) => `<option value="${escapeHTML(item.source_id)}"
          ${item.source_id === state.activeSourceId ? "selected" : ""}>
          ${escapeHTML(sourceName(state, item.source_id), `Source ${index + 1}`)}</option>`).join("")}
      </select>` : `<select id="source-select" class="full-select is-hidden" data-source-select aria-hidden="true" tabindex="-1">
        <option value="${escapeHTML(state.activeSourceId)}" selected>${escapeHTML(activeName)}</option>
      </select>`}`
      : `<div class="empty-state rail-empty"><strong>No recording yet</strong><p>Import a lesson recording to start reviewing.</p>
      <select id="source-select" class="full-select is-hidden" data-source-select aria-hidden="true" tabindex="-1"></select></div>`}
    <label class="file-button ${state.importing ? "is-busy" : ""}">
      <input type="file" accept="audio/*,video/*" data-import-file ${state.importing ? "disabled" : ""}>
      <span>${state.importing ? "Importing locally…" : multi ? "Import another recording" : "Import recording"}</span>
    </label>
    <p class="privacy-note">Imported media stays on this device. Nothing is uploaded.</p>
  </section>`;
}

function renderUtilities(state) {
  const tunerRunning = Boolean(state.tuner);
  const metroRunning = Boolean(state.metronome);
  return `<section class="rail-section utilities-section">
    <p class="side-label">Studio</p>
    <div class="utility-block">
      <div class="utility-heading"><h3>Tuner</h3>
        <button class="text-button" data-action="tuner" aria-pressed="${tunerRunning}">${tunerRunning ? "Stop" : "Start"}</button></div>
      <div class="tuner-reading"><strong data-tuner-note>--</strong><span data-tuner-hz>${tunerRunning ? "Listening…" : "Microphone off"}</span></div>
      <div class="tuner-meter" role="meter" aria-label="Tuning offset in cents" aria-valuemin="-50" aria-valuemax="50" aria-valuenow="0">
        <span class="tuner-center" aria-hidden="true"></span><i data-tuner-meter></i></div>
    </div>
    <div class="utility-block">
      <div class="utility-heading"><h3>Metronome</h3>
        <button class="text-button" data-action="metronome" aria-pressed="${metroRunning}">${metroRunning ? "Stop" : "Start"}</button></div>
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
    <span>${escapeHTML(notice.message)}</span><button class="text-button" data-action="dismiss-notice" aria-label="Dismiss message">Dismiss</button></div>`;
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
