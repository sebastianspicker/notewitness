"""Durable, single-worker orchestration for browser-requested local processing."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import errno
from enum import StrEnum
import fcntl
import os
from pathlib import Path, PurePosixPath
import sqlite3
import stat
import threading
import time
from typing import Callable, Iterator, Mapping, Protocol
from uuid import uuid4

from notewitness.project_store import ProjectStore


MAX_WORKBENCH_JOBS = 100
MAX_WORKBENCH_JOB_ATTEMPTS = 100
MAX_STATUS_MESSAGE_CHARS = 240
# Local tools use a five-second TERM grace period before SIGKILL.  The worker
# needs enough time to observe cancellation, reap that process, publish the
# terminal job state, and leave its loop.  This remains deliberately bounded:
# retaining the ownership lock is safer than letting a second service overlap
# a still-running local tool.
WORKBENCH_SHUTDOWN_WAIT_SECONDS = 12.0
_FILE_MODE = 0o600
_SCHEMA = """
CREATE TABLE IF NOT EXISTS workbench_jobs (
  job_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  source_id TEXT NOT NULL,
  state TEXT NOT NULL,
  progress_percent INTEGER NOT NULL,
  status_message TEXT NOT NULL,
  error_code TEXT,
  retryable INTEGER NOT NULL DEFAULT 0 CHECK (retryable IN (0, 1)),
  cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
  completed_steps TEXT NOT NULL DEFAULT '',
  attempt INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS workbench_jobs_state_idx
ON workbench_jobs(state, created_at, job_id);
"""


class WorkbenchProcessingError(RuntimeError):
    """A GUI processing request violated its bounded local contract."""


class WorkbenchJobKind(StrEnum):
    TRANSCRIPTION = "transcription"
    ANALYSIS = "analysis"
    COMPLETE = "complete"


class WorkbenchJobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class WorkbenchJob:
    job_id: str
    kind: WorkbenchJobKind
    source_id: str
    state: WorkbenchJobState
    progress_percent: int
    status_message: str
    error_code: str | None
    retryable: bool
    cancel_requested: bool
    completed_steps: tuple[str, ...]
    attempt: int
    created_at: str
    updated_at: str

    def as_public_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        payload["state"] = self.state.value
        payload["label"] = {
            WorkbenchJobKind.TRANSCRIPTION: "Speech transcription",
            WorkbenchJobKind.ANALYSIS: "Music and teaching analysis",
            WorkbenchJobKind.COMPLETE: "Complete local pass",
        }[self.kind]
        return payload


class WorkbenchExecutor(Protocol):
    def status(self) -> Mapping[str, object]:
        """Return non-sensitive readiness booleans and explanatory state."""

    def execute(
        self,
        kind: WorkbenchJobKind,
        source_id: str,
        *,
        job_id: str,
        attempt: int,
        cancellation_requested: Callable[[], bool],
        report_progress: Callable[[int, str], None],
        completed_steps: frozenset[str],
        mark_step_completed: Callable[[str], None],
    ) -> None:
        """Run only startup-approved local adapters for one project source."""


class DisabledWorkbenchExecutor:
    def status(self) -> Mapping[str, object]:
        return {
            "analysis_ready": False,
            "complete_ready": False,
            "configured": False,
            "missing_complete_modalities": [
                "speech_transcription",
                "anonymous_diarization",
                "note_transcription",
                "instrument_diarization",
            ],
            "modalities": {
                "speech_transcription": False,
                "anonymous_diarization": False,
                "note_transcription": False,
                "instrument_detection": False,
                "instrument_diarization": False,
            },
            "network_used": False,
            "transcription_ready": False,
        }

    def execute(
        self,
        kind: WorkbenchJobKind,
        source_id: str,
        *,
        job_id: str,
        attempt: int,
        cancellation_requested: Callable[[], bool],
        report_progress: Callable[[int, str], None],
        completed_steps: frozenset[str],
        mark_step_completed: Callable[[str], None],
    ) -> None:
        raise WorkbenchProcessingError("local_runtime_not_configured")


class WorkbenchJobStore:
    """Short-transaction, owner-private state for GUI processing jobs."""

    def __init__(self, database_path: Path) -> None:
        self.path = Path(os.path.abspath(os.fspath(database_path)))
        self._prepare_path()
        with self._connection() as connection:
            connection.executescript(_SCHEMA)

    def enqueue(self, kind: WorkbenchJobKind, source_id: str) -> WorkbenchJob:
        _source_identifier(source_id)
        now = _now()
        job_id = f"job:workbench-{uuid4().hex}"
        with self._transaction() as connection:
            self._require_no_active_job(connection)
            connection.execute(
                """INSERT INTO workbench_jobs (
                    job_id, kind, source_id, state, progress_percent, status_message,
                    error_code, retryable, cancel_requested, completed_steps, attempt,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?, NULL, 0, 0, '', 1, ?, ?)""",
                (
                    job_id,
                    kind.value,
                    source_id,
                    WorkbenchJobState.QUEUED.value,
                    "Waiting for the local worker",
                    now,
                    now,
                ),
            )
            return self._required(connection, job_id)

    def get(self, job_id: str) -> WorkbenchJob | None:
        _job_identifier(job_id)
        with self._connection() as connection:
            return self._select(connection, job_id)

    def list(self, limit: int = MAX_WORKBENCH_JOBS) -> tuple[WorkbenchJob, ...]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM workbench_jobs ORDER BY created_at DESC, job_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(_row_to_job(row) for row in rows)

    def claim_next(self) -> WorkbenchJob | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT job_id FROM workbench_jobs WHERE state = ? "
                "ORDER BY created_at, job_id LIMIT 1",
                (WorkbenchJobState.QUEUED.value,),
            ).fetchone()
            if row is None:
                return None
            job_id = str(row["job_id"])
            connection.execute(
                "UPDATE workbench_jobs SET state = ?, progress_percent = 1, "
                "status_message = ?, updated_at = ? WHERE job_id = ? AND state = ?",
                (
                    WorkbenchJobState.RUNNING.value,
                    "Starting approved local tools",
                    _now(),
                    job_id,
                    WorkbenchJobState.QUEUED.value,
                ),
            )
            return self._required(connection, job_id)

    def progress(self, job_id: str, percent: int, message: str) -> WorkbenchJob:
        if not isinstance(percent, int) or isinstance(percent, bool) or not 1 <= percent <= 99:
            raise ValueError("progress percent must be between 1 and 99")
        normalized = _status_message(message)
        with self._transaction() as connection:
            job = self._required(connection, job_id)
            if job.state not in {WorkbenchJobState.RUNNING, WorkbenchJobState.CANCELLING}:
                return job
            connection.execute(
                "UPDATE workbench_jobs SET progress_percent = ?, status_message = ?, "
                "updated_at = ? WHERE job_id = ?",
                (percent, normalized, _now(), job_id),
            )
            return self._required(connection, job_id)

    def complete(self, job_id: str) -> WorkbenchJob:
        with self._transaction() as connection:
            job = self._required(connection, job_id)
            steps_complete = _required_steps(job.kind).issubset(job.completed_steps)
            if not steps_complete and not job.cancel_requested:
                raise WorkbenchProcessingError("job_completion_checkpoint_missing")
            state = (
                WorkbenchJobState.COMPLETED
                if steps_complete
                else WorkbenchJobState.CANCELLED
            )
            message = (
                "Completed before cancellation took effect; local evidence is ready for review"
                if job.cancel_requested and steps_complete
                else "Local evidence is ready for review"
                if steps_complete
                else _cancelled_message(job.completed_steps)
            )
            connection.execute(
                "UPDATE workbench_jobs SET state = ?, progress_percent = ?, status_message = ?, "
                "error_code = NULL, retryable = ?, updated_at = ? WHERE job_id = ?",
                (state.value, 100 if state is WorkbenchJobState.COMPLETED else job.progress_percent,
                 message, 0 if state is WorkbenchJobState.COMPLETED else 1, _now(), job_id),
            )
            return self._required(connection, job_id)

    def mark_step_completed(self, job_id: str, step: str) -> WorkbenchJob:
        if step not in {"transcription", "analysis"}:
            raise ValueError("workbench processing step is unsupported")
        with self._transaction() as connection:
            job = self._required(connection, job_id)
            if job.state not in {WorkbenchJobState.RUNNING, WorkbenchJobState.CANCELLING}:
                return job
            steps = tuple(sorted({*job.completed_steps, step}))
            connection.execute(
                "UPDATE workbench_jobs SET completed_steps = ?, updated_at = ? WHERE job_id = ?",
                (",".join(steps), _now(), job_id),
            )
            return self._required(connection, job_id)

    def fail(self, job_id: str, error_code: str) -> WorkbenchJob:
        code = _error_code(error_code)
        with self._transaction() as connection:
            job = self._required(connection, job_id)
            steps_complete = _required_steps(job.kind).issubset(job.completed_steps)
            state = (
                WorkbenchJobState.COMPLETED
                if job.cancel_requested and steps_complete
                else WorkbenchJobState.CANCELLED
                if job.cancel_requested
                else WorkbenchJobState.FAILED
            )
            message = (
                "Completed before cancellation took effect; local evidence is ready for review"
                if state is WorkbenchJobState.COMPLETED
                else _cancelled_message(job.completed_steps)
                if state is WorkbenchJobState.CANCELLED
                else "Local processing stopped; the run can be retried"
            )
            connection.execute(
                "UPDATE workbench_jobs SET state = ?, status_message = ?, error_code = ?, "
                "retryable = ?, updated_at = ? WHERE job_id = ?",
                (
                    state.value,
                    message,
                    None if job.cancel_requested else code,
                    0 if state is WorkbenchJobState.COMPLETED else 1,
                    _now(),
                    job_id,
                ),
            )
            return self._required(connection, job_id)

    def request_cancellation(self, job_id: str) -> WorkbenchJob:
        with self._transaction() as connection:
            job = self._required(connection, job_id)
            if job.state is WorkbenchJobState.QUEUED:
                connection.execute(
                    "UPDATE workbench_jobs SET state = ?, cancel_requested = 1, "
                    "status_message = ?, retryable = 1, updated_at = ? WHERE job_id = ?",
                    (WorkbenchJobState.CANCELLED.value,
                     "Cancelled before local tools started; retry resumes this local run", _now(), job_id),
                )
            elif job.state is WorkbenchJobState.RUNNING:
                connection.execute(
                    "UPDATE workbench_jobs SET state = ?, cancel_requested = 1, "
                    "status_message = ?, updated_at = ? WHERE job_id = ?",
                    (WorkbenchJobState.CANCELLING.value,
                     "Stopping the local process safely", _now(), job_id),
                )
            return self._required(connection, job_id)

    def retry(self, job_id: str) -> WorkbenchJob:
        with self._transaction() as connection:
            job = self._required(connection, job_id)
            if job.state not in {
                WorkbenchJobState.FAILED,
                WorkbenchJobState.INTERRUPTED,
                WorkbenchJobState.CANCELLED,
            }:
                raise WorkbenchProcessingError("job_not_retryable")
            if not job.retryable:
                raise WorkbenchProcessingError("job_not_retryable")
            if job.attempt >= MAX_WORKBENCH_JOB_ATTEMPTS:
                raise WorkbenchProcessingError("job_attempt_limit_reached")
            self._require_no_active_job(connection)
            connection.execute(
                "UPDATE workbench_jobs SET state = ?, progress_percent = 0, status_message = ?, "
                "error_code = NULL, retryable = 0, cancel_requested = 0, attempt = attempt + 1, "
                "updated_at = ? WHERE job_id = ?",
                (WorkbenchJobState.QUEUED.value, "Waiting for the local worker", _now(), job_id),
            )
            return self._required(connection, job_id)

    def recover_interrupted(self) -> int:
        with self._transaction() as connection:
            return connection.execute(
                "UPDATE workbench_jobs SET state = ?, status_message = ?, retryable = 1, "
                "error_code = ?, updated_at = ? WHERE state IN (?, ?)",
                (
                    WorkbenchJobState.INTERRUPTED.value,
                    "The previous workbench closed during processing; retry is safe",
                    "workbench_interrupted",
                    _now(),
                    WorkbenchJobState.RUNNING.value,
                    WorkbenchJobState.CANCELLING.value,
                ),
            ).rowcount

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self._prepare_path()
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            yield connection
        finally:
            connection.close()
            self._private_sidecars()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")

    def _prepare_path(self) -> None:
        parent = self.path.parent
        metadata = parent.stat()
        if not parent.is_dir() or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise WorkbenchProcessingError("job_store_parent_not_private")
        if self.path.exists() or self.path.is_symlink():
            info = self.path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise WorkbenchProcessingError("job_store_not_regular")
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise WorkbenchProcessingError("job_store_not_private")
        else:
            descriptor = os.open(
                self.path,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                _FILE_MODE,
            )
            os.close(descriptor)

    def _private_sidecars(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            self._secure_sidecar(Path(f"{self.path}{suffix}"))

    @staticmethod
    def _secure_sidecar(candidate: Path) -> None:
        """Apply private permissions to the opened SQLite file, never its pathname."""
        try:
            descriptor = os.open(
                candidate,
                os.O_RDONLY
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NONBLOCK", 0),
            )
        except FileNotFoundError:
            # SQLite may remove WAL and SHM files while a connection is closing.
            return
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise WorkbenchProcessingError("job_store_not_regular") from exc
            raise WorkbenchProcessingError("job_store_sidecar_access_failed") from exc
        failure: BaseException | None = None
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise WorkbenchProcessingError("job_store_not_regular")
            if metadata.st_uid != os.getuid():
                raise WorkbenchProcessingError("job_store_not_private")
            os.fchmod(descriptor, _FILE_MODE)
        except WorkbenchProcessingError as exc:
            failure = exc
            raise
        except OSError as exc:
            failure = exc
            raise WorkbenchProcessingError("job_store_sidecar_access_failed") from exc
        finally:
            try:
                os.close(descriptor)
            except OSError as exc:
                if failure is None:
                    raise WorkbenchProcessingError("job_store_sidecar_access_failed") from exc

    @staticmethod
    def _select(connection: sqlite3.Connection, job_id: str) -> WorkbenchJob | None:
        row = connection.execute(
            "SELECT * FROM workbench_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return None if row is None else _row_to_job(row)

    def _required(self, connection: sqlite3.Connection, job_id: str) -> WorkbenchJob:
        _job_identifier(job_id)
        job = self._select(connection, job_id)
        if job is None:
            raise WorkbenchProcessingError("job_not_found")
        return job

    @staticmethod
    def _require_no_active_job(connection: sqlite3.Connection) -> None:
        active = connection.execute(
            "SELECT 1 FROM workbench_jobs WHERE state IN (?, ?, ?) LIMIT 1",
            (
                WorkbenchJobState.QUEUED.value,
                WorkbenchJobState.RUNNING.value,
                WorkbenchJobState.CANCELLING.value,
            ),
        ).fetchone()
        if active is not None:
            raise WorkbenchProcessingError("processing_job_already_active")


class _ProjectProcessingLock:
    """A private, process-wide advisory lock held for one service lifetime."""

    _owned_paths: set[str] = set()
    _owned_paths_lock = threading.Lock()

    def __init__(self, path: Path) -> None:
        self.path = Path(os.path.abspath(os.fspath(path)))
        self._descriptor: int | None = None

    def acquire(self) -> None:
        self._prepare_path()
        descriptor = os.open(
            self.path,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            _FILE_MODE,
        )
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
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
        if not parent.is_dir() or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
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
        _require_project_media(self.project_root, source_id)
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
        _require_project_media(self.project_root, current.source_id)
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
            thread.join(timeout=WORKBENCH_SHUTDOWN_WAIT_SECONDS)
            if thread.is_alive():
                raise WorkbenchProcessingError("workbench_processing_shutdown_timeout")
        self._service_lock.release()

    def _require_ready(self, kind: WorkbenchJobKind) -> None:
        status = self.executor.status()
        transcription = status.get("transcription_ready") is True
        analysis = status.get("analysis_ready") is True
        ready = {
            WorkbenchJobKind.TRANSCRIPTION: transcription,
            WorkbenchJobKind.ANALYSIS: analysis,
            WorkbenchJobKind.COMPLETE: status.get("complete_ready") is True,
        }[kind]
        if not ready:
            raise WorkbenchProcessingError("selected_local_runtime_not_ready")

    def _work_loop(self) -> None:
        while True:
            if self._stopping:
                return
            job = self.store.claim_next()
            if job is None:
                with self._condition:
                    self._condition.wait(timeout=0.5)
                continue
            self._active_job_id = job.job_id
            try:
                self.executor.execute(
                    job.kind,
                    job.source_id,
                    job_id=job.job_id,
                    attempt=job.attempt,
                    cancellation_requested=lambda: self._cancelled(job.job_id),
                    report_progress=lambda percent, message: self.store.progress(
                        job.job_id, percent, message
                    ),
                    completed_steps=frozenset(job.completed_steps),
                    mark_step_completed=lambda step: self.store.mark_step_completed(
                        job.job_id, step
                    ),
                )
                self.store.complete(job.job_id)
            except Exception as exc:
                self.store.fail(job.job_id, _safe_exception_code(exc))
            finally:
                self._active_job_id = None

    def _cancelled(self, job_id: str) -> bool:
        job = self.store.get(job_id)
        return self._stopping or job is None or job.cancel_requested


def _row_to_job(row: sqlite3.Row) -> WorkbenchJob:
    return WorkbenchJob(
        job_id=str(row["job_id"]),
        kind=WorkbenchJobKind(row["kind"]),
        source_id=str(row["source_id"]),
        state=WorkbenchJobState(row["state"]),
        progress_percent=int(row["progress_percent"]),
        status_message=str(row["status_message"]),
        error_code=str(row["error_code"]) if row["error_code"] is not None else None,
        retryable=bool(row["retryable"]),
        cancel_requested=bool(row["cancel_requested"]),
        completed_steps=tuple(
            step for step in str(row["completed_steps"]).split(",") if step
        ),
        attempt=int(row["attempt"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _require_project_media(project_root: Path, source_id: str) -> None:
    _source_identifier(source_id)
    payload = ProjectStore(project_root).load().payload
    matches = [item for item in payload["sources"] if item.get("id") == source_id]
    if len(matches) != 1:
        raise WorkbenchProcessingError("media_source_not_found")
    uri = matches[0].get("uri")
    relative = PurePosixPath(str(uri)) if isinstance(uri, str) else PurePosixPath()
    if (
        relative.is_absolute()
        or "\\" in str(uri)
        or not relative.parts
        or relative.parts[0] != "media"
        or len(relative.parts) != 2
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise WorkbenchProcessingError("source_is_not_project_media")


def _job_identifier(value: str) -> None:
    if not isinstance(value, str) or not value.startswith("job:workbench-") or len(value) > 128:
        raise WorkbenchProcessingError("invalid_job_id")


def _source_identifier(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise WorkbenchProcessingError("invalid_source_id")


def _status_message(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_STATUS_MESSAGE_CHARS:
        raise ValueError("status message must be bounded non-empty text")
    return value.strip()


def _error_code(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 96
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in value)
    ):
        return "local_processing_failed"
    return value


def _safe_exception_code(exc: Exception) -> str:
    name = type(exc).__name__
    converted = "".join(
        ("_" + character.lower()) if character.isupper() else character
        for character in name
    ).lstrip("_")
    return _error_code(converted)


def _required_steps(kind: WorkbenchJobKind) -> frozenset[str]:
    return {
        WorkbenchJobKind.TRANSCRIPTION: frozenset({"transcription"}),
        WorkbenchJobKind.ANALYSIS: frozenset({"analysis"}),
        WorkbenchJobKind.COMPLETE: frozenset({"transcription", "analysis"}),
    }[kind]


def _cancelled_message(completed_steps: tuple[str, ...]) -> str:
    completed = frozenset(completed_steps)
    if completed == {"transcription"}:
        return "Cancelled after speech transcription; completed evidence remains ready for review"
    if completed == {"analysis"}:
        return "Cancelled after music analysis; completed evidence remains ready for review"
    if completed:
        return "Cancelled after a completed stage; saved evidence remains ready for review"
    return "Cancelled before publishing new evidence"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
