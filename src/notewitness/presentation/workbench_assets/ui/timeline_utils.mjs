import { itemDuration, itemSource, itemTime, list } from "/assets/ui/value_utils.mjs";

export function formatTime(seconds, precision = true) {
  const numeric = Number(seconds);
  const safe = Number.isFinite(numeric) && numeric > 0 ? numeric : 0;
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const wholeSeconds = Math.floor(safe % 60);
  const milliseconds = Math.floor((safe % 1) * 1000);
  const prefix = hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}`
    : String(minutes).padStart(2, "0");
  return precision
    ? `${prefix}:${String(wholeSeconds).padStart(2, "0")}.${String(milliseconds).padStart(3, "0")}`
    : `${prefix}:${String(wholeSeconds).padStart(2, "0")}`;
}

export function sourceDurationSeconds(state, sourceId = state.activeSourceId) {
  const measured = Number(Object.entries(state.mediaDurations || {}).find(([id]) => id === sourceId)?.[1]);
  if (Number.isFinite(measured) && measured > 0) return measured;
  const projected = Number(list(state.data?.media).find((item) => item.source_id === sourceId)?.duration_us) / 1e6;
  if (Number.isFinite(projected) && projected > 0) return projected;
  const extents = list(state.data?.lesson?.statistics?.timeline_extents).filter((extent) => extent.source_id === sourceId);
  const extentEnd = Math.max(0, ...extents.map((extent) => (
    Number(extent.start_us || 0) + Number(extent.duration_us || 0)
  ) / 1e6));
  if (extentEnd > 0) return extentEnd;
  const laneEnd = Math.max(0, ...list(state.data?.timeline?.lanes).flatMap((lane) => (
    list(lane.items).filter((item) => itemSource(item) === sourceId)
      .map((item) => itemTime(item) + itemDuration(item))
  )));
  if (laneEnd > 0) return laneEnd;
  if (sourceId) return 1;
  const aggregate = Number(state.data?.lesson?.statistics?.timeline_duration_us || 0) / 1e6;
  return aggregate > 0 ? aggregate : 1;
}

export function timelineTicks(durationSeconds, targetCount = 7) {
  const duration = Number(durationSeconds);
  if (!Number.isFinite(duration) || duration <= 0) return [0];
  const rough = duration / Math.max(2, targetCount - 1);
  const power = 10 ** Math.floor(Math.log10(Math.max(rough, 0.001)));
  const normalized = rough / power;
  const multiplier = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  const step = multiplier * power;
  const ticks = [];
  for (let value = 0; value < duration; value += step) ticks.push(value);
  if (!ticks.length || duration - ticks.at(-1) > step * 0.25) ticks.push(duration);
  else ticks[ticks.length - 1] = duration;
  return ticks.slice(0, 12);
}

export function formatMusicalSelector(selector) {
  if (!selector || typeof selector !== "object") return "";
  return formatBarAndBeat(selector) || firstText(selector.phrase, selector.label);
}

function formatBarAndBeat(selector) {
  const bar = formatBarRange(selector.bar_start ?? selector.bar ?? selector.measure,
    selector.bar_end ?? selector.measure_end);
  return [bar, finiteLabel("beat", selector.beat ?? selector.beat_start)].filter(Boolean).join(" · ");
}

function formatBarRange(start, end) {
  if (!Number.isFinite(Number(start))) return "";
  if (Number.isFinite(Number(end)) && Number(end) !== Number(start)) return `bars ${Number(start)}–${Number(end)}`;
  return finiteLabel("bar", start);
}

function finiteLabel(label, value) {
  return Number.isFinite(Number(value)) ? `${label} ${Number(value)}` : "";
}

function firstText(...values) {
  return values.find((value) => typeof value === "string" && value.trim())?.trim() || "";
}

function collectMusicalMarkers(state, sourceId = state.activeSourceId) {
  return musicalRecords(state).flatMap((record) => musicalPoints(record)
    .map((point) => markerForPoint(point, sourceId)).filter(Boolean))
    .sort((a, b) => a.start - b.start || a.end - b.end);
}

function musicalRecords(state) {
  const lesson = state.data?.lesson || {};
  return [lesson.activity, lesson.full_transcript, lesson.transcript_suggestions,
    lesson.summary?.key_moments, lesson.relation_suggestions].flatMap(list);
}

function musicalPoints(record) {
  const anchors = list(record?.anchors);
  return anchors.length ? anchors : [record];
}

function markerForPoint(point, sourceId) {
  const span = point?.span || point?.playback || point;
  const label = formatMusicalSelector(point?.musical_selector);
  const pointSource = String(span?.source_id || point?.source_id || "");
  const start = Number(span?.start_us ?? 0) / 1e6;
  if (!isDisplayableMarker(label, sourceId, pointSource, start)) return null;
  const duration = Number(span?.duration_us ?? 0) / 1e6;
  return { start, end: start + (Number.isFinite(duration) && duration > 0 ? duration : 0), label,
    alignment: String(point?.alignment_state || "") };
}

function isDisplayableMarker(label, selectedSource, pointSource, start) {
  const belongsToSelectedSource = !selectedSource || !pointSource || pointSource === selectedSource;
  return Boolean(label) && belongsToSelectedSource && Number.isFinite(start);
}

export function hasMusicalTime(state, sourceId = state.activeSourceId) {
  return collectMusicalMarkers(state, sourceId).length > 0;
}

export function musicalTimeAt(state, seconds, sourceId = state.activeSourceId) {
  const markers = collectMusicalMarkers(state, sourceId);
  if (!markers.length) return "";
  const time = Number.isFinite(Number(seconds)) && Number(seconds) > 0 ? Number(seconds) : 0;
  const covering = markers.filter((item) => time >= item.start && time < markerEnd(item));
  if (covering.length) return covering.sort((a, b) => (a.end - a.start) - (b.end - b.start))[0].label;
  return markers.filter((item) => item.start <= time).at(-1)?.label || "unmetered";
}

function markerEnd(marker) {
  return marker.end > marker.start ? marker.end : marker.start + 0.001;
}
