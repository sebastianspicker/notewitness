/** Browser-session behavior for the mock Evidence Ledger. */

const app = document.querySelector("#app");
const reviewTemplate = document.querySelector('template[data-demo-panel="review"]');
const emptyReviewTemplate = document.querySelector('template[data-demo-panel="review-empty"]');
const panelTemplates = new Map([...document.querySelectorAll("template[data-demo-panel]")]
  .map((template) => [template.dataset.demoPanel, template]));
const contextTemplates = new Map([...document.querySelectorAll("template[data-demo-context]")]
  .map((template) => [template.dataset.demoContext, template]));
const reviewContextTemplates = new Map([...document.querySelectorAll("template[data-demo-review-context]")]
  .map((template) => [template.dataset.demoReviewContext, template]));
const supportedActions = new Set(["toggle-theme", "play", "seek-back", "seek-forward"]);
const initialCards = [...app.querySelectorAll("[data-review-card]")];
const initialTranscriptCount = Number(app.querySelector("#tab-transcript .n")?.textContent) || 0;
const state = {
  activeId: initialCards[0]?.dataset.reviewCard || "",
  activePanel: "review",
  audit: [],
  currentSeconds: 5,
  filter: "",
  isPlaying: false,
  queue: initialCards.map((card) => card.dataset.reviewCard),
  reviewKind: "all",
};
const durationSeconds = 30;

function cloneTemplate(template) {
  return template?.content.cloneNode(true) || document.createDocumentFragment();
}

function showNotice(message, kind = "info") {
  const region = app.querySelector("[data-notice-region]");
  if (!region) return;
  const notice = document.createElement("div");
  notice.className = "notice";
  notice.dataset.kind = ["success", "error", "warning"].includes(kind) ? kind : "info";
  notice.setAttribute("role", "status");
  const text = document.createElement("span");
  text.textContent = message;
  const dismiss = document.createElement("button");
  dismiss.type = "button";
  dismiss.dataset.demoDismiss = "";
  dismiss.textContent = "Dismiss";
  notice.append(text, dismiss);
  region.replaceChildren(notice);
}

function disableUnavailableControls(root = app) {
  root.querySelectorAll("[data-import-file]").forEach((control) => { control.disabled = true; });
  root.querySelectorAll("[data-action]").forEach((control) => {
    if (!supportedActions.has(control.dataset.action)) control.disabled = true;
  });
}

function updateQueueCounts() {
  const count = state.queue.length;
  const accepted = state.audit.filter((entry) => entry.action !== "Rejected").length;
  app.querySelector("#tab-review .n")?.replaceChildren(String(count));
  app.querySelector("#tab-transcript .n")?.replaceChildren(
    String(initialTranscriptCount + accepted),
  );
  const kicker = app.querySelector("#panel-review .panel-header .kicker");
  if (kicker) kicker.textContent = `${count} ${count === 1 ? "item" : "items"}`;
}

function appendDecisionSummary(panel) {
  panel?.querySelector("[data-demo-decision-summary]")?.remove();
  if (!panel || !state.audit.length) return;
  const section = document.createElement("section");
  section.className = "demo-decision-summary";
  section.dataset.demoDecisionSummary = "";
  const title = document.createElement("h3");
  title.textContent = "Browser-session decisions";
  const detail = document.createElement("p");
  detail.className = "supporting";
  detail.textContent = "These mock outcomes are included in this view until reload.";
  const list = document.createElement("ol");
  state.audit.forEach((entry) => {
    const item = document.createElement("li");
    item.textContent = `${entry.action}: ${entry.text}`;
    list.append(item);
  });
  section.append(title, detail, list);
  panel.append(section);
}

function appendAudit() {
  if (state.activePanel !== "review") return;
  const inspector = app.querySelector("[data-context-inspector]");
  if (!inspector) return;
  inspector.querySelector("[data-demo-audit]")?.remove();
  const section = document.createElement("section");
  section.className = "rail-section demo-audit";
  section.dataset.demoAudit = "";
  const title = document.createElement("p");
  title.className = "side-label";
  title.textContent = "Browser-session decision log";
  const detail = document.createElement("p");
  detail.className = "supporting";
  detail.textContent = state.audit.length
    ? "Decisions stay in this in-memory log until the page reloads."
    : "No mock decisions recorded in this browser session.";
  section.append(title, detail);
  if (state.audit.length) {
    const list = document.createElement("ol");
    state.audit.forEach((entry) => {
      const item = document.createElement("li");
      item.textContent = `${entry.number}. ${entry.action}: ${entry.text}`;
      list.append(item);
    });
    section.append(list);
  }
  inspector.append(section);
}

function selectReview(id, focus = false) {
  if (!state.queue.includes(id)) return;
  state.activeId = id;
  app.querySelectorAll("[data-select-review]").forEach((card) => {
    const selected = card.dataset.selectReview === id;
    card.classList.toggle("is-selected", selected);
    card.setAttribute("aria-current", String(selected));
  });
  const inspector = app.querySelector("[data-context-inspector]");
  inspector?.replaceChildren(cloneTemplate(reviewContextTemplates.get(id)));
  appendAudit();
  disableUnavailableControls(inspector);
  if (focus) app.querySelector(`[data-review-card="${CSS.escape(id)}"]`)?.focus();
}

function showEmptyQueue() {
  const panel = app.querySelector("[data-workspace-panel]");
  panel?.replaceChildren(cloneTemplate(emptyReviewTemplate));
  const inspector = app.querySelector("[data-context-inspector]");
  inspector?.replaceChildren(cloneTemplate(reviewContextTemplates.get("empty")));
  updateQueueCounts();
  appendAudit();
  disableUnavailableControls(panel);
}

function restoreReviewPanel() {
  const panel = app.querySelector("[data-workspace-panel]");
  if (!panel) return;
  if (!state.queue.length) {
    showEmptyQueue();
    return;
  }
  panel.replaceChildren(cloneTemplate(reviewTemplate));
  panel.querySelectorAll("[data-review-card]").forEach((card) => {
    if (!state.queue.includes(card.dataset.reviewCard)) card.remove();
  });
  const search = panel.querySelector("[data-query]");
  if (search) search.value = state.filter;
  const kind = panel.querySelector("[data-review-kind]");
  if (kind) kind.value = state.reviewKind;
  updateQueueCounts();
  filterCurrentPanel();
  selectReview(state.queue.includes(state.activeId) ? state.activeId : state.queue[0]);
  disableUnavailableControls(panel);
}

function selectPanel(name, focus = false) {
  if (!panelTemplates.has(name)) return;
  state.activePanel = name;
  if (name === "review") restoreReviewPanel();
  else {
    const panel = app.querySelector("[data-workspace-panel]");
    const inspector = app.querySelector("[data-context-inspector]");
    panel?.replaceChildren(cloneTemplate(panelTemplates.get(name)));
    inspector?.replaceChildren(cloneTemplate(contextTemplates.get(name)));
    appendDecisionSummary(panel);
    disableUnavailableControls(panel);
    disableUnavailableControls(inspector);
  }
  app.querySelectorAll("[data-tab]").forEach((tab) => {
    const selected = tab.dataset.tab === name;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  if (focus) app.querySelector(`[data-tab="${name}"]`)?.focus();
}

function filterCurrentPanel() {
  const panel = app.querySelector("[data-workspace-panel]");
  if (!panel) return;
  const rows = panel.querySelectorAll(".evidence-card, .transcript-entry");
  let visible = 0;
  rows.forEach((row) => {
    const kind = row.querySelector(".kind-label")?.textContent.trim().toLowerCase() || "";
    const matchesText = !state.filter || row.textContent.toLowerCase().includes(state.filter);
    const matchesKind = state.reviewKind === "all" || kind === state.reviewKind;
    const matches = matchesText && matchesKind;
    row.classList.toggle("demo-hidden", !matches);
    if (matches) visible += 1;
  });
  let empty = panel.querySelector("[data-demo-filter-empty]");
  if (!visible && rows.length) {
    empty ||= document.createElement("p");
    empty.dataset.demoFilterEmpty = "";
    empty.className = "supporting";
    empty.textContent = "No mock records match this filter.";
    panel.querySelector(".review-list, .transcript-list")?.after(empty);
  } else empty?.remove();
}

function formatTime(seconds) {
  const bounded = Math.max(0, Math.min(durationSeconds, seconds));
  const minutes = Math.floor(bounded / 60);
  const remainder = bounded - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${remainder.toFixed(3).padStart(6, "0")}`;
}

function selectTime(seconds) {
  state.currentSeconds = Math.max(0, Math.min(durationSeconds, Number(seconds) || 0));
  app.querySelectorAll("[data-clock], [data-clock-physical]").forEach((clock) => {
    clock.textContent = formatTime(state.currentSeconds);
  });
  const bar = Math.min(21, 18 + Math.floor(state.currentSeconds / 7.5));
  app.querySelectorAll("[data-clock-musical]").forEach((clock) => {
    clock.textContent = `bars 18–21 · bar ${bar}`;
  });
  app.querySelectorAll("[data-playhead]").forEach((playhead) => {
    playhead.style.left = `${state.currentSeconds / durationSeconds * 100}%`;
  });
}

function togglePlayback() {
  state.isPlaying = !state.isPlaying;
  app.querySelectorAll("[data-play-icon]").forEach((label) => {
    label.textContent = state.isPlaying ? "Pause" : "Play";
  });
}

function tickClock() {
  if (!state.isPlaying) return;
  const next = state.currentSeconds + 0.25;
  if (next >= durationSeconds) {
    selectTime(durationSeconds);
    togglePlayback();
    return;
  }
  selectTime(next);
}

function openRevision(id) {
  const card = app.querySelector(`[data-review-card="${CSS.escape(id)}"]`);
  if (!card || !state.queue.includes(id)) return;
  const dialog = document.createElement("dialog");
  dialog.className = "demo-revision-dialog";
  dialog.dataset.demoRevisionDialog = "";
  const form = document.createElement("form");
  form.method = "dialog";
  form.dataset.demoRevisionForm = id;
  const heading = document.createElement("h2");
  heading.textContent = "Revise mock record";
  const hint = document.createElement("p");
  hint.className = "supporting";
  hint.textContent = "This revision and its decision remain only in the current browser session.";
  const field = document.createElement("textarea");
  field.name = "text";
  field.required = true;
  field.value = card.querySelector(".claim")?.textContent.trim() || "";
  const actions = document.createElement("div");
  actions.className = "dialog-actions";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.dataset.demoCloseRevision = "";
  cancel.textContent = "Cancel";
  const save = document.createElement("button");
  save.type = "submit";
  save.className = "primary-button";
  save.textContent = "Revise and advance";
  actions.append(cancel, save);
  form.append(heading, hint, field, actions);
  dialog.append(form);
  app.append(dialog);
  dialog.showModal();
  field.focus();
}

function applyMockDecision(id, action, revisedText = "") {
  const position = state.queue.indexOf(id);
  if (position < 0) return;
  const card = app.querySelector(`[data-review-card="${CSS.escape(id)}"]`);
  const original = card?.querySelector(".claim")?.textContent.trim() || "Mock record";
  const text = revisedText || original;
  state.audit.push({ number: state.audit.length + 1, action, text });
  state.queue.splice(position, 1);
  card?.remove();
  state.activeId = state.queue[position] || state.queue[position - 1] || "";
  if (!state.queue.length) {
    showEmptyQueue();
    showNotice(`${action} recorded. The mock queue is empty.`, "success");
    return;
  }
  updateQueueCounts();
  selectReview(state.activeId, true);
  filterCurrentPanel();
  showNotice(`${action} recorded. The next mock record is selected.`, "success");
}

app.addEventListener("click", (event) => {
  const dismiss = event.target.closest("[data-demo-dismiss]");
  if (dismiss) {
    dismiss.closest(".notice")?.remove();
    return;
  }
  const close = event.target.closest("[data-demo-close-revision]");
  if (close) {
    close.closest("dialog")?.close();
    close.closest("dialog")?.remove();
    return;
  }
  const tab = event.target.closest("[data-tab]");
  if (tab) {
    selectPanel(tab.dataset.tab, tab.getAttribute("role") === "tab");
    return;
  }
  const seek = event.target.closest("[data-seek]");
  if (seek) {
    selectTime(seek.dataset.seek);
    return;
  }
  const row = event.target.closest("[data-select-review]");
  if (row) {
    selectReview(row.dataset.selectReview, true);
    return;
  }
  const accept = event.target.closest("[data-accept]");
  if (accept) {
    applyMockDecision(accept.dataset.accept, "Accepted");
    return;
  }
  const reject = event.target.closest("[data-reject]");
  if (reject) {
    applyMockDecision(reject.dataset.reject, "Rejected");
    return;
  }
  const revise = event.target.closest("[data-revise]");
  if (revise) {
    openRevision(revise.dataset.revise);
    return;
  }
  const action = event.target.closest("[data-action]");
  if (!action || action.disabled) return;
  if (action.dataset.action === "toggle-theme") {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    action.setAttribute("aria-pressed", String(next === "dark"));
  } else if (action.dataset.action === "play") togglePlayback();
  else if (action.dataset.action === "seek-back") selectTime(state.currentSeconds - 5);
  else if (action.dataset.action === "seek-forward") selectTime(state.currentSeconds + 5);
});

app.addEventListener("submit", (event) => {
  const form = event.target.closest("[data-demo-revision-form]");
  if (!form) return;
  event.preventDefault();
  const text = new FormData(form).get("text")?.toString().trim();
  if (!text) return;
  const dialog = form.closest("dialog");
  const id = form.dataset.demoRevisionForm;
  dialog?.close();
  dialog?.remove();
  applyMockDecision(id, "Revised and accepted", text);
});

app.addEventListener("input", (event) => {
  if (!event.target.matches("[data-query]")) return;
  state.filter = event.target.value.trim().toLowerCase();
  filterCurrentPanel();
});

app.addEventListener("change", (event) => {
  if (event.target.matches("[data-review-kind]")) {
    state.reviewKind = event.target.value;
    filterCurrentPanel();
  }
  if (event.target.matches("[data-lane-kind]")) {
    const lane = app.querySelector(`[data-lane="${CSS.escape(event.target.dataset.laneKind)}"]`);
    lane?.classList.toggle("demo-hidden", !event.target.checked);
  }
});

app.addEventListener("keydown", (event) => {
  const tab = event.target.closest('[role="tab"]');
  if (tab && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
    event.preventDefault();
    const tabs = [...app.querySelectorAll('[role="tab"]')];
    const index = tabs.indexOf(tab);
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
      : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    selectPanel(tabs[next]?.dataset.tab, true);
    return;
  }
  if (event.key === "Escape") {
    document.querySelector("[data-demo-revision-dialog]")?.close();
    document.querySelector("[data-demo-revision-dialog]")?.remove();
    return;
  }
  if (event.metaKey || event.ctrlKey || event.altKey
    || event.target.matches("input, textarea, select, button, [contenteditable=true]")) return;
  const key = event.key.toLocaleLowerCase();
  if (state.activePanel === "review" && state.queue.length) {
    if (["arrowdown", "j", "arrowup", "k"].includes(key)) {
      event.preventDefault();
      const current = Math.max(0, state.queue.indexOf(state.activeId));
      const offset = key === "arrowdown" || key === "j" ? 1 : -1;
      selectReview(state.queue[(current + offset + state.queue.length) % state.queue.length], true);
      return;
    }
    if (key === "a") { event.preventDefault(); applyMockDecision(state.activeId, "Accepted"); return; }
    if (key === "x") { event.preventDefault(); applyMockDecision(state.activeId, "Rejected"); return; }
    if (key === "r") { event.preventDefault(); openRevision(state.activeId); return; }
  }
  if (event.key === " ") {
    event.preventDefault();
    togglePlayback();
  }
});

disableUnavailableControls();
const sessionNote = document.querySelector(".demo-session-note");
if (sessionNote) {
  const mobileNote = sessionNote.cloneNode(true);
  mobileNote.classList.add("demo-session-note-mobile");
  mobileNote.querySelector("strong").textContent = "Mock data";
  mobileNote.querySelector("span").textContent = "resets on reload";
  app.querySelector(".app-header")?.append(mobileNote);
  const source = app.querySelector(".source-section");
  source?.append(sessionNote);
  if (source) {
    const tourLink = document.createElement("a");
    tourLink.className = "demo-tour-link";
    tourLink.href = "./tour.html";
    tourLink.textContent = "Screenshot tour";
    source.append(tourLink);
  }
}
selectReview(state.activeId);
selectTime(state.currentSeconds);
window.setInterval(tickClock, 250);
