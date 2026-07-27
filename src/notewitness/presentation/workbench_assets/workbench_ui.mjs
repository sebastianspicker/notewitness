/**
 * NoteWitness workbench UI — thin public barrel.
 * Browser loads modules under /assets/ui/*; Node verify uses the same absolute
 * import paths through its inline registerHooks resolver.
 */

export {
  list,
  escapeHTML,
  encodeId,
  formatTime,
  humanActors,
  humanActor,
  anchor,
  playback,
  itemTime,
  itemDuration,
  itemSource,
  sourceDurationSeconds,
  timelineTicks,
  reviewItems,
  transcriptItems,
  sourceName,
  mediaCount,
  isMultiSource,
  formatMusicalSelector,
  hasMusicalTime,
  musicalTimeAt,
  renderDualClocks,
} from "/assets/ui/utils.mjs";

export {
  renderWorkbench,
  renderNotice,
} from "/assets/ui/shell.mjs";

export {
  renderProcessing,
  processingTerminalTransition,
} from "/assets/ui/processing.mjs";

export { renderTimeline } from "/assets/ui/timeline.mjs";
export { renderPanel } from "/assets/ui/panels.mjs";
export { renderTransport } from "/assets/ui/transport.mjs";
