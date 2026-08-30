import { humanActors, list } from "/assets/workbench_ui.mjs";

export function createLoading(c) {
  function showLoadingState() { const loading = document.createElement("p"); loading.className = "loading-state"; loading.setAttribute("role", "status"); loading.textContent = "Opening the private lesson workbench…"; c.app.replaceChildren(loading); }
  function applyLoadedState(data, processing) { c.state.data = data; c.state.processing = processing || { runtime: {}, jobs: [] }; if (!humanActors(c.state).some((actor) => actor.id === c.state.authorId)) c.state.authorId = ""; const media = list(data?.media); if (!media.some((item) => item.source_id === c.state.activeSourceId)) c.state.activeSourceId = media[0]?.source_id || ""; c.state.tempo = Number(data?.metronome?.bpm || c.state.tempo || 72); if (!c.state.visibleLaneKinds.size) c.state.visibleLaneKinds = new Set(list(data?.timeline?.lanes).map((lane) => lane.kind)); }
  function startPollingWhenNeeded() { if (list(c.state.processing.jobs).some((job) => ["queued", "running", "cancelling"].includes(job.state))) c.startJobPolling(); }
  async function load({ preservePlayback = false, quiet = false, skipProcessing = false } = {}) { const restore = preservePlayback ? c.snapshotPlayback() : null; if (!quiet && !c.state.data) showLoadingState(); try { const processing = skipProcessing ? Promise.resolve(c.state.processing) : c.request("/api/jobs").catch(() => ({ runtime: {}, jobs: [] })); const [data, jobs] = await Promise.all([c.request("/api/workbench"), processing]); applyLoadedState(data, jobs); c.render(restore); startPollingWhenNeeded(); } catch (error) { c.renderFatalState(error); } }
  return { load };
}
