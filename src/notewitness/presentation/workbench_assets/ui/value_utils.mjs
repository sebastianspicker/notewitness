const FALLBACK = "N/A";

export const list = (value) => Array.isArray(value) ? value : [];

export function escapeHTML(value, fallback = FALLBACK) {
  const entities = new Map([
    ["&", "&amp;"], ["<", "&lt;"], [">", "&gt;"], ['"', "&quot;"], ["'", "&#39;"],
  ]);
  return String(value ?? fallback).replace(/[&<>"']/g, (character) => entities.get(character));
}

export const encodeId = (value) => encodeURIComponent(String(value ?? ""));

export function humanActors(state) {
  return list(state.data?.actors).filter((actor) => actor?.id && actor.human_evidence_eligible === true);
}

export function humanActor(state) {
  return humanActors(state).find((actor) => actor.id === state.authorId) || null;
}

export const anchor = (item) => list(item?.anchors)[0] || item?.anchor || {};
export const playback = (item) => item?.playback || anchor(item).span || item || {};
export const itemTime = (item) => Number(playback(item).start_us ?? 0) / 1e6;
export const itemDuration = (item) => Number(playback(item).duration_us ?? 0) / 1e6;
export const itemSource = (item) => String(playback(item).source_id || "");

export function sourceName(state, sourceId) {
  const media = list(state.data?.media).find((item) => item.source_id === sourceId);
  if (media?.display_name) return String(media.display_name);
  const source = list(state.data?.lesson?.sources).find((item) => item.source_id === sourceId);
  const raw = String(source?.uri || sourceId || "Source");
  const name = raw.split("/").filter(Boolean).at(-1) || raw;
  return name.length > 42 ? `${name.slice(0, 39)}…` : name;
}

export function modalityLabel(modality) {
  const labels = new Map([
    ["speech_transcription", "speech transcription"],
    ["activity_segmentation", "speech/music activity segmentation"],
    ["anonymous_diarization", "anonymous speaker diarization"],
    ["note_transcription", "note transcription"],
    ["instrument_detection", "instrument detection"],
    ["instrument_diarization", "instrument diarization"],
  ]);
  return labels.get(modality) || String(modality).replaceAll("_", " ");
}

export function mediaCount(state) {
  return list(state.data?.media).length;
}

export function isMultiSource(state) {
  return mediaCount(state) > 1;
}
