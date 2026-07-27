/** Media binding, seek, playhead sync, and source switching. */

import {
  formatTime,
  list,
  musicalTimeAt,
  sourceDurationSeconds,
  sourceName,
} from "/assets/workbench_ui.mjs";

/**
 * @param {object} c Controller context (state, app, setNotice, refresh*).
 */
export function createPlayback(c) {
  function snapshotPlayback() {
    return {
      sourceId: c.state.activeSourceId,
      currentTime: Number(c.state.media?.currentTime || 0),
      paused: c.state.media?.paused !== false,
    };
  }

  function selectedMedia() {
    const media = list(c.state.data?.media);
    return media.find((item) => item.source_id === c.state.activeSourceId) || media[0] || null;
  }

  function bindMedia(restore) {
    c.state.media = c.app.querySelector("[data-media]");
    const mediaRecord = selectedMedia();
    if (!c.state.media || !mediaRecord?.url) return;
    c.state.media.src = mediaRecord.url;
    c.state.media.addEventListener("loadedmetadata", () => {
      const duration = Number(c.state.media?.duration);
      if (Number.isFinite(duration) && duration > 0) {
        c.state.mediaDurations[c.state.activeSourceId] = duration;
        c.refreshTimeline();
      }
      if (restore?.sourceId === c.state.activeSourceId) {
        c.state.media.currentTime = Math.min(
          Number(restore.currentTime || 0),
          Number.isFinite(duration) ? duration : Number.MAX_SAFE_INTEGER,
        );
        if (!restore.paused) c.state.media.play().catch(() => {});
        restore = null;
      }
      syncPlayback();
    });
    c.state.media.addEventListener("timeupdate", syncPlayback);
    c.state.media.addEventListener("play", syncPlaybackState);
    c.state.media.addEventListener("pause", syncPlaybackState);
    c.state.media.addEventListener("ended", syncPlaybackState);
    c.state.media.addEventListener("error", () => {
      c.setNotice("This local media source could not be played. Its checksum or format may need review.", "error");
    });
  }

  function seek(seconds, sourceId = c.state.activeSourceId, autoplay = true) {
    if (sourceId && sourceId !== c.state.activeSourceId) switchSource(sourceId);
    if (!c.state.media || !selectedMedia()?.url) {
      c.setNotice("This evidence has no playable project media.", "error");
      return;
    }
    const duration = Number(c.state.media.duration);
    const upper = Number.isFinite(duration) && duration > 0 ? duration : Number.MAX_SAFE_INTEGER;
    c.state.media.currentTime = Math.max(0, Math.min(upper, Number(seconds || 0)));
    if (autoplay) c.state.media.play().catch((error) => {
      c.setNotice(`Playback is unavailable: ${error.message}`, "error");
    });
    syncPlayback();
  }

  function switchSource(sourceId) {
    const target = list(c.state.data?.media).find((item) => item.source_id === sourceId);
    if (!target) {
      c.setNotice("That evidence source is not available for local playback.", "error");
      return;
    }
    c.state.media?.pause();
    c.state.activeSourceId = sourceId;
    c.state.query = "";
    const sourceSelect = c.app.querySelector("[data-source-select]");
    if (sourceSelect) sourceSelect.value = sourceId;
    if (c.state.media) {
      c.state.media.src = target.url;
      c.state.media.load();
    }
    c.refreshTimeline();
    c.refreshPanel();
    const clockSource = c.app.querySelector(".playback-clock .clock-source-line");
    if (clockSource) {
      clockSource.textContent = sourceSelect?.selectedOptions?.[0]?.textContent?.trim()
        || sourceName(c.state, sourceId)
        || "Source";
    }
    syncPlayback();
  }

  function syncPlayback() {
    const current = Number(c.state.media?.currentTime || 0);
    const duration = sourceDurationSeconds(c.state);
    c.app.querySelectorAll("[data-playhead]").forEach((node) => {
      node.style.left = `${Math.max(0, Math.min(100, current / duration * 100))}%`;
    });
    const clock = c.app.querySelector("[data-clock]");
    if (clock) clock.textContent = formatTime(current);
    const physical = c.app.querySelectorAll("[data-clock-physical]");
    physical.forEach((node) => {
      node.textContent = formatTime(current);
    });
    const musicalLabel = musicalTimeAt(c.state, current);
    c.app.querySelectorAll("[data-clock-musical]").forEach((node) => {
      if (musicalLabel) node.textContent = musicalLabel;
    });
  }

  function syncPlaybackState() {
    const label = c.app.querySelector("[data-play-icon]");
    const button = label?.closest("button");
    if (!label || !button) return;
    const playing = c.state.media?.paused === false;
    label.textContent = playing ? "Pause" : "Play";
    button.setAttribute("aria-label", playing ? "Pause source" : "Play source");
  }

  return {
    snapshotPlayback,
    selectedMedia,
    bindMedia,
    seek,
    switchSource,
    syncPlayback,
    syncPlaybackState,
  };
}
