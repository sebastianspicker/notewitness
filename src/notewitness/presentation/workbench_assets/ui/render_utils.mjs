import { escapeHTML, list } from "/assets/ui/value_utils.mjs";

export function renderActorAttributionOptionsHTML(state, selectedId, requireChoice) {
  let optionsHTML = requireChoice
    ? '<option value="" selected disabled>Choose project actor…</option>' : "";
  for (const actor of list(state.data?.actors).filter((item) => item.id)) {
    const selected = !requireChoice && actor.id === selectedId ? "selected" : "";
    const label = actor.instrument_role ? `${actor.role} · ${actor.instrument_role}` : actor.role;
    optionsHTML += `<option value="${escapeHTML(actor.id)}" ${selected}>${escapeHTML(label)}</option>`;
  }
  return optionsHTML;
}

export function renderConfidence(confidence) {
  if (!confidence || typeof confidence !== "object") return "confidence not reported";
  if (confidence.kind === "not_applicable") return "confidence not applicable";
  const score = [confidence.value, confidence.score, confidence.probability].find(
    (value) => Number.isFinite(Number(value)),
  );
  return score === undefined ? String(confidence.kind || "confidence reported")
    : `${Math.round(Number(score) * (Number(score) <= 1 ? 100 : 1))}% confidence`;
}

export function renderPairs(pairs) {
  return pairs.map(([key, value]) => `<dt>${escapeHTML(key.replaceAll("_", " "))}</dt><dd>${escapeHTML(value)}</dd>`).join("");
}
