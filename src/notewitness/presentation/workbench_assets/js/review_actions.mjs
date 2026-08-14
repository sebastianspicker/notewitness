import { encodeId, humanActor, list, reviewItems } from "/assets/workbench_ui.mjs";

export function createReviewActions(c) {
  function attributedActorId(eventId) {
    const selector = c.app.querySelector(`[data-attribution="${CSS.escape(eventId)}"]`);
    if (!selector?.value) {
      c.setNotice("Choose the project actor represented by this evidence before saving.", "error");
      selector?.focus();
      return "";
    }
    return selector.value;
  }

  function requireHumanActor() {
    const actor = humanActor(c.state);
    if (!actor) c.setNotice("Choose an eligible named human reviewer before saving evidence.", "error");
    return actor;
  }

  async function acceptSuggestion(eventId) {
    const author = requireHumanActor();
    if (!author) return;
    const actorId = attributedActorId(eventId);
    if (!actorId) return;
    const current = reviewItems(c.state);
    const next = current[current.findIndex((item) => encodeId(item.event_id) === eventId) + 1];
    const saved = await c.runMutation(`review-${eventId}`, "Saving human acceptance", async () => {
      await c.request("/api/review/accept", { method: "POST", headers: c.actionHeaders(), body: JSON.stringify({
        event_id: decodeURIComponent(eventId), actor_id: actorId, author_id: author.id,
        reason: "Accepted after local evidence review.", project_sha256: c.projectSha(),
      }) });
      if (next) c.state.pendingFocus = encodeId(next.event_id);
      await c.load({ preservePlayback: true, quiet: true });
    });
    if (saved) c.setNotice(next ? "Evidence accepted. The next suggestion is ready." : "Evidence accepted. Review queue complete for this view.", "success");
  }

  async function reviewRelation(relationId, decision) {
    const author = requireHumanActor();
    if (!author) return;
    const accepting = decision === "accept";
    const saved = await c.runMutation(`relation-${relationId}`, `${accepting ? "Accepting" : "Rejecting"} pedagogical suggestion`, async () => {
      await c.request(`/api/review/relations/${accepting ? "accept" : "reject"}`, { method: "POST", headers: c.actionHeaders(), body: JSON.stringify({
        relation_id: decodeURIComponent(relationId), author_id: author.id,
        reason: accepting ? "Accepted after reviewing the linked local transcript evidence." : "Rejected after reviewing the linked local transcript evidence.",
        project_sha256: c.projectSha(),
      }) });
      await c.load({ preservePlayback: true, quiet: true });
    });
    if (saved) c.setNotice(accepting ? "Relation accepted; evidence-backed practice plan updated." : "Relation rejected; machine suggestion remains in the audit trail.", "success");
  }

  function openRevision(eventId, mode) {
    const author = requireHumanActor();
    if (!author) return;
    const actorId = attributedActorId(eventId);
    if (!actorId) return;
    const collection = mode === "revise-suggestion" ? c.state.data?.lesson?.transcript_suggestions : c.state.data?.lesson?.full_transcript;
    const event = list(collection).find((item) => encodeId(item.event_id) === eventId);
    if (!event) return c.setNotice("The selected evidence is no longer in the current project view.", "error");
    c.state.dialog = { mode, eventId, actorId, originalText: event.display_text || "",
      reason: mode === "revise-suggestion" ? "Corrected before accepting the machine suggestion." : "Corrected an accepted annotation during local review." };
    c.renderPreservingPlayback();
  }

  function openBookmarkDialog() {
    if (!requireHumanActor() || !c.state.activeSourceId) return;
    const sourceSelect = c.app.querySelector("[data-source-select]");
    c.state.dialog = { mode: "bookmark", timeSeconds: Number(c.state.media?.currentTime || 0),
      sourceName: sourceSelect?.selectedOptions?.[0]?.textContent?.trim() || "current source" };
    c.renderPreservingPlayback();
  }

  function openReviewerSetup() { c.state.dialog = { mode: "reviewer-setup" }; c.renderPreservingPlayback(); }
  function openRenderedDialog() { const dialog = c.app.querySelector("[data-editor-dialog]"); if (c.state.dialog && dialog && !dialog.open) dialog.showModal(); }
  function closeDialog() { const dialog = c.app.querySelector("[data-editor-dialog]"); if (dialog?.open) dialog.close(); c.state.dialog = null; c.renderPreservingPlayback(); }

  async function submitDialog(form) {
    const dialog = c.state.dialog;
    if (!dialog) return;
    const values = new FormData(form);
    if (dialog.mode === "reviewer-setup") return submitReviewerSetup(values);
    const author = requireHumanActor();
    if (!author) return;
    if (dialog.mode === "bookmark") return submitBookmark(dialog, values, author);
    return submitRevision(dialog, values, author);
  }

  async function submitReviewerSetup(values) {
    const role = String(values.get("role") || "").trim();
    if (!role) return;
    const bytes = new Uint8Array(12); crypto.getRandomValues(bytes);
    const actorId = `actor:local-${Array.from(bytes, (item) => item.toString(16).padStart(2, "0")).join("")}`;
    const saved = await c.runMutation("reviewer-setup", "Creating local reviewer", async () => {
      await c.request("/api/actors", { method: "POST", headers: c.actionHeaders(), body: JSON.stringify({ actor_id: actorId, role, project_sha256: c.projectSha() }) });
      c.state.authorId = actorId; c.state.dialog = null; await c.load({ preservePlayback: true, quiet: true });
    });
    if (saved) c.setNotice("Local reviewer created. You can now record and review evidence.", "success");
  }

  async function submitBookmark(dialog, values, author) {
    const label = String(values.get("label") || "").trim(); if (!label) return;
    const saved = await c.runMutation("bookmark", "Saving exact-time bookmark", async () => {
      await c.request("/api/bookmarks", { method: "POST", headers: c.actionHeaders(), body: JSON.stringify({ source_id: c.state.activeSourceId, start_us: Math.round(dialog.timeSeconds * 1e6), duration_us: 0, label, author_id: author.id, project_sha256: c.projectSha() }) });
      c.state.dialog = null; await c.load({ preservePlayback: true, quiet: true });
    });
    if (saved) c.setNotice("Bookmark saved to this private project.", "success");
  }

  async function submitRevision(dialog, values, author) {
    const replacementText = String(values.get("replacement_text") || "").trim();
    const reason = String(values.get("reason") || "").trim(); if (!replacementText || !reason) return;
    const endpoint = dialog.mode === "revise-suggestion" ? "/api/review/accept" : "/api/review/revise";
    const saved = await c.runMutation(`revision-${dialog.eventId}`, "Saving evidence revision", async () => {
      await c.request(endpoint, { method: "POST", headers: c.actionHeaders(), body: JSON.stringify({ event_id: decodeURIComponent(dialog.eventId), actor_id: dialog.actorId, author_id: author.id, reason, replacement_text: replacementText, project_sha256: c.projectSha() }) });
      c.state.dialog = null; await c.load({ preservePlayback: true, quiet: true });
    });
    if (saved) c.setNotice("Revision saved with its human reason and source timing.", "success");
  }

  async function updatePractice(checkbox) {
    const actor = requireHumanActor(); if (!actor) { checkbox.checked = !checkbox.checked; return; }
    const taskId = checkbox.dataset.practice; const completed = checkbox.checked;
    c.app.querySelectorAll(`[data-practice="${CSS.escape(taskId)}"]`).forEach((item) => { item.disabled = true; item.checked = completed; });
    const saved = await c.runMutation(`practice-${taskId}`, "Updating practice plan", async () => {
      await c.request("/api/practice", { method: "POST", headers: c.actionHeaders(), body: JSON.stringify({ task_id: decodeURIComponent(taskId), completed, author_id: actor.id, project_sha256: c.projectSha() }) });
      await c.load({ preservePlayback: true, quiet: true });
    });
    if (saved) c.setNotice(completed ? "Practice task marked complete." : "Practice task reopened.", "success"); else checkbox.checked = !completed;
  }

  return { acceptSuggestion, reviewRelation, openRevision, openBookmarkDialog, openReviewerSetup,
    openRenderedDialog, closeDialog, submitDialog, updatePractice, requireHumanActor, attributedActorId };
}
