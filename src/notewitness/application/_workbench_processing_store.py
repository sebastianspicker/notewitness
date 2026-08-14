"""Private SQLite state store and file safeguards for workbench processing."""

from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import sqlite3
import stat
from typing import Iterator
from uuid import uuid4

from ._workbench_processing_contracts import (
    MAX_WORKBENCH_JOB_ATTEMPTS,
    MAX_WORKBENCH_JOBS,
    WorkbenchJob,
    WorkbenchJobKind,
    WorkbenchJobState,
    WorkbenchProcessingError,
    cancelled_message,
    error_code,
    job_identifier,
    now,
    required_steps,
    source_identifier,
    status_message,
)


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


class WorkbenchJobStore:
    """Short-transaction, owner-private state for GUI processing jobs."""

    def __init__(self, database_path: Path) -> None:
        self.path = Path(os.path.abspath(os.fspath(database_path)))
        self._prepare_path()
        with self._connection() as connection:
            connection.executescript(_SCHEMA)

    def enqueue(self, kind: WorkbenchJobKind, source_id: str) -> WorkbenchJob:
        source_identifier(source_id)
        timestamp = now()
        job_id = f"job:workbench-{uuid4().hex}"
        with self._transaction() as connection:
            self._require_no_active_job(connection)
            connection.execute(
                """INSERT INTO workbench_jobs (
                    job_id, kind, source_id, state, progress_percent, status_message,
                    error_code, retryable, cancel_requested, completed_steps, attempt,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?, NULL, 0, 0, '', 1, ?, ?)""",
                (job_id, kind.value, source_id, WorkbenchJobState.QUEUED.value,
                 "Waiting for the local worker", timestamp, timestamp),
            )
            return self._required(connection, job_id)

    def get(self, job_id: str) -> WorkbenchJob | None:
        job_identifier(job_id)
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
                (WorkbenchJobState.RUNNING.value, "Starting approved local tools", now(),
                 job_id, WorkbenchJobState.QUEUED.value),
            )
            return self._required(connection, job_id)

    def progress(self, job_id: str, percent: int, message: str) -> WorkbenchJob:
        if not isinstance(percent, int) or isinstance(percent, bool) or not 1 <= percent <= 99:
            raise ValueError("progress percent must be between 1 and 99")
        normalized = status_message(message)
        with self._transaction() as connection:
            job = self._required(connection, job_id)
            if job.state not in {WorkbenchJobState.RUNNING, WorkbenchJobState.CANCELLING}:
                return job
            connection.execute(
                "UPDATE workbench_jobs SET progress_percent = ?, status_message = ?, "
                "updated_at = ? WHERE job_id = ?",
                (percent, normalized, now(), job_id),
            )
            return self._required(connection, job_id)

    def complete(self, job_id: str) -> WorkbenchJob:
        with self._transaction() as connection:
            job = self._required(connection, job_id)
            steps_complete = required_steps(job.kind).issubset(job.completed_steps)
            if not steps_complete and not job.cancel_requested:
                raise WorkbenchProcessingError("job_completion_checkpoint_missing")
            state = WorkbenchJobState.COMPLETED if steps_complete else WorkbenchJobState.CANCELLED
            message = _completion_message(job, steps_complete)
            connection.execute(
                "UPDATE workbench_jobs SET state = ?, progress_percent = ?, status_message = ?, "
                "error_code = NULL, retryable = ?, updated_at = ? WHERE job_id = ?",
                (state.value, 100 if state is WorkbenchJobState.COMPLETED else job.progress_percent,
                 message, 0 if state is WorkbenchJobState.COMPLETED else 1, now(), job_id),
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
                (",".join(steps), now(), job_id),
            )
            return self._required(connection, job_id)

    def fail(self, job_id: str, error_code_value: str) -> WorkbenchJob:
        code = error_code(error_code_value)
        with self._transaction() as connection:
            job = self._required(connection, job_id)
            state = _failure_state(job)
            connection.execute(
                "UPDATE workbench_jobs SET state = ?, status_message = ?, error_code = ?, "
                "retryable = ?, updated_at = ? WHERE job_id = ?",
                (state.value, _failure_message(job, state),
                 None if job.cancel_requested else code,
                 0 if state is WorkbenchJobState.COMPLETED else 1, now(), job_id),
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
                     "Cancelled before local tools started; retry resumes this local run", now(), job_id),
                )
            elif job.state is WorkbenchJobState.RUNNING:
                connection.execute(
                    "UPDATE workbench_jobs SET state = ?, cancel_requested = 1, "
                    "status_message = ?, updated_at = ? WHERE job_id = ?",
                    (WorkbenchJobState.CANCELLING.value,
                     "Stopping the local process safely", now(), job_id),
                )
            return self._required(connection, job_id)

    def retry(self, job_id: str) -> WorkbenchJob:
        with self._transaction() as connection:
            job = self._required(connection, job_id)
            if job.state not in {WorkbenchJobState.FAILED, WorkbenchJobState.INTERRUPTED,
                                 WorkbenchJobState.CANCELLED} or not job.retryable:
                raise WorkbenchProcessingError("job_not_retryable")
            if job.attempt >= MAX_WORKBENCH_JOB_ATTEMPTS:
                raise WorkbenchProcessingError("job_attempt_limit_reached")
            self._require_no_active_job(connection)
            connection.execute(
                "UPDATE workbench_jobs SET state = ?, progress_percent = 0, status_message = ?, "
                "error_code = NULL, retryable = 0, cancel_requested = 0, attempt = attempt + 1, "
                "updated_at = ? WHERE job_id = ?",
                (WorkbenchJobState.QUEUED.value, "Waiting for the local worker", now(), job_id),
            )
            return self._required(connection, job_id)

    def recover_interrupted(self) -> int:
        with self._transaction() as connection:
            return connection.execute(
                "UPDATE workbench_jobs SET state = ?, status_message = ?, retryable = 1, "
                "error_code = ?, updated_at = ? WHERE state IN (?, ?)",
                (WorkbenchJobState.INTERRUPTED.value,
                 "The previous workbench closed during processing; retry is safe",
                 "workbench_interrupted", now(), WorkbenchJobState.RUNNING.value,
                 WorkbenchJobState.CANCELLING.value),
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
        parent_is_private = (
            parent.is_dir()
            and metadata.st_uid == os.getuid()
            and not stat.S_IMODE(metadata.st_mode) & 0o077
        )
        if not parent_is_private:
            raise WorkbenchProcessingError("job_store_parent_not_private")
        if self.path.exists() or self.path.is_symlink():
            info = self.path.lstat()
            database_is_private = (
                not stat.S_ISLNK(info.st_mode)
                and stat.S_ISREG(info.st_mode)
                and info.st_uid == os.getuid()
                and not stat.S_IMODE(info.st_mode) & 0o077
            )
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise WorkbenchProcessingError("job_store_not_regular")
            if not database_is_private:
                raise WorkbenchProcessingError("job_store_not_private")
            return
        descriptor = os.open(
            self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, _FILE_MODE,
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
                os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NONBLOCK", 0),
            )
        except FileNotFoundError:
            return
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise WorkbenchProcessingError("job_store_not_regular") from exc
            raise WorkbenchProcessingError("job_store_sidecar_access_failed") from exc
        _secure_opened_sidecar(descriptor)

    @staticmethod
    def _select(connection: sqlite3.Connection, job_id: str) -> WorkbenchJob | None:
        row = connection.execute("SELECT * FROM workbench_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return None if row is None else _row_to_job(row)

    def _required(self, connection: sqlite3.Connection, job_id: str) -> WorkbenchJob:
        job_identifier(job_id)
        job = self._select(connection, job_id)
        if job is None:
            raise WorkbenchProcessingError("job_not_found")
        return job

    @staticmethod
    def _require_no_active_job(connection: sqlite3.Connection) -> None:
        active = connection.execute(
            "SELECT 1 FROM workbench_jobs WHERE state IN (?, ?, ?) LIMIT 1",
            (WorkbenchJobState.QUEUED.value, WorkbenchJobState.RUNNING.value,
             WorkbenchJobState.CANCELLING.value),
        ).fetchone()
        if active is not None:
            raise WorkbenchProcessingError("processing_job_already_active")


def _completion_message(job: WorkbenchJob, steps_complete: bool) -> str:
    if job.cancel_requested and steps_complete:
        return "Completed before cancellation took effect; local evidence is ready for review"
    if steps_complete:
        return "Local evidence is ready for review"
    return cancelled_message(job.completed_steps)


def _failure_state(job: WorkbenchJob) -> WorkbenchJobState:
    if not job.cancel_requested:
        return WorkbenchJobState.FAILED
    return (WorkbenchJobState.COMPLETED if required_steps(job.kind).issubset(job.completed_steps)
            else WorkbenchJobState.CANCELLED)


def _failure_message(job: WorkbenchJob, state: WorkbenchJobState) -> str:
    if state is WorkbenchJobState.COMPLETED:
        return "Completed before cancellation took effect; local evidence is ready for review"
    if state is WorkbenchJobState.CANCELLED:
        return cancelled_message(job.completed_steps)
    return "Local processing stopped; the run can be retried"


def _secure_opened_sidecar(descriptor: int) -> None:
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


def _row_to_job(row: sqlite3.Row) -> WorkbenchJob:
    return WorkbenchJob(
        job_id=str(row["job_id"]), kind=WorkbenchJobKind(row["kind"]),
        source_id=str(row["source_id"]), state=WorkbenchJobState(row["state"]),
        progress_percent=int(row["progress_percent"]), status_message=str(row["status_message"]),
        error_code=str(row["error_code"]) if row["error_code"] is not None else None,
        retryable=bool(row["retryable"]), cancel_requested=bool(row["cancel_requested"]),
        completed_steps=tuple(step for step in str(row["completed_steps"]).split(",") if step),
        attempt=int(row["attempt"]), created_at=str(row["created_at"]), updated_at=str(row["updated_at"]),
    )
