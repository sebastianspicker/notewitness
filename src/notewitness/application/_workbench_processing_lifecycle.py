"""Private service, project lock, and worker lifecycle for workbench jobs."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import stat
import threading

from notewitness.project_store import ProjectStore

from ._workbench_processing_contracts import (
    DisabledWorkbenchExecutor,
    WorkbenchExecutor,
    WorkbenchJob,
    WorkbenchJobKind,
    WorkbenchJobState,
    WorkbenchProcessingError,
    require_project_media,
    safe_exception_code,
)
from ._workbench_processing_store import WorkbenchJobStore


_FILE_MODE = 0o600


def _shutdown_wait_seconds() -> float:
    """Resolve the public facade value so its established patch seam remains live."""

    from . import workbench_processing

    return workbench_processing.WORKBENCH_SHUTDOWN_WAIT_SECONDS


class _ProjectProcessingLock:
    """A private, process-wide advisory lock held for one service lifetime."""

    _owned_paths: set[str] = set()
    _owned_paths_lock = threading.Lock()

    def __init__(self, path: Path) -> None:
        self.path = Path(os.path.abspath(os.fspath(path)))
        self._descriptor: int | None = None

    def acquire(self) -> None:
        self._prepare_path()
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, _FILE_MODE)
        try:
            metadata = os.fstat(descriptor)
            is_private_regular_file = (
                stat.S_ISREG(metadata.st_mode)
                and metadata.st_uid == os.getuid()
                and not stat.S_IMODE(metadata.st_mode) & 0o077
            )
            if not is_private_regular_file:
                raise WorkbenchProcessingError("processing_lock_not_private")
            with self._owned_paths_lock:
                if os.fspath(self.path) in self._owned_paths:
                    raise WorkbenchProcessingError("workbench_processing_service_already_active")
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise WorkbenchProcessingError(
                        "workbench_processing_service_already_active"
                    ) from exc
                self._owned_paths.add(os.fspath(self.path))
            self._descriptor = descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def release(self) -> None:
        descriptor = self._descriptor
        if descriptor is None:
            return
        self._descriptor = None
        with self._owned_paths_lock:
            self._owned_paths.discard(os.fspath(self.path))
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _prepare_path(self) -> None:
        parent = self.path.parent
        metadata = parent.stat()
        parent_is_private = (
            parent.is_dir()
            and metadata.st_uid == os.getuid()
            and not stat.S_IMODE(metadata.st_mode) & 0o077
        )
        if not parent_is_private:
            raise WorkbenchProcessingError("processing_lock_parent_not_private")
        if self.path.exists() or self.path.is_symlink():
            info = self.path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise WorkbenchProcessingError("processing_lock_not_regular")
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise WorkbenchProcessingError("processing_lock_not_private")


class WorkbenchProcessingService:
    """Serialize local engines while keeping HTTP request threads responsive."""

    def __init__(
        self,
        project_root: str | Path,
        executor: WorkbenchExecutor | None = None,
        *,
        start_worker: bool = True,
    ) -> None:
        self.project_root = ProjectStore(project_root).root
        runs = ProjectStore(self.project_root).ensure_private_directory("runs")
        self._service_lock = _ProjectProcessingLock(runs / "workbench-processing.lock")
        self._service_lock.acquire()
        try:
            self.store = WorkbenchJobStore(runs / "workbench-jobs.sqlite")
            self.store.recover_interrupted()
        except BaseException:
            self._service_lock.release()
            raise
        self.executor = executor or DisabledWorkbenchExecutor()
        self._condition = threading.Condition()
        self._stopping = False
        self._active_job_id: str | None = None
        self._thread: threading.Thread | None = None
        if start_worker:
            self._thread = threading.Thread(
                target=self._work_loop,
                name="notewitness-local-processing",
                daemon=False,
            )
            self._thread.start()

    def snapshot(self) -> dict[str, object]:
        return {
            "jobs": [job.as_public_dict() for job in self.store.list()],
            "runtime": dict(self.executor.status()),
        }

    def enqueue(self, kind: str, source_id: str) -> WorkbenchJob:
        try:
            selected = WorkbenchJobKind(kind)
        except ValueError as exc:
            raise WorkbenchProcessingError("unsupported_job_kind") from exc
        self._require_ready(selected)
        require_project_media(self.project_root, source_id)
        job = self.store.enqueue(selected, source_id)
        with self._condition:
            self._condition.notify_all()
        return job

    def cancel(self, job_id: str) -> WorkbenchJob:
        job = self.store.request_cancellation(job_id)
        with self._condition:
            self._condition.notify_all()
        return job

    def retry(self, job_id: str) -> WorkbenchJob:
        current = self.store.get(job_id)
        if current is None:
            raise WorkbenchProcessingError("job_not_found")
        self._require_ready(current.kind)
        require_project_media(self.project_root, current.source_id)
        job = self.store.retry(job_id)
        with self._condition:
            self._condition.notify_all()
        return job

    def close(self) -> None:
        if self._active_job_id is not None:
            self.store.request_cancellation(self._active_job_id)
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=_shutdown_wait_seconds())
            if thread.is_alive():
                raise WorkbenchProcessingError("workbench_processing_shutdown_timeout")
        self._service_lock.release()

    def _require_ready(self, kind: WorkbenchJobKind) -> None:
        status = self.executor.status()
        ready = {
            WorkbenchJobKind.TRANSCRIPTION: status.get("transcription_ready") is True,
            WorkbenchJobKind.ANALYSIS: status.get("analysis_ready") is True,
            WorkbenchJobKind.COMPLETE: status.get("complete_ready") is True,
        }[kind]
        if not ready:
            raise WorkbenchProcessingError("selected_local_runtime_not_ready")

    def _work_loop(self) -> None:
        while not self._stopping:
            job = self.store.claim_next()
            if job is None:
                with self._condition:
                    self._condition.wait(timeout=0.5)
                continue
            self._active_job_id = job.job_id
            try:
                self.executor.execute(
                    job.kind, job.source_id, job_id=job.job_id, attempt=job.attempt,
                    cancellation_requested=lambda: self._cancelled(job.job_id),
                    report_progress=lambda percent, message: self.store.progress(job.job_id, percent, message),
                    completed_steps=frozenset(job.completed_steps),
                    mark_step_completed=lambda step: self.store.mark_step_completed(job.job_id, step),
                )
                self.store.complete(job.job_id)
            except Exception as exc:
                self.store.fail(job.job_id, safe_exception_code(exc))
            finally:
                self._active_job_id = None

    def _cancelled(self, job_id: str) -> bool:
        job = self.store.get(job_id)
        return self._stopping or job is None or job.cancel_requested
