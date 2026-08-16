/** Local processing job queue: poll, enqueue, cancel, retry. */

import {
  list,
  processingTerminalTransition,
} from "/assets/workbench_ui.mjs";

const JOB_POLL_MILLISECONDS = 2000;

/**
 * @param {object} c Controller context (state, app, request, actionHeaders, …).
 */
export function createProcessing(c) {
  async function enqueueJob() {
    const selector = c.app.querySelector("[data-run-kind]");
    const kind = selector?.value;
    if (!kind || !c.state.activeSourceId) return;
    const saved = await c.runMutation("enqueue-job", "Queueing local processing", async () => {
      const queued = await c.request("/api/jobs", {
        method: "POST",
        headers: c.actionHeaders(),
        body: JSON.stringify({ kind, source_id: c.state.activeSourceId }),
      });
      const current = list(c.state.processing?.jobs).filter((job) => job.job_id !== queued.job_id);
      c.state.processing = { ...(c.state.processing || {}), jobs: [queued, ...current] };
      c.refreshProcessing();
      await pollJobs({ rethrowErrors: true });
    });
    if (saved) {
      c.setNotice("Local processing queued. You can keep reviewing while it runs.", "success");
      startJobPolling();
    }
  }

  async function jobAction(jobId, actionName) {
    const saved = await c.runMutation(`${actionName}-${jobId}`, `${actionName === "cancel" ? "Cancelling" : "Retrying"} local processing`, async () => {
      await c.request(`/api/jobs/${encodeURIComponent(jobId)}/${actionName}`, {
        method: "POST", headers: c.actionHeaders(), body: JSON.stringify({}),
      });
      await pollJobs({ rethrowErrors: true });
    });
    if (saved) c.setNotice(actionName === "cancel" ? "Cancellation requested." : "Processing queued for a safe retry.", "success");
  }

  async function pollJobs({ rethrowErrors = false } = {}) {
    if (!c.state.data) return;
    try {
      const previous = list(c.state.processing?.jobs);
      const processing = await c.request("/api/jobs");
      c.state.processing = processing || { runtime: {}, jobs: [] };
      const transition = processingTerminalTransition(previous, c.state.processing.jobs);
      if (transition) {
        await c.load({ preservePlayback: true, quiet: true, skipProcessing: true });
        c.setNotice(
          transition === "completed"
            ? "Local processing finished. New machine evidence is ready for review."
            : "Processing stopped after saving part of the evidence. Review it now or resume the remaining stages.",
          transition === "completed" ? "success" : "warning",
        );
      } else c.refreshProcessing();
      if (!list(c.state.processing.jobs).some((job) => ["queued", "running", "cancelling"].includes(job.state))) {
        stopJobPolling();
      }
    } catch (error) {
      stopJobPolling();
      c.setNotice(`Processing status is unavailable: ${error.message}`, "error");
      if (rethrowErrors) throw error;
    }
  }

  function startJobPolling() {
    if (c.state.jobPoll) return;
    c.state.jobPoll = window.setInterval(pollJobs, JOB_POLL_MILLISECONDS);
  }

  function stopJobPolling() {
    window.clearInterval(c.state.jobPoll);
    c.state.jobPoll = 0;
  }

  return {
    enqueueJob,
    jobAction,
    pollJobs,
    startJobPolling,
    stopJobPolling,
  };
}
