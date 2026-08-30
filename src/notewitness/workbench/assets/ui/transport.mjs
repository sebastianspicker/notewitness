import {
  escapeHTML,
  formatTime,
  hasMusicalTime,
  humanActor,
  isMultiSource,
  musicalTimeAt,
  sourceName,
} from "/assets/ui/utils.mjs";

export function renderTransport(state) {
  const recording = state.recorder?.state === "recording";
  const saving = state.captureState === "saving";
  const current = Number(state.media?.currentTime || 0);
  const multi = isMultiSource(state);
  const musical = hasMusicalTime(state);
  const musicalLabel = musical ? (musicalTimeAt(state, current) || "unmetered") : "";
  return `<footer class="transport ${multi ? "is-multi-source" : "is-single-source"}" aria-label="Playback and recording controls">
    <div class="record-group rec">
      <button class="record-button ${recording ? "is-recording" : ""}" data-action="record"
        aria-label="${recording ? "Stop and save recording" : "Start local recording"}"
        ${humanActor(state) && !saving ? "" : "disabled"}><span aria-hidden="true"></span></button>
      <div>
        <strong data-record-label>${saving ? "Saving recording…" : recording ? "Recording locally" : "New local take"}</strong>
        <span data-record-duration>${recording ? "00:00" : "Microphone off"}</span>
      </div>
      <button class="text-button" data-action="cancel-recording" ${recording ? "" : "hidden"}>Discard</button>
    </div>
    <div class="playback-controls">
      <button data-action="seek-back" aria-label="Seek back five seconds">−5</button>
      <button class="play-button play" data-action="play" aria-label="Play source" ${state.activeSourceId ? "" : "disabled"}>
        <span data-play-icon>Play</span>
      </button>
      <button data-action="seek-forward" aria-label="Seek forward five seconds">+5</button>
    </div>
    <div class="playback-clock now">
      <output data-clock>${formatTime(current)}</output>
      ${musical
        ? `<span class="clock-musical-line" data-clock-musical>${escapeHTML(musicalLabel)}</span>`
        : `<span class="clock-source-line">${escapeHTML(sourceName(state, state.activeSourceId), "No source")}</span>`}
    </div>
  </footer>`;
}
