import {
  encodeId,
  escapeHTML,
  formatTime,
  humanActor,
  itemDuration,
  itemSource,
  itemTime,
  list,
  relationLabel,
  relationText,
  renderActorAttributionOptionsHTML,
  renderConfidence,
  renderPairs,
  reviewItems,
  selectedReviewItem,
  statusLabel,
  statusTone,
  transcriptItems,
  anchor,
} from "/assets/ui/utils.mjs";

export function renderPanel(state) {
  if (state.activePanel === "transcript") return renderTranscriptPanel(state);
  if (state.activePanel === "lesson") return renderLessonPanel(state);
  return renderReviewPanel(state);
}

function renderPanelHeader(title, description, count) {
  return `<div class="panel-header">
    <div class="panel-title"><h2>${escapeHTML(title)}</h2><p class="kicker">${count} ${count === 1 ? "item" : "items"}</p></div>
    <p class="panel-lede">${escapeHTML(description)}</p>
  </div>`;
}

function renderSearch(state) {
  return `<label class="search-field"><span>Search this view</span>
    <input type="search" data-query value="${escapeHTML(state.query, "")}" placeholder="Words, actors, kinds…"></label>`;
}

function renderSigil(tone) {
  return `<span class="sigil sigil-${tone}" aria-hidden="true"></span>`;
}

function renderReviewPanel(state) {
  const items = reviewItems(state);
  const selected = selectedReviewItem(state);
  const all = list(state.data?.lesson?.transcript_suggestions).filter((item) => {
    return !state.activeSourceId || itemSource(item) === state.activeSourceId;
  });
  const kinds = [...new Set(all.map((item) => String(item.content_kind || "other")))].sort();
  return `<section class="content-panel review" id="panel-review" role="tabpanel" aria-labelledby="tab-review">
    ${renderPanelHeader("Review queue", "Machine suggestions stay pencilled in until a named person accepts, revises, or rejects them.", items.length)}
    <div class="panel-tools filter-row">
      <label>Evidence type <select data-review-kind>
        <option value="all">All evidence</option>${kinds.map((kind) => `<option value="${escapeHTML(kind)}" ${state.reviewKind === kind ? "selected" : ""}>${escapeHTML(kind.replaceAll("_", " "))}</option>`).join("")}
      </select></label>
      ${renderSearch(state)}
    </div>
    <ol class="evidence-list review-list" aria-label="Machine suggestions waiting for review">${items.length ? items.map((item) => renderReviewItem(item, selected)).join("")
      : `<li class="empty-state queue-empty"><strong>${all.length ? "Nothing matches this view" : "Nothing left to review"}</strong><p>${all.length ? "Clear the search or the evidence type filter to see the rest of the queue." : "Every machine suggestion for this source has a recorded decision. New local processing results will appear here for review."}</p></li>`}</ol>
  </section>`;
}

function renderReviewItem(item, selected) {
  const eventId = encodeId(item.event_id);
  const start = itemTime(item);
  const end = start + itemDuration(item);
  const tone = statusTone(item.review_status);
  const active = selected?.event_id === item.event_id;
  return `<li class="evidence-card is-${tone} ${active ? "is-selected" : ""}" data-review-card="${eventId}" data-select-review="${eventId}" role="button" aria-current="${active ? "true" : "false"}" tabindex="0">
    <div class="entry-margin">
      <button class="time-button play-link" data-seek="${start}" data-source="${escapeHTML(itemSource(item))}" aria-label="Play from ${formatTime(start, false)} to ${formatTime(end, false)}">${formatTime(start, false)}</button>
      <span class="entry-end" aria-hidden="true">${formatTime(end, false)}</span>
    </div>
    <div class="entry-body">
      <blockquote class="claim">${escapeHTML(item.display_text)}</blockquote>
      <p class="status-line">
        <span class="tag ${tone}">${renderSigil(tone)}${tone === "suggested" ? "Suggested" : escapeHTML(statusLabel(item.review_status))}</span>
        <span class="kind-label">${escapeHTML(String(item.content_kind || "evidence").replaceAll("_", " "))}</span>
        <span>${escapeHTML(item.actor_role, "unattributed")}</span>
        <span>${renderConfidence(item.confidence)}</span>
      </p>
    </div>
  </li>`;
}

function renderTranscriptPanel(state) {
  const items = transcriptItems(state);
  return `<section class="content-panel" id="panel-transcript" role="tabpanel" aria-labelledby="tab-transcript">
    ${renderPanelHeader("Full transcript", "Accepted evidence in source order: speech, notes, pitch, music, silence, and overlap on one record.", items.length)}
    <div class="panel-tools">${renderSearch(state)}</div>
    <ol class="transcript-list">${items.length ? items.map((item) => renderTranscriptItem(state, item)).join("")
      : '<li class="empty-state"><strong>No accepted evidence yet</strong><p>Accept suggestions in the review queue, run local processing, or clear the search.</p></li>'}</ol>
  </section>`;
}

function renderTranscriptItem(state, item) {
  const editable = humanActor(state) && typeof item.body_value === "string";
  const eventId = encodeId(item.event_id);
  const tone = statusTone(item.review_status || "human_accepted");
  return `<li class="transcript-entry is-${tone}">
    <div class="entry-margin">
      <button class="time-button" data-seek="${itemTime(item)}" data-source="${escapeHTML(itemSource(item))}" aria-label="Play from ${formatTime(itemTime(item))}">${formatTime(itemTime(item))}</button>
    </div>
    <div class="entry-body">
      <p class="claim">${escapeHTML(item.display_text)}</p>
      <p class="evidence-meta status-line">
        <span class="tag ${tone}">${renderSigil(tone)}${escapeHTML(statusLabel(item.review_status || "human_accepted"))}</span>
        <span class="kind-label accepted">${escapeHTML(String(item.content_kind || "evidence").replaceAll("_", " "))}</span>
        <span>${escapeHTML(item.actor_role, "unattributed")}</span>
      </p>
    </div>
    ${editable ? `<div class="entry-actions">
      <label class="compact-attribution"><span>Attributed to</span><select data-attribution="${eventId}">
        ${renderActorAttributionOptionsHTML(state, item.actor_id, false)}</select></label>
      <button class="text-button" data-edit="${eventId}">Edit</button>
    </div>` : ""}
  </li>`;
}

function renderLessonPanel(state) {
  const lesson = state.data?.lesson || {};
  const summary = lesson.summary || {};
  const bookmarks = list(lesson.bookmarks);
  const tasks = list(lesson.practice_plan?.tasks);
  return `<section class="content-panel lesson-panel" id="panel-lesson" role="tabpanel" aria-labelledby="tab-lesson">
    <div class="panel-header">
      <div class="panel-title"><h2>Lesson notes</h2><p class="kicker">Projected from reviewed evidence</p></div>
      <p class="panel-lede">${escapeHTML(summary.overview, "No lesson summary has been projected yet. Notes appear once evidence has been reviewed.")}</p>
    </div>
    <div class="lesson-grid">
      ${renderMoments(summary.key_moments)}
      ${renderRelationSuggestions(state, lesson.relation_suggestions)}
      ${renderFeedback(summary.feedback)}
      ${renderPractice(state, tasks)}
      ${renderBookmarks(state, bookmarks)}
      <div class="lesson-pair">
        ${renderStatistics(lesson.statistics)}
        ${renderProvenance(lesson)}
      </div>
      <section class="lesson-section limits-section"><p class="kicker">Interpretation limits</p><h3>Read before reuse</h3>
        <ol class="limitations">${list(lesson.limitations).map((item) => `<li>${escapeHTML(item)}</li>`).join("") || "<li>No limitations recorded.</li>"}</ol></section>
    </div>
  </section>`;
}

function renderSectionHead(kicker, title) {
  return `<p class="kicker">${escapeHTML(kicker)}</p><h3>${escapeHTML(title)}</h3>`;
}

function renderTimeMargin(point, precise = false) {
  return `<div class="entry-margin"><button class="time-button" data-seek="${itemTime(point)}" data-source="${escapeHTML(itemSource(point))}">${formatTime(itemTime(point), precise)}</button></div>`;
}

function renderRelationSuggestions(state, suggestions) {
  const actor = humanActor(state);
  const items = list(suggestions).filter((item) => {
    return item?.relation_type === "local:assigned_for_practice";
  });
  return `<section class="lesson-section">${renderSectionHead("Human review required", "Pedagogical suggestions")}
    <p class="supporting section-note">Local rule-based links that repeat transcript evidence. They do not summarise or assess.</p>
    <ol class="moment-list">${items.map((item) => {
      const point = anchor(item);
      const relationId = encodeId(item.relation_id);
      return `<li class="is-suggested">${renderTimeMargin(point)}
        <div class="entry-body"><p class="moment-type"><span class="tag suggested">${renderSigil("suggested")}Suggested</span> ${escapeHTML(relationLabel(item.relation_type))}</p>
          <p class="moment-text">${escapeHTML(relationText(item.label, item.relation_type))}</p>
          <p class="supporting">Accept the linked transcript evidence first; relation text cannot be edited.</p>
          <div class="row-actions"><button class="secondary-button" data-accept-relation="${relationId}" ${actor ? "" : "disabled"}>Accept relation</button>
            <button class="text-button" data-reject-relation="${relationId}" ${actor ? "" : "disabled"}>Reject</button></div></div></li>`;
    }).join("") || '<li class="empty-state"><strong>No pedagogical suggestions</strong><p>Explicit practice instructions found in the transcript will appear here for review.</p></li>'}</ol></section>`;
}

function renderMoments(moments) {
  const items = list(moments);
  return `<section class="lesson-section">${renderSectionHead("Teaching sequence", "Key moments")}
    <ol class="moment-list">${items.slice(0, 12).map((item) => {
      const point = anchor(item);
      return `<li>${renderTimeMargin(point)}
        <div class="entry-body"><p class="moment-type">${escapeHTML(relationLabel(item.relation_type, "moment"))}</p><p class="moment-text">${escapeHTML(relationText(item.label, item.relation_type))}</p></div></li>`;
    }).join("") || '<li class="empty-state"><strong>No key moments yet</strong><p>Reviewed relations between evidence will appear here in teaching order.</p></li>'}</ol></section>`;
}

function renderFeedback(feedback) {
  const items = list(feedback);
  return `<section class="lesson-section">${renderSectionHead("Teacher evidence", "Feedback")}
    <ol class="feedback-list">${items.map((item) => `<li>${renderTimeMargin(item)}
      <div class="entry-body"><p class="moment-text">${escapeHTML(item.text)}</p><p class="status-line"><span>${escapeHTML(item.actor_role)}</span><span>${escapeHTML(statusLabel(item.review_status))}</span></p></div></li>`).join("")
      || '<li class="empty-state"><strong>No feedback identified</strong><p>Reviewed teacher feedback will appear here.</p></li>'}</ol></section>`;
}

function renderPractice(state, tasks) {
  const actor = humanActor(state);
  return `<section class="lesson-section">${renderSectionHead("Next session", "Practice plan")}
    <ul class="practice-list">${tasks.map((item) => `<li><label><input type="checkbox" data-practice="${encodeId(item.task_id)}"
      ${item.completed ? "checked" : ""} ${actor ? "" : "disabled"}><span>${escapeHTML(item.text)}</span></label>
      <small>${escapeHTML(statusLabel(item.review_status))}</small></li>`).join("") || '<li class="empty-state"><strong>No practice task yet</strong><p>Only assignments backed by reviewed evidence are listed.</p></li>'}</ul></section>`;
}

function renderBookmarks(state, bookmarks) {
  return `<section class="lesson-section"><div class="section-heading"><div>${renderSectionHead("Exact-time recall", "Bookmarks")}</div>
    <button class="secondary-button" data-action="open-bookmark" ${humanActor(state) && state.activeSourceId ? "" : "disabled"}>Add bookmark</button></div>
    <ul class="bookmark-list">${bookmarks.map((item) => `<li>${renderTimeMargin(item, true)}
      <div class="entry-body"><p class="moment-text">${escapeHTML(item.label)}</p></div></li>`).join("") || '<li class="empty-state"><strong>No bookmarks</strong><p>Mark an exact moment while listening; it is saved with its source time.</p></li>'}</ul></section>`;
}

function renderStatistics(statistics = {}) {
  const pairs = Object.entries(statistics).filter(([, value]) => typeof value !== "object");
  return `<section class="lesson-section">${renderSectionHead("Descriptive only", "Lesson statistics")}
    <dl class="definition-grid">${renderPairs(pairs) || "<dt>State</dt><dd>Awaiting evidence</dd>"}</dl></section>`;
}

function renderProvenance(lesson) {
  const graph = lesson.source_graph || {};
  const pairs = Object.entries(graph).filter(([, value]) => typeof value !== "object");
  return `<section class="lesson-section">${renderSectionHead("Research trace", "Provenance")}
    <dl class="definition-grid">${renderPairs(pairs) || "<dt>Mode</dt><dd>Local only</dd>"}</dl></section>`;
}
