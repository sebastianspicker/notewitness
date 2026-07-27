import {
  escapeHTML,
  humanActors,
  list,
  renderNotice,
  renderPanel,
  renderProcessing,
  renderTimeline,
  renderWorkbench,
} from "/assets/workbench_ui.mjs";
import { createApi } from "/assets/js/api.mjs";
import { createPlayback } from "/assets/js/playback.mjs";
import { createProcessing } from "/assets/js/processing.mjs";
import { createActions } from "/assets/js/actions.mjs";

const app = document.querySelector("#app");

const state = {
  data: null,
  processing: { runtime: {}, jobs: [] },
  media: null,
  mediaDurations: {},
  recorder: null,
  chunks: [],
  tuner: null,
  metronome: null,
  tempo: 72,
  authorId: "",
  activeSourceId: "",
  activePanel: "review",
  reviewKind: "all",
  transcriptExportFormat: "html",
  query: "",
  visibleLaneKinds: new Set(),
  notice: null,
  dialog: null,
  busy: new Set(),
  importing: false,
  captureBytes: 0,
  captureStartedAt: 0,
  captureTimeout: 0,
  captureInterval: 0,
  captureTooLarge: false,
  captureDiscarded: false,
  captureState: "idle",
  jobPoll: 0,
};

/** Mutable controller bag shared by factories; methods are filled in as modules wire up. */
const c = { state, app };
Object.assign(c, createApi(c));

function setNotice(message, kind = "info") {
  state.notice = message ? { message, kind } : null;
  const region = app.querySelector("[data-notice-region]");
  if (region) region.innerHTML = renderNotice(state.notice);
}

function setBusy(key, busy) {
  if (busy) state.busy.add(key);
  else state.busy.delete(key);
  app.querySelectorAll(`[data-busy-key="${CSS.escape(key)}"]`).forEach((button) => {
    button.disabled = busy;
    button.setAttribute("aria-busy", String(busy));
  });
}

async function runMutation(key, activity, operation) {
  if (state.busy.has(key)) return false;
  setBusy(key, true);
  setNotice(`${activity}…`, "info");
  try {
    await operation();
    return true;
  } catch (error) {
    if (error.status === 409) {
      await load({ preservePlayback: true, quiet: true }).catch(() => {});
      setNotice("The project changed in another action. The latest local version is shown; please try again.", "error");
    } else {
      setNotice(`${activity} could not be completed: ${error.message}`, "error");
    }
    return false;
  } finally {
    setBusy(key, false);
  }
}

function render(restore = null) {
  app.innerHTML = renderWorkbench(state);
  c.bindMedia(restore);
  c.openRenderedDialog();
  if (state.pendingFocus) {
    const target = app.querySelector(`[data-review-card="${CSS.escape(state.pendingFocus)}"]`);
    state.pendingFocus = "";
    target?.focus();
  }
}

function renderPreservingPlayback() {
  const restore = c.snapshotPlayback();
  render(restore);
}

function refreshPanel() {
  const panel = app.querySelector("[data-workspace-panel]");
  if (panel) panel.innerHTML = renderPanel(state);
  app.querySelectorAll("[data-tab]").forEach((tab) => {
    const selected = tab.dataset.tab === state.activePanel;
    if (tab.getAttribute("role") === "tab") {
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
    }
  });
}

function refreshTimeline() {
  const root = app.querySelector("[data-timeline-root]");
  if (root) root.innerHTML = renderTimeline(state);
  c.syncPlayback();
}

function refreshProcessing() {
  const section = app.querySelector(".processing-section");
  if (section) section.outerHTML = renderProcessing(state);
}

function selectPanel(name, focus = false) {
  if (!["review", "transcript", "lesson"].includes(name)) return;
  state.activePanel = name;
  refreshPanel();
  if (focus) app.querySelector(`[data-tab="${name}"][role="tab"]`)?.focus();
}

Object.assign(c, {
  setNotice,
  setBusy,
  runMutation,
  render,
  renderPreservingPlayback,
  refreshPanel,
  refreshTimeline,
  refreshProcessing,
  selectPanel,
});

Object.assign(c, createPlayback(c));

async function load({ preservePlayback = false, quiet = false, skipProcessing = false } = {}) {
  const restore = preservePlayback ? c.snapshotPlayback() : null;
  if (!quiet && !state.data) app.innerHTML = '<p class="loading-state" role="status">Opening the private lesson workbench…</p>';
  try {
    const dataPromise = c.request("/api/workbench");
    const processingPromise = skipProcessing ? Promise.resolve(state.processing)
      : c.request("/api/jobs").catch(() => ({ runtime: {}, jobs: [] }));
    const [data, processing] = await Promise.all([dataPromise, processingPromise]);
    state.data = data;
    state.processing = processing || { runtime: {}, jobs: [] };
    if (!humanActors(state).some((actor) => actor.id === state.authorId)) state.authorId = "";
    const media = list(state.data?.media);
    if (!media.some((item) => item.source_id === state.activeSourceId)) {
      state.activeSourceId = media[0]?.source_id || "";
    }
    state.tempo = Number(state.data?.metronome?.bpm || state.tempo || 72);
    if (!state.visibleLaneKinds.size) {
      state.visibleLaneKinds = new Set(list(state.data?.timeline?.lanes).map((lane) => lane.kind));
    }
    render(restore);
    if (list(state.processing.jobs).some((job) => ["queued", "running", "cancelling"].includes(job.state))) {
      c.startJobPolling();
    }
  } catch (error) {
    app.innerHTML = `<main class="fatal-state" role="alert"><p class="kicker">Local project unavailable</p>
      <h1>The workbench could not open</h1><p>${escapeHTML(error.message)}</p>
      <button class="primary-button" data-action="reload">Try again</button></main>`;
  }
}

c.load = load;
Object.assign(c, createProcessing(c));
Object.assign(c, createActions(c));

function handleClick(event) {
  const tab = event.target.closest("[data-tab]");
  if (tab) {
    selectPanel(tab.dataset.tab, tab.getAttribute("role") === "tab");
    return;
  }
  const seekButton = event.target.closest("[data-seek]");
  if (seekButton) {
    c.seek(Number(seekButton.dataset.seek), seekButton.dataset.source);
    return;
  }
  const accept = event.target.closest("[data-accept]");
  if (accept) {
    c.acceptSuggestion(accept.dataset.accept);
    return;
  }
  const acceptRelation = event.target.closest("[data-accept-relation]");
  if (acceptRelation) {
    c.reviewRelation(acceptRelation.dataset.acceptRelation, "accept");
    return;
  }
  const rejectRelation = event.target.closest("[data-reject-relation]");
  if (rejectRelation) {
    c.reviewRelation(rejectRelation.dataset.rejectRelation, "reject");
    return;
  }
  const revise = event.target.closest("[data-revise]");
  if (revise) {
    c.openRevision(revise.dataset.revise, "revise-suggestion");
    return;
  }
  const edit = event.target.closest("[data-edit]");
  if (edit) {
    c.openRevision(edit.dataset.edit, "revise-accepted");
    return;
  }
  const cancelJob = event.target.closest("[data-cancel-job]");
  if (cancelJob) {
    c.jobAction(cancelJob.dataset.cancelJob, "cancel");
    return;
  }
  const retryJob = event.target.closest("[data-retry-job]");
  if (retryJob) {
    c.jobAction(retryJob.dataset.retryJob, "retry");
    return;
  }
  const actionButton = event.target.closest("[data-action]");
  if (actionButton?.dataset.action === "open-reviewer-setup") {
    c.openReviewerSetup();
    return;
  }
  if (actionButton) c.action(actionButton.dataset.action);
}

function handleChange(event) {
  if (event.target.matches("[data-author]")) {
    state.authorId = event.target.value;
    renderPreservingPlayback();
  } else if (event.target.matches("[data-source-select]")) {
    c.switchSource(event.target.value);
  } else if (event.target.matches("[data-review-kind]")) {
    state.reviewKind = event.target.value;
    refreshPanel();
  } else if (event.target.matches("[data-transcript-format]")) {
    state.transcriptExportFormat = event.target.value;
    renderPreservingPlayback();
  } else if (event.target.matches("[data-lane-kind]")) {
    if (event.target.checked) state.visibleLaneKinds.add(event.target.dataset.laneKind);
    else state.visibleLaneKinds.delete(event.target.dataset.laneKind);
    refreshTimeline();
  } else if (event.target.matches("[data-practice]")) {
    c.updatePractice(event.target);
  } else if (event.target.matches("[data-import-file]")) {
    c.importMedia(event.target.files?.[0]);
  }
}

function handleInput(event) {
  if (!event.target.matches("[data-query]")) return;
  state.query = event.target.value;
  const cursor = event.target.selectionStart;
  refreshPanel();
  const replacement = app.querySelector("[data-query]");
  replacement?.focus();
  replacement?.setSelectionRange(cursor, cursor);
}

function handleSubmit(event) {
  if (!event.target.matches("[data-dialog-form]")) return;
  event.preventDefault();
  if (event.target.reportValidity()) c.submitDialog(event.target);
}

function handleKeydown(event) {
  const currentTab = event.target.closest('[role="tab"]');
  if (currentTab && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
    event.preventDefault();
    const tabs = [...app.querySelectorAll('[role="tab"]')];
    const index = tabs.indexOf(currentTab);
    const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
      : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    selectPanel(tabs[nextIndex].dataset.tab, true);
    return;
  }
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const editing = event.target.matches("input, textarea, select, button, [contenteditable=true]");
  if (!editing && /^[1-7]$/.test(event.key)) {
    const lane = app.querySelector(`[data-lane] kbd:first-child`);
    const matching = [...app.querySelectorAll("[data-lane]")].find((item) => {
      return item.querySelector("kbd")?.textContent?.trim() === event.key;
    });
    if (matching || lane) {
      event.preventDefault();
      (matching || lane.closest("[data-lane]"))?.scrollIntoView({ block: "nearest" });
      (matching || lane.closest("[data-lane]"))?.querySelector("button")?.focus();
    }
  } else if (!editing && event.key === " ") {
    event.preventDefault();
    c.action("play");
  }
}

app.addEventListener("click", handleClick);
app.addEventListener("change", handleChange);
app.addEventListener("input", handleInput);
app.addEventListener("submit", handleSubmit);
app.addEventListener("cancel", (event) => {
  if (event.target.matches("[data-editor-dialog]")) {
    event.preventDefault();
    c.closeDialog();
  }
});
document.addEventListener("keydown", handleKeydown);
window.addEventListener("beforeunload", () => {
  c.stopJobPolling();
  state.tuner?.stop();
  c.stopMetronome();
  state.recorder?.stream?.getTracks().forEach((track) => track.stop());
});

load();
