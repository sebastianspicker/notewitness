/** Client-only walkthrough behavior. No API, media, microphone, or persistence calls. */

const app = document.querySelector("#app");
const demoState = { accepted: false, currentSeconds: 5 };
const commandSelectors = [
  "[data-action]",
  "[data-accept]",
  "[data-revise]",
  "[data-edit]",
  "[data-accept-relation]",
  "[data-reject-relation]",
  "[data-practice]",
  "[data-cancel-job]",
  "[data-retry-job]",
];

function markSimulatedCommands(root = app) {
  root.querySelectorAll(commandSelectors.join(",")).forEach((control) => {
    if (control.matches("input")) {
      const label = control.closest("label");
      if (label && !label.querySelector(".demo-action-label")) {
        label.insertAdjacentHTML("beforeend", '<span class="demo-action-label">Simulated</span>');
      }
      return;
    }
    if (control.matches(".record-button")) {
      const group = control.closest(".record-group")?.querySelector("div");
      if (group && !group.querySelector(".demo-action-label")) {
        group.insertAdjacentHTML("beforeend", '<span class="demo-action-label">Simulated</span>');
      }
      return;
    }
    control.dataset.demoCommand = "simulated";
    control.setAttribute("title", `${control.getAttribute("title") || control.textContent.trim() || "Action"} — simulated in this static demo`);
  });
  root.querySelectorAll("[data-import-file]").forEach((input) => {
    input.disabled = true;
    const label = input.closest("label");
    if (label && !label.querySelector(".demo-action-label")) {
      label.querySelector("span")?.insertAdjacentHTML("beforeend", '<span class="demo-action-label">Simulated</span>');
    }
  });
  const source = root.querySelector(".source-section");
  if (source && !source.querySelector(".demo-command-help")) {
    source.insertAdjacentHTML("beforeend", '<p class="demo-command-help"><strong>Static walkthrough.</strong> Navigation changes this page only. Marked actions never run commands, access devices, upload, save, or export.</p>');
  }
}

function showNotice(message, kind = "info") {
  const region = app.querySelector("[data-notice-region]");
  if (!region) return;
  region.innerHTML = `<div class="notice" data-kind="${kind}" role="status"><span>${message}</span><button type="button" data-demo-dismiss>Dismiss</button></div>`;
}

function selectPanel(name, focus = false) {
  const template = document.querySelector(`template[data-demo-panel="${name}"]`);
  const target = app.querySelector("[data-workspace-panel]");
  if (!template || !target) return;
  target.replaceChildren(template.content.cloneNode(true));
  if (name === "review" && demoState.accepted) applyAcceptedState();
  app.querySelectorAll("[data-tab]").forEach((tab) => {
    const selected = tab.dataset.tab === name;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  markSimulatedCommands(target);
  if (focus) app.querySelector(`[data-tab="${name}"]`)?.focus();
}

function applyAcceptedState() {
  app.querySelector("[data-review-card]")?.remove();
  const count = app.querySelector('#tab-review .n');
  if (count) count.textContent = "0";
  const list = app.querySelector(".review-list");
  if (list && !list.children.length) {
    list.innerHTML = '<li class="empty-state"><strong>Simulation complete</strong><p>The suggestion was removed from this browser-only queue. No evidence was saved.</p></li>';
  }
  const kicker = app.querySelector("#panel-review .panel-header .kicker");
  if (kicker) kicker.textContent = "0 items";
}

function filterCurrentPanel() {
  const panel = app.querySelector("[data-workspace-panel]");
  const query = panel?.querySelector("[data-query]")?.value.trim().toLowerCase() || "";
  const kind = panel?.querySelector("[data-review-kind]")?.value || "all";
  const rows = panel?.querySelectorAll(".evidence-card, .transcript-entry") || [];
  rows.forEach((row) => {
    const kindText = row.querySelector(".kind-label")?.textContent.trim().toLowerCase() || "";
    const matchesQuery = !query || row.textContent.toLowerCase().includes(query);
    const matchesKind = kind === "all" || kindText === kind;
    row.classList.toggle("demo-hidden", !(matchesQuery && matchesKind));
  });
}

function selectTime(seconds) {
  demoState.currentSeconds = Math.max(0, Math.min(30, Number(seconds) || 0));
  const minutes = Math.floor(demoState.currentSeconds / 60);
  const remainder = demoState.currentSeconds - minutes * 60;
  const time = `${String(minutes).padStart(2, "0")}:${remainder.toFixed(3).padStart(6, "0")}`;
  app.querySelectorAll("[data-clock]").forEach((clock) => { clock.textContent = time; });
  app.querySelectorAll("[data-playhead]").forEach((playhead) => {
    playhead.style.left = `${demoState.currentSeconds / 30 * 100}%`;
  });
}

function simulateCommand(control) {
  if (control.matches("[data-accept]")) {
    demoState.accepted = true;
    applyAcceptedState();
    showNotice("Simulated acceptance only. No project record was created or changed.", "success");
    return;
  }
  if (control.matches("[data-practice]")) {
    showNotice("Simulated practice-state change only. Nothing was saved.");
    return;
  }
  if (control.dataset.action === "play") {
    const label = control.querySelector("[data-play-icon]");
    if (label) label.textContent = label.textContent === "Play" ? "Pause" : "Play";
    showNotice("Simulated transport only. The synthetic fixture contains no playable media.");
    return;
  }
  if (control.dataset.action === "seek-back" || control.dataset.action === "seek-forward") {
    selectTime(demoState.currentSeconds + (control.dataset.action === "seek-back" ? -5 : 5));
    return;
  }
  showNotice("Simulated action only. The published demo cannot run commands, access devices, save, upload, or export.");
}

app.addEventListener("click", (event) => {
  const dismiss = event.target.closest("[data-demo-dismiss]");
  if (dismiss) {
    dismiss.closest(".notice")?.remove();
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
  const command = event.target.closest(commandSelectors.join(","));
  if (command) {
    event.preventDefault();
    simulateCommand(command);
  }
});

app.addEventListener("input", (event) => {
  if (event.target.matches("[data-query]")) filterCurrentPanel();
});

app.addEventListener("change", (event) => {
  if (event.target.matches("[data-review-kind]")) filterCurrentPanel();
  if (event.target.matches("[data-lane-kind]")) {
    const lane = app.querySelector(`[data-lane="${CSS.escape(event.target.dataset.laneKind)}"]`);
    lane?.classList.toggle("demo-hidden", !event.target.checked);
  }
  if (event.target.matches("[data-practice]")) simulateCommand(event.target);
});

app.addEventListener("keydown", (event) => {
  const current = event.target.closest('[role="tab"]');
  if (!current || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const tabs = [...app.querySelectorAll('[role="tab"]')];
  const index = tabs.indexOf(current);
  const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
    : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
  selectPanel(tabs[nextIndex].dataset.tab, true);
});

markSimulatedCommands();
selectTime(demoState.currentSeconds);
