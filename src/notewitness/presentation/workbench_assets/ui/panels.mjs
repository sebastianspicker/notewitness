import {
  encodeId,
  escapeHTML,
  formatTime,
  humanActor,
  itemDuration,
  itemSource,
  itemTime,
  list,
  renderActorAttributionOptions,
  renderConfidence,
  renderPairs,
  reviewItems,
  transcriptItems,
  anchor,
} from "/assets/ui/utils.mjs";

export function renderPanel(state) {
  if (state.activePanel === "transcript") return renderTranscriptPanel(state);
  if (state.activePanel === "lesson") return renderLessonPanel(state);
  return renderReviewPanel(state);
}

function renderPanelHeader(state, title, description, count) {
  return `<div class="panel-header"><div><p class="kicker">${count} ${count === 1 ? "item" : "items"}</p>
    <h2>${escapeHTML(title)}</h2><p>${escapeHTML(description)}</p></div>
    <label class="search-field"><span>Search this view</span>
      <input type="search" data-query value="${escapeHTML(state.query, "")}" placeholder="Search evidence…"></label></div>`;
}

function renderReviewPanel(state) {
  const items = reviewItems(state);
  const all = list(state.data?.lesson?.transcript_suggestions).filter((item) => {
    return !state.activeSourceId || itemSource(item) === state.activeSourceId;
  });
  const kinds = [...new Set(all.map((item) => String(item.content_kind || "other")))].sort();
  return `<section class="content-panel review" id="panel-review" role="tabpanel" aria-labelledby="tab-review">
    ${renderPanelHeader(state, "Review queue", "Verify machine suggestions before they become accepted research evidence.", items.length)}
    <div class="filter-row"><label>Evidence type <select data-review-kind>
      <option value="all">All evidence</option>${kinds.map((kind) => `<option value="${escapeHTML(kind)}" ${state.reviewKind === kind ? "selected" : ""}>${escapeHTML(kind.replaceAll("_", " "))}</option>`).join("")}
    </select></label><p class="supporting">Suggested items require a named human decision before acceptance.</p></div>
    <ol class="evidence-list review-list">${items.length ? items.map((item, index) => renderReviewItem(state, item, index)).join("")
      : `<li class="empty-state"><strong>Nothing matches this review view</strong><p>${all.length ? "Clear the search or evidence filter." : "No machine suggestions are waiting for this source."}</p></li>`}</ol>
  </section>`;
}

function renderReviewItem(state, item, index) {
  const actor = humanActor(state);
  const eventId = encodeId(item.event_id);
  const canRevise = typeof item.body_value === "string";
  const start = itemTime(item);
  const end = start + itemDuration(item);
  const status = String(item.review_status || "machine_suggested");
  const suggested = status === "machine_suggested" || status.includes("suggest");
  return `<li class="evidence-card evidence" data-review-card="${eventId}" tabindex="-1">
    <div class="status-line">
      <span class="tag ${suggested ? "suggested" : "accepted"}">${suggested ? "Suggested" : escapeHTML(status.replaceAll("_", " "))}</span>
      <span class="dot">·</span>
      <span class="kind-label">${escapeHTML(String(item.content_kind || "evidence").replaceAll("_", " "))}</span>
      <span class="dot">·</span>
      <button class="time-button play-link" data-seek="${start}" data-source="${escapeHTML(itemSource(item))}">
        ${formatTime(start, false)} – ${formatTime(end, false)}</button>
      <span class="dot">·</span>
      <span>${escapeHTML(item.actor_role, "unattributed")}</span>
      <span class="dot">·</span>
      <span>${renderConfidence(item.confidence)}</span>
    </div>
    <blockquote class="claim">${escapeHTML(item.display_text)}</blockquote>
    <div class="review-controls adjudicate"><label class="field">Attribute to
      <select class="attribution" data-attribution="${eventId}">
        ${renderActorAttributionOptions(state, item.actor_id, true)}
      </select></label><div class="row-actions actions">
        ${canRevise ? `<button class="secondary-button" data-revise="${eventId}" ${actor ? "" : "disabled"}>Revise</button>` : ""}
        <button class="primary-button" data-accept="${eventId}" data-busy-key="review-${eventId}" ${actor ? "" : "disabled"}>Accept${index < reviewItems(state).length - 1 ? " & next" : ""}</button>
      </div></div>
  </li>`;
}

function renderTranscriptPanel(state) {
  const items = transcriptItems(state);
  return `<section class="content-panel" id="panel-transcript" role="tabpanel" aria-labelledby="tab-transcript">
    ${renderPanelHeader(state, "Full transcript", "Speech, notes, pitch, music, silence, and overlap remain on one chronological evidence record.", items.length)}
    <ol class="transcript-list">${items.length ? items.map((item) => renderTranscriptItem(state, item)).join("")
      : '<li class="empty-state"><strong>No accepted evidence matches</strong><p>Run processing, review suggestions, or clear the search.</p></li>'}</ol>
  </section>`;
}

function renderTranscriptItem(state, item) {
  const editable = humanActor(state) && typeof item.body_value === "string";
  const eventId = encodeId(item.event_id);
  return `<li class="transcript-entry">
    <button class="time-button" data-seek="${itemTime(item)}" data-source="${escapeHTML(itemSource(item))}">${formatTime(itemTime(item))}</button>
    <div><div class="evidence-meta"><span class="kind-label accepted">${escapeHTML(String(item.content_kind || "evidence").replaceAll("_", " "))}</span>
      <span>${escapeHTML(item.actor_role, "unattributed")}</span><span>${escapeHTML(item.review_status, "accepted")}</span></div>
      <p class="claim">${escapeHTML(item.display_text)}</p></div>
    ${editable ? `<label class="compact-attribution">Attributed actor<select data-attribution="${eventId}">
      ${renderActorAttributionOptions(state, item.actor_id, false)}</select></label>
      <button class="secondary-button" data-edit="${eventId}">Edit</button>` : ""}
  </li>`;
}

function renderLessonPanel(state) {
  const lesson = state.data?.lesson || {};
  const summary = lesson.summary || {};
  const bookmarks = list(lesson.bookmarks);
  const tasks = list(lesson.practice_plan?.tasks);
  return `<section class="content-panel lesson-panel" id="panel-lesson" role="tabpanel" aria-labelledby="tab-lesson">
    <div class="panel-header"><div><p class="kicker">Evidence-backed projection</p><h2>Lesson notes</h2>
      <p>${escapeHTML(summary.overview, "No lesson summary has been projected yet.")}</p></div></div>
    <div class="lesson-grid">
      ${renderMoments(summary.key_moments)}
      ${renderRelationSuggestions(state, lesson.relation_suggestions)}
      ${renderFeedback(summary.feedback)}
      ${renderPractice(state, tasks)}
      ${renderBookmarks(state, bookmarks)}
      ${renderStatistics(lesson.statistics)}
      ${renderProvenance(lesson)}
      <section class="lesson-section full-width"><p class="kicker">Interpretation limits</p><h3>Read before reuse</h3>
        <ul class="limitations">${list(lesson.limitations).map((item) => `<li>${escapeHTML(item)}</li>`).join("") || "<li>No limitations recorded.</li>"}</ul></section>
    </div>
  </section>`;
}

function renderRelationSuggestions(state, suggestions) {
  const actor = humanActor(state);
  const items = list(suggestions).filter((item) => {
    return item?.relation_type === "local:assigned_for_practice";
  });
  return `<section class="lesson-section full-width"><p class="kicker">Human review required</p><h3>Pedagogical suggestions</h3>
    <p class="supporting">These local rule-based links repeat transcript evidence; they do not create a summary or assessment.</p>
    <ol class="moment-list">${items.map((item) => {
      const point = anchor(item);
      const relationId = encodeId(item.relation_id);
      return `<li><button class="time-button" data-seek="${itemTime(point)}" data-source="${escapeHTML(itemSource(point))}">${formatTime(itemTime(point), false)}</button>
        <div><strong>${escapeHTML(String(item.relation_type || "relation").replaceAll("_", " "))}</strong><p>${escapeHTML(item.label)}</p>
          <small>Accept the linked transcript evidence first; relation text cannot be edited.</small></div>
        <div class="row-actions"><button class="secondary-button" data-reject-relation="${relationId}" ${actor ? "" : "disabled"}>Reject</button>
          <button class="primary-button" data-accept-relation="${relationId}" ${actor ? "" : "disabled"}>Accept relation</button></div></li>`;
    }).join("") || '<li class="empty-state"><strong>No pedagogical suggestions</strong><p>Explicit local transcript instructions will appear here for review.</p></li>'}</ol></section>`;
}

function renderMoments(moments) {
  const items = list(moments);
  return `<section class="lesson-section"><p class="kicker">Teaching sequence</p><h3>Key moments</h3>
    <ol class="moment-list">${items.slice(0, 12).map((item) => {
      const point = anchor(item);
      return `<li><button class="time-button" data-seek="${itemTime(point)}" data-source="${escapeHTML(itemSource(point))}">${formatTime(itemTime(point), false)}</button>
        <div><strong>${escapeHTML(String(item.relation_type || "moment").replaceAll("_", " "))}</strong><p>${escapeHTML(item.label)}</p></div></li>`;
    }).join("") || '<li class="empty-state"><strong>No key moments yet</strong><p>Reviewed relations will appear here.</p></li>'}</ol></section>`;
}

function renderFeedback(feedback) {
  const items = list(feedback);
  return `<section class="lesson-section"><p class="kicker">Teacher evidence</p><h3>Feedback</h3>
    <ul class="feedback-list">${items.map((item) => `<li><button class="time-button" data-seek="${itemTime(item)}" data-source="${escapeHTML(itemSource(item))}">${formatTime(itemTime(item), false)}</button>
      <div><p>${escapeHTML(item.text)}</p><span>${escapeHTML(item.actor_role)} · ${escapeHTML(item.review_status)}</span></div></li>`).join("")
      || '<li class="empty-state"><strong>No feedback identified</strong><p>Reviewed teacher feedback will appear here.</p></li>'}</ul></section>`;
}

function renderPractice(state, tasks) {
  const actor = humanActor(state);
  return `<section class="lesson-section"><p class="kicker">Next session</p><h3>Practice plan</h3>
    <ul class="practice-list">${tasks.map((item) => `<li><label><input type="checkbox" data-practice="${encodeId(item.task_id)}"
      ${item.completed ? "checked" : ""} ${actor ? "" : "disabled"}><span>${escapeHTML(item.text)}</span></label>
      <small>${escapeHTML(item.review_status)}</small></li>`).join("") || '<li class="empty-state"><strong>No practice task yet</strong><p>Only evidence-backed assignments are shown.</p></li>'}</ul></section>`;
}

function renderBookmarks(state, bookmarks) {
  return `<section class="lesson-section"><div class="section-heading"><div><p class="kicker">Exact-time recall</p><h3>Bookmarks</h3></div>
    <button class="secondary-button" data-action="open-bookmark" ${humanActor(state) && state.activeSourceId ? "" : "disabled"}>Add</button></div>
    <ul class="bookmark-list">${bookmarks.map((item) => `<li><button class="time-button" data-seek="${itemTime(item)}" data-source="${escapeHTML(itemSource(item))}">${formatTime(itemTime(item))}</button>
      <span>${escapeHTML(item.label)}</span></li>`).join("") || '<li class="empty-state"><strong>No bookmarks</strong><p>Mark an exact moment while listening.</p></li>'}</ul></section>`;
}

function renderStatistics(statistics = {}) {
  const pairs = Object.entries(statistics).filter(([, value]) => typeof value !== "object");
  return `<section class="lesson-section"><p class="kicker">Descriptive only</p><h3>Lesson statistics</h3>
    <dl class="definition-grid">${renderPairs(pairs) || "<dt>State</dt><dd>Awaiting evidence</dd>"}</dl></section>`;
}

function renderProvenance(lesson) {
  const graph = lesson.source_graph || {};
  const pairs = Object.entries(graph).filter(([, value]) => typeof value !== "object");
  return `<section class="lesson-section"><p class="kicker">Research trace</p><h3>Provenance</h3>
    <dl class="definition-grid">${renderPairs(pairs) || "<dt>Mode</dt><dd>Local only</dd>"}</dl></section>`;
}
