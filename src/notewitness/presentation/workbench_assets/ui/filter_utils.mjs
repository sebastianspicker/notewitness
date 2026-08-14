import { itemSource, list } from "/assets/ui/value_utils.mjs";

export function reviewItems(state) {
  return filterItems(state, state.data?.lesson?.transcript_suggestions, true);
}

export function transcriptItems(state) {
  return filterItems(state, state.data?.lesson?.full_transcript, false);
}

function filterItems(state, items, useKindFilter) {
  const query = String(state.query || "").trim().toLocaleLowerCase();
  return list(items).filter((item) => matchesActiveFilters(state, item, useKindFilter, query));
}

function matchesActiveFilters(state, item, useKindFilter, query) {
  const matchesSource = !state.activeSourceId || itemSource(item) === state.activeSourceId;
  const matchesKind = !useKindFilter || state.reviewKind === "all"
    || String(item.content_kind || "other") === state.reviewKind;
  const searchableText = [item.display_text, item.actor_role, item.content_kind,
    item.review_status].join(" ").toLocaleLowerCase();
  const matchesQuery = !query || searchableText.includes(query);
  return matchesSource && matchesKind && matchesQuery;
}
