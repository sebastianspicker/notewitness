import {
  escapeHTML,
  formatTime,
  itemDuration,
  itemSource,
  itemTime,
  list,
  renderDualClocks,
  sourceDurationSeconds,
  timelineTicks,
} from "/assets/ui/utils.mjs";

export function renderTimeline(state) {
  const sourceId = state.activeSourceId;
  const duration = sourceDurationSeconds(state, sourceId);
  const ticks = timelineTicks(duration);
  const visible = state.visibleLaneKinds;
  const lanes = list(state.data?.timeline?.lanes).filter((lane) => {
    return visible.has(lane.kind) && list(lane.items).some((item) => itemSource(item) === sourceId);
  });
  const allKinds = list(state.data?.timeline?.lanes).map((lane) => [lane.kind, lane.label]);
  const currentSeconds = Number(state.media?.currentTime || 0);
  return `<section class="timeline-section" aria-labelledby="timeline-heading">
    <div class="timeline-header score-head">
      <div>
        <h2 id="timeline-heading">Timeline</h2>
      </div>
      ${renderDualClocks(state, currentSeconds)}
      <div class="timeline-actions">
        <details class="lane-picker"><summary>Tracks · ${visible.size}</summary>
          <fieldset><legend>Timeline tracks</legend>${allKinds.map(([kind, label]) => `<label>
            <input type="checkbox" data-lane-kind="${escapeHTML(kind)}" ${visible.has(kind) ? "checked" : ""}>
            ${escapeHTML(label)}</label>`).join("")}</fieldset></details>
      </div>
    </div>
    <div class="timeline-scroll" tabindex="0" aria-label="Scrollable source timeline">
      <div class="timeline-canvas lanes">
        <div class="time-ruler lane lane-h">
          <span class="ruler-label lane-name">Track</span>
          <div class="tick-scale track ticks" data-tick-scale>
            ${ticks.map((tick) => `<span style="left:${Math.min(100, tick / duration * 100)}%">${formatTime(tick, false)}</span>`).join("")}
          </div>
        </div>
        ${lanes.length ? lanes.map((lane) => renderLane(state, lane, duration)).join("")
          : `<div class="timeline-empty"><strong>No evidence on visible tracks</strong><p>Choose more tracks or run local processing for this source.</p></div>`}
      </div>
    </div>
  </section>`;
}

function renderLane(state, lane, duration) {
  const items = list(lane.items).filter((item) => itemSource(item) === state.activeSourceId);
  return `<div class="timeline-lane lane" data-lane="${escapeHTML(lane.kind)}">
    <div class="lane-label lane-name"><kbd>${escapeHTML(lane.keyboard_shortcut, "")}</kbd>
      <span>${escapeHTML(lane.label)}</span></div>
    <div class="lane-track track">${items.map((item) => {
      const left = Math.max(0, Math.min(100, itemTime(item) / duration * 100));
      const width = Math.max(0.8, Math.min(100 - left, itemDuration(item) / duration * 100));
      return `<button class="lane-item event" style="left:${left}%;width:${width}%"
        data-seek="${itemTime(item)}" data-source="${escapeHTML(itemSource(item))}"
        data-kind="${escapeHTML(lane.kind)}" data-status="${escapeHTML(item.review_status)}"
        title="${escapeHTML(item.label)}" aria-label="Play at ${formatTime(itemTime(item))}: ${escapeHTML(item.accessibility_label || item.label)}">
        ${escapeHTML(item.label)}</button>`;
    }).join("")}<i class="playhead" style="left:0" data-playhead aria-hidden="true"></i></div>
  </div>`;
}
