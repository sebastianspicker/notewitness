import { encodeId } from "/assets/ui/value_utils.mjs";
import { renderContextInspector } from "/assets/ui/context.mjs";
import { renderNotice, renderWorkbench } from "/assets/ui/shell.mjs";
import { renderPanel } from "/assets/ui/panels.mjs";
import { renderProcessing } from "/assets/ui/processing.mjs";
import { renderTimeline } from "/assets/ui/timeline.mjs";
import { reviewItems } from "/assets/ui/filter_utils.mjs";

export function createRendering(c) {
  const { app, state } = c;
  function replaceMarkup(target, markup) { const body = new DOMParser().parseFromString(markup, "text/html").body; target.replaceChildren(...body.childNodes); }
  function paragraph(text) { const element = document.createElement("p"); element.textContent = text; return element; }
  function renderFatalState(error) { const main = document.createElement("main"); main.className = "fatal-state"; main.setAttribute("role", "alert"); const kicker = document.createElement("p"); kicker.className = "kicker"; const title = document.createElement("h1"); if (error?.status === 401) { kicker.textContent = "No workbench session in this tab"; title.textContent = "Open the workbench from its launch link"; main.append(kicker, title, paragraph("Each launch link opens the workbench once, in one browser tab. This tab was opened without it, or the link was already used, so reloading here cannot help."), paragraph("Switch back to the tab the launch link opened. If that tab is closed, press Enter in the terminal running the workbench to print a new launch link, or stop the workbench there with Ctrl+C and start it again. Your saved project is not affected.")); app.replaceChildren(main); return; } kicker.textContent = "Local project unavailable"; title.textContent = "The workbench could not open"; const detail = paragraph(error?.message || "An unknown local error occurred."); const retry = document.createElement("button"); retry.className = "primary-button"; retry.dataset.action = "reload"; retry.textContent = "Try again"; main.append(kicker, title, detail, retry); app.replaceChildren(main); }
  function setNotice(message, kind = "info") { state.notice = message ? { message, kind } : null; const region = app.querySelector("[data-notice-region]"); if (region) replaceMarkup(region, renderNotice(state.notice)); }
  function setBusy(key, busy) { if (busy) state.busy.add(key); else state.busy.delete(key); app.querySelectorAll(`[data-busy-key="${CSS.escape(key)}"]`).forEach((button) => { button.disabled = busy; button.setAttribute("aria-busy", String(busy)); }); }
  async function runMutation(key, activity, operation) { if (state.busy.has(key)) return false; setBusy(key, true); setNotice(`${activity}…`, "info"); try { await operation(); return true; } catch (error) { if (error.status === 409 && error.message.endsWith("project_changed")) { await c.load({ preservePlayback: true, quiet: true }).catch(() => undefined); setNotice("The project changed in another action. The latest local version is shown; please try again.", "error"); } else setNotice(`${activity} could not be completed: ${error.message}`, "error"); return false; } finally { setBusy(key, false); } }
  function render(restore = null) { replaceMarkup(app, renderWorkbench(state)); c.bindMedia(restore); c.openRenderedDialog(); if (state.pendingFocus) { const target = app.querySelector(`[data-review-card="${CSS.escape(state.pendingFocus)}"]`); state.pendingFocus = ""; target?.focus(); } }
  function renderPreservingPlayback() { render(c.snapshotPlayback()); }
  function refreshPanel() { const panel = app.querySelector("[data-workspace-panel]"); if (panel) replaceMarkup(panel, renderPanel(state)); app.querySelectorAll("[data-tab]").forEach((tab) => { const selected = tab.dataset.tab === state.activePanel; if (tab.getAttribute("role") === "tab") { tab.setAttribute("aria-selected", String(selected)); tab.tabIndex = selected ? 0 : -1; } }); }
  function syncReviewSelection() { const items = reviewItems(state); if (!items.some((item) => encodeId(item.event_id) === state.activeReviewId)) state.activeReviewId = encodeId(items[0]?.event_id || ""); }
  function refreshContextInspector() { const inspector = app.querySelector("[data-context-inspector]"); if (inspector) replaceMarkup(inspector, renderContextInspector(state)); }
  function refreshReviewSurface() { syncReviewSelection(); refreshPanel(); refreshContextInspector(); }
  function selectReview(eventId, focus = false) { if (!reviewItems(state).some((item) => encodeId(item.event_id) === eventId)) return; state.activeReviewId = eventId; refreshReviewSurface(); if (focus) app.querySelector(`[data-review-card="${CSS.escape(eventId)}"]`)?.focus(); }
  function refreshTimeline() { const root = app.querySelector("[data-timeline-root]"); if (root) replaceMarkup(root, renderTimeline(state)); c.syncPlayback(); }
  function refreshProcessing() { const section = app.querySelector(".processing-section"); if (!section) return; const body = new DOMParser().parseFromString(renderProcessing(state), "text/html").body; section.replaceWith(...body.childNodes); }
  function selectPanel(name, focus = false) { if (!["review", "transcript", "lesson"].includes(name)) return; state.activePanel = name; refreshPanel(); refreshContextInspector(); if (focus) app.querySelector(`[data-tab="${name}"][role="tab"]`)?.focus(); }
  return { renderFatalState, setNotice, setBusy, runMutation, render, renderPreservingPlayback, refreshPanel, refreshTimeline, refreshProcessing, refreshReviewSurface, selectReview, selectPanel };
}
