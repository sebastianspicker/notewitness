/** Review, export, bookmarks, practice, dialogs, capture, tuner, metronome. */

import { estimatePitch } from "/assets/pitch_estimator.mjs";
import {
  encodeId,
  formatTime,
  humanActor,
  list,
  reviewItems,
} from "/assets/workbench_ui.mjs";

const MAX_CAPTURE_BYTES = 512 * 1024 * 1024;
const MAX_CAPTURE_MILLISECONDS = 2 * 60 * 60 * 1000;

/**
 * @param {object} c Controller context (state, app, request, render helpers, …).
 */
export function createActions(c) {
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
    const index = current.findIndex((item) => encodeId(item.event_id) === eventId);
    const next = current[index + 1];
    const key = `review-${eventId}`;
    const saved = await c.runMutation(key, "Saving human acceptance", async () => {
      await c.request("/api/review/accept", {
        method: "POST",
        headers: c.actionHeaders(),
        body: JSON.stringify({
          event_id: decodeURIComponent(eventId),
          actor_id: actorId,
          author_id: author.id,
          reason: "Accepted after local evidence review.",
          project_sha256: c.projectSha(),
        }),
      });
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
      await c.request(`/api/review/relations/${accepting ? "accept" : "reject"}`, {
        method: "POST",
        headers: c.actionHeaders(),
        body: JSON.stringify({
          relation_id: decodeURIComponent(relationId),
          author_id: author.id,
          reason: accepting
            ? "Accepted after reviewing the linked local transcript evidence."
            : "Rejected after reviewing the linked local transcript evidence.",
          project_sha256: c.projectSha(),
        }),
      });
      await c.load({ preservePlayback: true, quiet: true });
    });
    if (saved) c.setNotice(
      accepting ? "Relation accepted; evidence-backed practice plan updated." : "Relation rejected; machine suggestion remains in the audit trail.",
      "success",
    );
  }

  function openRevision(eventId, mode) {
    const author = requireHumanActor();
    if (!author) return;
    const actorId = attributedActorId(eventId);
    if (!actorId) return;
    const collection = mode === "revise-suggestion"
      ? c.state.data?.lesson?.transcript_suggestions : c.state.data?.lesson?.full_transcript;
    const event = list(collection).find((item) => encodeId(item.event_id) === eventId);
    if (!event) {
      c.setNotice("The selected evidence is no longer in the current project view.", "error");
      return;
    }
    c.state.dialog = {
      mode,
      eventId,
      actorId,
      originalText: event.display_text || "",
      reason: mode === "revise-suggestion"
        ? "Corrected before accepting the machine suggestion."
        : "Corrected an accepted annotation during local review.",
    };
    c.renderPreservingPlayback();
  }

  function openBookmarkDialog() {
    if (!requireHumanActor() || !c.state.activeSourceId) return;
    const sourceSelect = c.app.querySelector("[data-source-select]");
    c.state.dialog = {
      mode: "bookmark",
      timeSeconds: Number(c.state.media?.currentTime || 0),
      sourceName: sourceSelect?.selectedOptions?.[0]?.textContent?.trim() || "current source",
    };
    c.renderPreservingPlayback();
  }

  function openReviewerSetup() {
    c.state.dialog = { mode: "reviewer-setup" };
    c.renderPreservingPlayback();
  }

  function openRenderedDialog() {
    if (!c.state.dialog) return;
    const dialog = c.app.querySelector("[data-editor-dialog]");
    if (dialog && !dialog.open) dialog.showModal();
  }

  function closeDialog() {
    const dialog = c.app.querySelector("[data-editor-dialog]");
    if (dialog?.open) dialog.close();
    c.state.dialog = null;
    c.renderPreservingPlayback();
  }

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
    const bytes = new Uint8Array(12);
    crypto.getRandomValues(bytes);
    const actorId = `actor:local-${Array.from(bytes, (item) => item.toString(16).padStart(2, "0")).join("")}`;
    const saved = await c.runMutation("reviewer-setup", "Creating local reviewer", async () => {
      await c.request("/api/actors", { method: "POST", headers: c.actionHeaders(),
        body: JSON.stringify({ actor_id: actorId, role, project_sha256: c.projectSha() }) });
      c.state.authorId = actorId;
      c.state.dialog = null;
      await c.load({ preservePlayback: true, quiet: true });
    });
    if (saved) c.setNotice("Local reviewer created. You can now record and review evidence.", "success");
  }

  async function submitBookmark(dialog, values, author) {
    const label = String(values.get("label") || "").trim();
    if (!label) return;
    const saved = await c.runMutation("bookmark", "Saving exact-time bookmark", async () => {
      await c.request("/api/bookmarks", { method: "POST", headers: c.actionHeaders(), body: JSON.stringify({
        source_id: c.state.activeSourceId, start_us: Math.round(dialog.timeSeconds * 1e6), duration_us: 0,
        label, author_id: author.id, project_sha256: c.projectSha(),
      }) });
      c.state.dialog = null;
      await c.load({ preservePlayback: true, quiet: true });
    });
    if (saved) c.setNotice("Bookmark saved to this private project.", "success");
  }

  async function submitRevision(dialog, values, author) {
    const replacementText = String(values.get("replacement_text") || "").trim();
    const reason = String(values.get("reason") || "").trim();
    if (!replacementText || !reason) return;
    const endpoint = dialog.mode === "revise-suggestion" ? "/api/review/accept" : "/api/review/revise";
    const saved = await c.runMutation(`revision-${dialog.eventId}`, "Saving evidence revision", async () => {
      await c.request(endpoint, {
        method: "POST",
        headers: c.actionHeaders(),
        body: JSON.stringify({
          event_id: decodeURIComponent(dialog.eventId),
          actor_id: dialog.actorId,
          author_id: author.id,
          reason,
          replacement_text: replacementText,
          project_sha256: c.projectSha(),
        }),
      });
      c.state.dialog = null;
      await c.load({ preservePlayback: true, quiet: true });
    });
    if (saved) c.setNotice("Revision saved with its human reason and source timing.", "success");
  }

  async function updatePractice(checkbox) {
    const actor = requireHumanActor();
    if (!actor) {
      checkbox.checked = !checkbox.checked;
      return;
    }
    const taskId = checkbox.dataset.practice;
    const completed = checkbox.checked;
    c.app.querySelectorAll(`[data-practice="${CSS.escape(taskId)}"]`).forEach((item) => {
      item.disabled = true;
      item.checked = completed;
    });
    const saved = await c.runMutation(`practice-${taskId}`, "Updating practice plan", async () => {
      await c.request("/api/practice", {
        method: "POST",
        headers: c.actionHeaders(),
        body: JSON.stringify({
          task_id: decodeURIComponent(taskId),
          completed,
          author_id: actor.id,
          project_sha256: c.projectSha(),
        }),
      });
      await c.load({ preservePlayback: true, quiet: true });
    });
    if (saved) c.setNotice(completed ? "Practice task marked complete." : "Practice task reopened.", "success");
    else checkbox.checked = !completed;
  }

  async function importMedia(file) {
    if (!file || c.state.importing) return;
    c.state.importing = true;
    c.renderPreservingPlayback();
    c.setNotice(`Importing ${file.name || "recording"} into the private project…`, "info");
    try {
      const result = await c.request("/api/imports", {
        method: "POST",
        headers: {
          ...c.actionHeaders(file.type || "application/octet-stream"),
          "X-Media-Name": encodeURIComponent(file.name || "lesson-recording"),
        },
        body: file,
      });
      c.state.activeSourceId = result.source_id;
      await c.load({ quiet: true });
      c.setNotice("Recording imported locally. It is ready for playback and configured processing.", "success");
    } catch (error) {
      c.setNotice(`Recording could not be imported: ${error.message}`, "error");
    } finally {
      c.state.importing = false;
      c.renderPreservingPlayback();
    }
  }

  async function exportMusic(format) {
    const rightsAuthorized = Boolean(c.app.querySelector("[data-export-rights]")?.checked);
    const lossesAcknowledged = Boolean(c.app.querySelector("[data-export-losses]")?.checked);
    if (!rightsAuthorized || !lossesAcknowledged) {
      c.setNotice(
        "Confirm both export decisions before creating a file from private lesson evidence.",
        "error",
      );
      return;
    }
    const filename = musicExportFilename(format);
    let result = null;
    const saved = await c.runMutation("music-export", `Creating ${format.toUpperCase()} export`, async () => {
      result = await c.request("/api/exports/music", {
        method: "POST",
        headers: c.actionHeaders(),
        body: JSON.stringify({
          format,
          filename,
          source_id: c.state.activeSourceId,
          authorize_local_export: rightsAuthorized,
          acknowledge_export_losses: lossesAcknowledged,
        }),
      });
    });
    if (saved) c.setNotice(musicExportNotice(result, filename), "success");
  }

  function musicExportFilename(format) {
    const extension = format === "midi" ? "mid" : "csv";
    return `notewitness-notes-${new Date().toISOString().replaceAll(":", "-")}.${extension}`;
  }

  function musicExportNotice(result, filename) {
    const losses = list(result?.documented_losses).length;
    const lossSummary = losses ? ` and ${losses} documented projection loss${losses === 1 ? "" : "es"}` : "";
    return `${result?.filename || filename} saved locally with ${result?.record_count || 0} notes${lossSummary}.`;
  }

  async function exportTranscript() {
    const options = transcriptExportOptions();
    if (!options.rights || !options.losses) {
      c.setNotice("Confirm rights authorization and format-loss acknowledgement before export.", "error");
      return;
    }
    let result = null;
    const saved = await c.runMutation("transcript-export", "Creating transcript export", async () => {
      result = await c.request("/api/exports/transcript", {
        method: "POST",
        headers: c.actionHeaders(),
        body: JSON.stringify(transcriptExportPayload(options)),
      });
    });
    if (saved) c.setNotice(`${result?.filename || "Transcript"} saved locally from ${result?.evidence_layer === "include_machine_suggestions" ? "accepted and machine-suggested" : "accepted"} evidence.`, "success");
  }

  function transcriptExportOptions() {
    return {
      format: selectedValue("[data-transcript-format]", "html"),
      evidenceLayer: selectedValue("[data-transcript-layer]", "accepted_only"),
      rights: checked("[data-transcript-rights]"),
      losses: checked("[data-transcript-losses]"),
      pauseValue: selectedValue("[data-transcript-pause]", ""),
      interval: Number(selectedValue("[data-transcript-interval]", 60000)),
      visibleTimestamps: checked("[data-transcript-timestamps]"),
    };
  }

  function selectedValue(selector, fallback) {
    return c.app.querySelector(selector)?.value || fallback;
  }

  function checked(selector) {
    return Boolean(c.app.querySelector(selector)?.checked);
  }

  function transcriptExportPayload(options) {
    const extension = options.format === "webvtt" ? "vtt"
      : options.format === "text" ? "txt" : "html";
    const timestamp = new Date().toISOString().replaceAll(":", "-");
    return {
      format: options.format,
      filename: `notewitness-transcript-${timestamp}.${extension}`,
      source_id: c.state.activeSourceId,
      evidence_layer: options.evidenceLayer,
      authorize_local_export: options.rights,
      acknowledge_export_losses: options.losses,
      visible_timestamps: options.format === "webvtt" ? false : options.visibleTimestamps,
      timestamp_interval_ms: validTimestampInterval(options.interval),
      pause_threshold_ms: transcriptPauseThreshold(options),
    };
  }

  function validTimestampInterval(interval) {
    return Number.isInteger(interval) && interval > 0 ? interval : 60000;
  }

  function transcriptPauseThreshold(options) {
    return options.format === "webvtt" || !options.pauseValue
      ? null : Number(options.pauseValue);
  }

  const actionHandlers = new Map([
    ["play", togglePlayback],
    ["seek-back", () => c.seek((c.state.media?.currentTime || 0) - 5)],
    ["seek-forward", () => c.seek((c.state.media?.currentTime || 0) + 5)],
    ["open-bookmark", openBookmarkDialog],
    ["dismiss-notice", () => c.setNotice("")],
    ["close-dialog", closeDialog],
    ["record", toggleRecording],
    ["cancel-recording", cancelRecording],
    ["tuner", toggleTuner],
    ["metronome", toggleMetronome],
    ["tempo-down", () => setTempo(-1)],
    ["tempo-up", () => setTempo(1)],
    ["enqueue-job", c.enqueueJob],
    ["export-csv", () => exportMusic("csv")],
    ["export-midi", () => exportMusic("midi")],
    ["export-transcript", exportTranscript],
    ["reload", c.load],
  ]);

  async function action(name) {
    const handler = actionHandlers.get(name);
    if (!handler) return;
    try {
      await handler();
    } catch (error) {
      c.setNotice(`Action failed: ${error.message}`, "error");
    }
  }

  async function togglePlayback() {
    if (!c.state.media) return;
    if (c.state.media.paused) {
      await c.state.media.play().catch((error) => c.setNotice(error.message, "error"));
    } else {
      c.state.media.pause();
    }
  }

  function setTempo(change) {
    c.state.tempo = Math.max(20, Math.min(300, c.state.tempo + change));
    c.app.querySelectorAll("[data-bpm]").forEach((node) => { node.textContent = c.state.tempo; });
    if (c.state.metronome) restartMetronome();
  }

  async function toggleRecording() {
    if (c.state.recorder?.state === "recording") {
      c.state.captureState = "saving";
      c.state.recorder.stop();
      refreshRecordingUI();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      c.state.chunks = [];
      c.state.captureBytes = 0;
      c.state.captureStartedAt = Date.now();
      c.state.captureTooLarge = false;
      c.state.captureDiscarded = false;
      c.state.captureState = "recording";
      c.state.recorder = new MediaRecorder(stream);
      c.state.recorder.ondataavailable = (event) => {
        if (!event.data.size) return;
        c.state.captureBytes += event.data.size;
        if (c.state.captureBytes > MAX_CAPTURE_BYTES) {
          c.state.captureTooLarge = true;
          if (c.state.recorder.state === "recording") c.state.recorder.stop();
          return;
        }
        c.state.chunks.push(event.data);
      };
      c.state.recorder.onstop = uploadCapture;
      c.state.recorder.start(1000);
      c.state.captureTimeout = window.setTimeout(() => {
        if (c.state.recorder?.state === "recording") c.state.recorder.stop();
      }, MAX_CAPTURE_MILLISECONDS);
      c.state.captureInterval = window.setInterval(refreshRecordingUI, 250);
      refreshRecordingUI();
    } catch (error) {
      c.state.captureState = "idle";
      c.setNotice(`Recording is unavailable: ${error.message}`, "error");
    }
  }

  function cancelRecording() {
    if (c.state.recorder?.state !== "recording") return;
    c.state.captureDiscarded = true;
    c.state.recorder.stop();
    refreshRecordingUI();
  }

  function refreshRecordingUI() {
    const recording = c.state.recorder?.state === "recording";
    const button = c.app.querySelector('[data-action="record"]');
    const label = c.app.querySelector("[data-record-label]");
    const duration = c.app.querySelector("[data-record-duration]");
    const discard = c.app.querySelector('[data-action="cancel-recording"]');
    button?.classList.toggle("is-recording", recording);
    if (button) button.setAttribute("aria-label", recording ? "Stop and save recording" : "Start local recording");
    if (label) label.textContent = recordingLabel(recording);
    if (duration) duration.textContent = recordingDuration(recording);
    if (discard) discard.hidden = !recording;
  }

  function recordingLabel(recording) {
    if (c.state.captureState === "saving") return "Saving recording…";
    return recording ? "Recording locally" : "New local take";
  }

  function recordingDuration(recording) {
    if (!recording) return "Microphone off";
    return formatTime((Date.now() - c.state.captureStartedAt) / 1000, false);
  }

  async function uploadCapture() {
    window.clearTimeout(c.state.captureTimeout);
    window.clearInterval(c.state.captureInterval);
    const recorder = c.state.recorder;
    const stream = recorder?.stream;
    const author = humanActor(c.state);
    try {
      if (c.state.captureDiscarded) {
        c.setNotice("Recording discarded. No project file was created.", "info");
        return;
      }
      if (c.state.captureTooLarge) throw new Error("Capture exceeded the 512 MiB local limit.");
      if (!author) throw new Error("The selected reviewer is no longer eligible.");
      const contentType = recorder?.mimeType || "audio/webm";
      const blob = new Blob(c.state.chunks, { type: contentType });
      if (!blob.size) throw new Error("The microphone returned an empty recording.");
      const durationMs = Math.max(0, Date.now() - c.state.captureStartedAt);
      c.state.captureState = "saving";
      const result = await c.request("/api/captures", {
        method: "POST",
        headers: {
          ...c.actionHeaders(blob.type),
          "X-Capture-Author": author.id,
          "X-Capture-Duration-Ms": String(durationMs),
          "X-Capture-Name": `Local take ${new Date(c.state.captureStartedAt).toLocaleTimeString()}`,
          "X-Capture-Started-At": new Date(c.state.captureStartedAt).toISOString(),
        },
        body: blob,
      });
      c.state.activeSourceId = result.source_id;
      await c.load({ quiet: true });
      c.setNotice("Local take saved and selected as the active source.", "success");
    } catch (error) {
      c.setNotice(`Recording could not be saved: ${error.message}`, "error");
    } finally {
      stream?.getTracks().forEach((track) => track.stop());
      c.state.recorder = null;
      c.state.chunks = [];
      c.state.captureState = "idle";
      refreshRecordingUI();
    }
  }

  async function toggleTuner() {
    if (c.state.tuner) {
      c.state.tuner.stop();
      c.state.tuner = null;
      c.renderPreservingPlayback();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const context = new AudioContext();
      const analyser = context.createAnalyser();
      analyser.fftSize = 4096;
      context.createMediaStreamSource(stream).connect(analyser);
      const samples = new Float32Array(analyser.fftSize);
      const tuner = { frame: 0, inFlight: false, lastAnalysis: 0, errorShown: false };
      const poll = () => {
        const now = performance.now();
        if (now - tuner.lastAnalysis >= 150 && !tuner.inFlight) {
          tuner.lastAnalysis = now;
          analyser.getFloatTimeDomainData(samples);
          const estimate = estimatePitch(samples, context.sampleRate);
          if (estimate) sendTuner(estimate.frequencyHz, tuner);
        }
        tuner.frame = requestAnimationFrame(poll);
      };
      tuner.frame = requestAnimationFrame(poll);
      tuner.stop = () => {
        cancelAnimationFrame(tuner.frame);
        stream.getTracks().forEach((track) => track.stop());
        context.close();
      };
      c.state.tuner = tuner;
      c.renderPreservingPlayback();
    } catch (error) {
      c.setNotice(`Tuner is unavailable: ${error.message}`, "error");
    }
  }

  async function sendTuner(frequency, tuner) {
    tuner.inFlight = true;
    const hz = c.app.querySelector("[data-tuner-hz]");
    if (hz) hz.textContent = `${frequency.toFixed(1)} Hz`;
    try {
      const reading = await c.request("/api/tuner", {
        method: "POST", headers: c.actionHeaders(), body: JSON.stringify({ frequency_hz: frequency }),
      });
      renderTunerReading(reading);
    } catch (error) {
      if (!tuner.errorShown) {
        tuner.errorShown = true;
        c.setNotice(`Tuner mapping is unavailable: ${error.message}`, "error");
      }
    } finally {
      tuner.inFlight = false;
    }
  }

  function renderTunerReading(reading) {
    const note = c.app.querySelector("[data-tuner-note]");
    const meter = c.app.querySelector("[data-tuner-meter]");
    const container = meter?.closest("[role=meter]");
    if (note && reading?.note_name) note.textContent = `${reading.note_name}${reading.octave ?? ""}`;
    const cents = Math.max(-50, Math.min(50, Number(reading?.cents_offset || 0)));
    if (meter) meter.style.left = `${cents + 50}%`;
    if (container) container.setAttribute("aria-valuenow", String(cents));
  }

  async function toggleMetronome() {
    if (c.state.metronome) {
      stopMetronome();
      c.renderPreservingPlayback();
      return;
    }
    await startMetronome();
    c.renderPreservingPlayback();
  }

  async function startMetronome() {
    const context = new AudioContext();
    const metronome = {
      context,
      cycleSeconds: 0,
      cycleStart: context.currentTime + 0.05,
      tickIndex: 0,
      ticks: [],
      timer: 0,
    };
    c.state.metronome = metronome;
    try {
      const plan = await c.request("/api/metronome", {
        method: "POST",
        headers: c.actionHeaders(),
        body: JSON.stringify({ bpm: c.state.tempo, bars: 1, beats_per_bar: 4, subdivisions: 1 }),
      });
      if (c.state.metronome !== metronome) return;
      metronome.ticks = list(plan?.ticks);
      metronome.cycleSeconds = Number(plan.beats_per_bar) * 60 / Number(plan.bpm);
      if (!metronome.ticks.length || !Number.isFinite(metronome.cycleSeconds)) {
        throw new Error("The local metronome plan was empty or invalid.");
      }
      await context.resume();
      metronome.timer = window.setInterval(() => scheduleClicks(metronome), 25);
      scheduleClicks(metronome);
    } catch (error) {
      if (c.state.metronome === metronome) stopMetronome();
      c.setNotice(`Metronome is unavailable: ${error.message}`, "error");
    }
  }

  function scheduleClicks(metronome) {
    const lag = metronome.context.currentTime - metronome.cycleStart;
    if (lag >= metronome.cycleSeconds) {
      metronome.cycleStart += Math.floor(lag / metronome.cycleSeconds) * metronome.cycleSeconds;
      metronome.tickIndex = 0;
    }
    while (metronome.ticks.length) {
      const tick = metronome.ticks[metronome.tickIndex];
      const scheduledAt = metronome.cycleStart + Number(tick.at_us) / 1e6;
      if (scheduledAt >= metronome.context.currentTime + 0.1) return;
      if (scheduledAt >= metronome.context.currentTime) scheduleClick(metronome.context, scheduledAt, tick.accent);
      metronome.tickIndex += 1;
      if (metronome.tickIndex === metronome.ticks.length) {
        metronome.tickIndex = 0;
        metronome.cycleStart += metronome.cycleSeconds;
      }
    }
  }

  function scheduleClick(context, at, accent) {
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.frequency.value = accent === "bar" ? 1100 : 880;
    gain.gain.setValueAtTime(accent === "bar" ? 0.1 : 0.07, at);
    gain.gain.exponentialRampToValueAtTime(0.001, at + 0.04);
    oscillator.connect(gain).connect(context.destination);
    oscillator.start(at);
    oscillator.stop(at + 0.05);
  }

  function stopMetronome() {
    if (!c.state.metronome) return;
    window.clearInterval(c.state.metronome.timer);
    c.state.metronome.context.close();
    c.state.metronome = null;
  }

  function restartMetronome() {
    stopMetronome();
    startMetronome();
  }

  return {
    acceptSuggestion, reviewRelation, openRevision, openBookmarkDialog,
    openReviewerSetup, openRenderedDialog, closeDialog, submitDialog,
    updatePractice, importMedia, exportMusic, exportTranscript, action, setTempo,
    toggleRecording, cancelRecording, refreshRecordingUI, uploadCapture,
    toggleTuner, sendTuner, renderTunerReading, toggleMetronome, startMetronome,
    scheduleClicks, scheduleClick, stopMetronome, restartMetronome,
    requireHumanActor, attributedActorId,
  };
}
