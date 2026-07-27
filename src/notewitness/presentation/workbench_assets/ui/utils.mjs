const FALLBACK = "N/A";

export const list = (value) => Array.isArray(value) ? value : [];

export function escapeHTML(value, fallback = FALLBACK) {
  return String(value ?? fallback).replace(
    /[&<>"']/g,
    (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#39;",
    })[character],
  );
}

export const encodeId = (value) => encodeURIComponent(String(value ?? ""));

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

export function humanActors(state) {
  return list(state.data?.actors).filter((actor) => {
    return actor?.id && actor.human_evidence_eligible === true;
  });
}

export function humanActor(state) {
  return humanActors(state).find((actor) => actor.id === state.authorId) || null;
}

export const anchor = (item) => list(item?.anchors)[0] || item?.anchor || {};
export const playback = (item) => item?.playback || anchor(item).span || item || {};
export const itemTime = (item) => Number(playback(item).start_us ?? 0) / 1e6;
export const itemDuration = (item) => Number(playback(item).duration_us ?? 0) / 1e6;
export const itemSource = (item) => String(playback(item).source_id || "");

export function sourceDurationSeconds(state, sourceId = state.activeSourceId) {
  const measured = Number(state.mediaDurations?.[sourceId]);
  if (Number.isFinite(measured) && measured > 0) return measured;
  const projected = Number(
    list(state.data?.media).find((item) => item.source_id === sourceId)?.duration_us,
  ) / 1e6;
  if (Number.isFinite(projected) && projected > 0) return projected;
  const extents = list(state.data?.lesson?.statistics?.timeline_extents).filter(
    (extent) => extent.source_id === sourceId,
  );
  const extentEnd = Math.max(0, ...extents.map((extent) => {
    return (Number(extent.start_us || 0) + Number(extent.duration_us || 0)) / 1e6;
  }));
  if (extentEnd > 0) return extentEnd;
  const laneEnd = Math.max(0, ...list(state.data?.timeline?.lanes).flatMap((lane) => {
    return list(lane.items).filter((item) => itemSource(item) === sourceId).map((item) => {
      return itemTime(item) + itemDuration(item);
    });
  }));
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
  const multiplier = normalized <= 1 ? 1 : normalized <= 2 ? 2
    : normalized <= 5 ? 5 : 10;
  const step = multiplier * power;
  const ticks = [];
  for (let value = 0; value < duration; value += step) ticks.push(value);
  if (!ticks.length || duration - ticks.at(-1) > step * 0.25) ticks.push(duration);
  else ticks[ticks.length - 1] = duration;
  return ticks.slice(0, 12);
}

export function reviewItems(state) {
  return filterItems(state, state.data?.lesson?.transcript_suggestions, true);
}

export function transcriptItems(state) {
  return filterItems(state, state.data?.lesson?.full_transcript, false);
}

function filterItems(state, items, useKindFilter) {
  const query = String(state.query || "").trim().toLocaleLowerCase();
  return list(items).filter((item) => {
    const sameSource = !state.activeSourceId || itemSource(item) === state.activeSourceId;
    const sameKind = !useKindFilter || state.reviewKind === "all"
      || String(item.content_kind || "other") === state.reviewKind;
    const haystack = [item.display_text, item.actor_role, item.content_kind,
      item.review_status].join(" ").toLocaleLowerCase();
    return sameSource && sameKind && (!query || haystack.includes(query));
  });
}

export function sourceName(state, sourceId) {
  const media = list(state.data?.media).find((item) => item.source_id === sourceId);
  if (media?.display_name) return String(media.display_name);
  const source = list(state.data?.lesson?.sources).find((item) => item.source_id === sourceId);
  const raw = String(source?.uri || sourceId || "Source");
  const name = raw.split("/").filter(Boolean).at(-1) || raw;
  return name.length > 42 ? `${name.slice(0, 39)}…` : name;
}

export function renderActorAttributionOptions(state, selectedId, requireChoice) {
  const prompt = requireChoice
    ? '<option value="" selected disabled>Choose project actor…</option>' : "";
  const options = list(state.data?.actors).filter((actor) => actor.id).map((actor) => {
    const selected = !requireChoice && actor.id === selectedId ? "selected" : "";
    const label = actor.instrument_role ? `${actor.role} · ${actor.instrument_role}` : actor.role;
    return `<option value="${escapeHTML(actor.id)}" ${selected}>${escapeHTML(label)}</option>`;
  }).join("");
  return prompt + options;
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

export function modalityLabel(modality) {
  return {
    speech_transcription: "speech transcription",
    activity_segmentation: "speech/music activity segmentation",
    anonymous_diarization: "anonymous speaker diarization",
    note_transcription: "note transcription",
    instrument_detection: "instrument detection",
    instrument_diarization: "instrument diarization",
  }[modality] || String(modality).replaceAll("_", " ");
}

export function mediaCount(state) {
  return list(state.data?.media).length;
}

/** True when the project has more than one playable source. */
export function isMultiSource(state) {
  return mediaCount(state) > 1;
}

/**
 * Format a graph musical_selector for the dual clock.
 * Returns empty string when the selector has no displayable musical position.
 */
export function formatMusicalSelector(selector) {
  if (!selector || typeof selector !== "object") return "";
  const barStart = selector.bar_start ?? selector.bar ?? selector.measure;
  const barEnd = selector.bar_end ?? selector.measure_end;
  const beat = selector.beat ?? selector.beat_start;
  const parts = [];
  if (Number.isFinite(Number(barStart)) && Number.isFinite(Number(barEnd))
    && Number(barEnd) !== Number(barStart)) {
    parts.push(`bars ${Number(barStart)}–${Number(barEnd)}`);
  } else if (Number.isFinite(Number(barStart))) {
    parts.push(`bar ${Number(barStart)}`);
  }
  if (Number.isFinite(Number(beat))) {
    parts.push(`beat ${Number(beat)}`);
  }
  if (!parts.length && typeof selector.phrase === "string" && selector.phrase.trim()) {
    parts.push(selector.phrase.trim());
  }
  if (!parts.length && typeof selector.label === "string" && selector.label.trim()) {
    parts.push(selector.label.trim());
  }
  return parts.join(" · ");
}

function collectMusicalMarkers(state, sourceId = state.activeSourceId) {
  const lesson = state.data?.lesson || {};
  const records = [
    ...list(lesson.activity),
    ...list(lesson.full_transcript),
    ...list(lesson.transcript_suggestions),
    ...list(lesson.summary?.key_moments),
    ...list(lesson.relation_suggestions),
  ];
  const markers = [];
  for (const record of records) {
    for (const point of list(record?.anchors).length ? list(record.anchors) : [record]) {
      const span = point?.span || point?.playback || point;
      const selector = point?.musical_selector;
      const label = formatMusicalSelector(selector);
      const sid = String(span?.source_id || point?.source_id || "");
      if (!label || (sourceId && sid && sid !== sourceId)) continue;
      const start = Number(span?.start_us ?? 0) / 1e6;
      const duration = Number(span?.duration_us ?? 0) / 1e6;
      if (!Number.isFinite(start)) continue;
      markers.push({
        start,
        end: start + (Number.isFinite(duration) && duration > 0 ? duration : 0),
        label,
        alignment: String(point?.alignment_state || ""),
      });
    }
  }
  markers.sort((a, b) => a.start - b.start || a.end - b.end);
  return markers;
}

/** Whether any musical-time anchors exist for the active (or given) source. */
export function hasMusicalTime(state, sourceId = state.activeSourceId) {
  return collectMusicalMarkers(state, sourceId).length > 0;
}

/**
 * Resolve the dual-clock musical label at a physical time in seconds.
 * Prefer a covering aligned span; otherwise the nearest prior span; else unmetered.
 */
export function musicalTimeAt(state, seconds, sourceId = state.activeSourceId) {
  const markers = collectMusicalMarkers(state, sourceId);
  if (!markers.length) return "";
  const t = Number(seconds);
  const time = Number.isFinite(t) && t > 0 ? t : 0;
  const covering = markers.filter((item) => {
    const end = item.end > item.start ? item.end : item.start + 0.001;
    return time >= item.start && time < end;
  });
  if (covering.length) {
    covering.sort((a, b) => (a.end - a.start) - (b.end - b.start));
    return covering[0].label;
  }
  let prior = null;
  for (const item of markers) {
    if (item.start <= time) prior = item;
    else break;
  }
  if (prior) return prior.label;
  return "unmetered";
}

/** Compact dual-clock markup for timeline header and transport. */
export function renderDualClocks(state, seconds = 0) {
  if (!hasMusicalTime(state)) return "";
  const musical = musicalTimeAt(state, seconds) || "unmetered";
  return `<div class="dual-clocks clocks" data-dual-clocks aria-label="Physical and musical time">
    <span class="clock-physical">Physical <b data-clock-physical>${formatTime(seconds)}</b></span>
    <span class="clock-musical">Musical <b data-clock-musical>${escapeHTML(musical)}</b></span>
  </div>`;
}
