/** Compatibility surface for workbench UI helpers. */
export * from "/assets/ui/value_utils.mjs";
export * from "/assets/ui/filter_utils.mjs";
export * from "/assets/ui/render_utils.mjs";
export * from "/assets/ui/timeline_utils.mjs";

import { escapeHTML } from "/assets/ui/value_utils.mjs";
import { formatTime, hasMusicalTime, musicalTimeAt } from "/assets/ui/timeline_utils.mjs";

export function renderDualClocks(state, seconds = 0) {
  if (!hasMusicalTime(state)) return "";
  const musical = musicalTimeAt(state, seconds) || "unmetered";
  return `<div class="dual-clocks clocks" data-dual-clocks aria-label="Physical and musical time">
    <span class="clock-physical">Physical <b data-clock-physical>${formatTime(seconds)}</b></span>
    <span class="clock-musical">Musical <b data-clock-musical>${escapeHTML(musical)}</b></span>
  </div>`;
}
