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
  return score === undefined ? escapeHTML(confidence.kind || "confidence reported")
    : `${Math.round(Number(score) * (Number(score) <= 1 ? 100 : 1))}% confidence`;
}

export function renderPairs(pairs) {
  return pairs.map(([key, value]) => `<dt>${escapeHTML(key.replaceAll("_", " "))}</dt><dd>${escapeHTML(value)}</dd>`).join("");
}

const STATUS_LABELS = new Map([
  ["machine_suggested", "Suggested"],
  ["human_accepted", "Accepted"],
  ["accepted", "Accepted"],
  ["human_created", "Human-created"],
  ["human_revision", "Revised"],
  ["human_revised", "Revised"],
  ["human_annotation", "Annotated"],
  ["human_capture", "Captured"],
  ["human_review", "In human review"],
  ["rejected", "Rejected"],
]);

/** Plain-language review state; machine output is "pencilled", human output "inked". */
export function statusLabel(status) {
  const value = String(status || "");
  return STATUS_LABELS.get(value) || value.replaceAll("_", " ") || "Unknown state";
}

export function statusTone(status) {
  const value = String(status || "machine_suggested");
  if (value === "rejected") return "rejected";
  return value === "machine_suggested" || value.includes("suggest") ? "suggested" : "accepted";
}

/** Relation types are namespaced ("local:assigned_for_practice"); show the readable part. */
export function relationLabel(type, fallback = "relation") {
  const value = String(type || fallback);
  return value.slice(value.lastIndexOf(":") + 1).replaceAll("_", " ");
}

/** Drop a repeated "<relation type>: " prefix from a projected label. */
export function relationText(label, type) {
  const text = String(label ?? "");
  const prefixes = [String(type || ""), String(type || "").replaceAll("_", " ")].filter(Boolean);
  const prefix = prefixes.find((value) => text.toLowerCase().startsWith(`${value.toLowerCase()}: `));
  return prefix ? text.slice(prefix.length + 2) : text;
}
