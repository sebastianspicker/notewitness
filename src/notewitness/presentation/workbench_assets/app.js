import { createActions } from "/assets/js/actions.mjs";
import { createApi } from "/assets/js/api.mjs";
import { createEventHandlers } from "/assets/js/app_events.mjs";
import { createLoading } from "/assets/js/app_loading.mjs";
import { createRendering } from "/assets/js/app_rendering.mjs";
import { createWorkbenchState } from "/assets/js/app_state.mjs";
import { createPlayback } from "/assets/js/playback.mjs";
import { createProcessing } from "/assets/js/processing.mjs";

const state = createWorkbenchState();
const c = { state, app: document.querySelector("#app") };
Object.assign(c, createApi(c), createRendering(c), createPlayback(c));
Object.assign(c, createLoading(c), createProcessing(c));
Object.assign(c, createActions(c));
const events = createEventHandlers(c);

c.app.addEventListener("click", events.handleClick);
c.app.addEventListener("change", events.handleChange);
c.app.addEventListener("input", events.handleInput);
c.app.addEventListener("submit", events.handleSubmit);
c.app.addEventListener("cancel", (event) => {
  if (event.target.matches("[data-editor-dialog]")) { event.preventDefault(); c.closeDialog(); }
});
document.addEventListener("keydown", events.handleKeydown);
window.addEventListener("beforeunload", () => {
  c.stopJobPolling(); state.tuner?.stop(); c.stopMetronome();
  state.recorder?.stream?.getTracks().forEach((track) => track.stop());
});

c.load();
