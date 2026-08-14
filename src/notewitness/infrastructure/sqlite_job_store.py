"""Crash-safe, owner-private SQLite persistence for bounded analysis jobs."""

from __future__ import annotations

from contextlib import contextmanager
import errno
import math
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time
from typing import Iterator

from notewitness.domain.analysis import AnalysisStage, JobState
from notewitness.domain.jobs import (
    AnalysisJobSpec,
    DurableJob,
    MAX_ARTIFACT_ID_CHARS,
    MAX_JOB_ID_CHARS,
)
from notewitness.infrastructure.sqlite_job_store_records import (
    row_to_job as _row_to_job,
    spec_values as _spec_values,
)


_FILE_MODE = 0o600
_DIRECTORY_MODE = 0o700
_SCHEMA = """
CREATE TABLE IF NOT EXISTS analysis_jobs (
  job_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  source_sha256 TEXT NOT NULL,
  stages_json TEXT NOT NULL,
  spans_json TEXT NOT NULL,
  adapter_fingerprint_sha256 TEXT NOT NULL,
  runtime_fingerprint_sha256 TEXT NOT NULL,
  settings_fingerprint_sha256 TEXT NOT NULL,
  score_sha256 TEXT,
  created_at TEXT NOT NULL,
  state TEXT NOT NULL,
  owner_id TEXT,
  lease_expires_at REAL,
  cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
  checkpoint_stage TEXT,
  completed_span_count INTEGER NOT NULL DEFAULT 0,
  continuation_token TEXT,
  last_artifact_id TEXT,
  failure_reason TEXT
);
CREATE INDEX IF NOT EXISTS analysis_jobs_claim_idx
ON analysis_jobs(state, created_at);
"""


class JobStoreError(RuntimeError):
    """The job store cannot be safely used or its state is invalid."""


class JobConflictError(JobStoreError):
    """A lease, state, or immutable identity precondition was not met."""


class SQLiteJobStore:
    """A short-transaction job store; callers never supply SQL fragments."""

    def __init__(self, database_path: str | Path, *, busy_timeout_ms: int = 5_000) -> None:
        self.path = _trusted_path(Path(os.path.abspath(os.fspath(database_path))))
        if (
            not isinstance(busy_timeout_ms, int)
            or isinstance(busy_timeout_ms, bool)
            or not 1 <= busy_timeout_ms <= 60_000
        ):
            raise ValueError("busy_timeout_ms must be between 1 and 60000.")
        self._busy_timeout_ms = busy_timeout_ms
        self._prepare_path()
        with self._connection() as connection:
            connection.executescript(_SCHEMA)
            self._private_sidecars()

    def close(self) -> None:
        """Compatibility no-op: each operation owns a short-lived connection."""

    def enqueue(self, spec: AnalysisJobSpec) -> DurableJob:
        if not isinstance(spec, AnalysisJobSpec):
            raise ValueError("enqueue requires an AnalysisJobSpec.")
        with self._transaction() as connection:
            existing = self._select(connection, spec.job_id)
            if existing is not None:
                if existing.spec != spec:
                    raise JobConflictError(
                        "job_id already belongs to a different immutable specification"
                    )
                return existing
            connection.execute(
                """INSERT INTO analysis_jobs (
                    job_id, source_id, source_sha256, stages_json, spans_json,
                    adapter_fingerprint_sha256, runtime_fingerprint_sha256,
                    settings_fingerprint_sha256, score_sha256, created_at, state,
                    owner_id, lease_expires_at, cancel_requested, checkpoint_stage,
                    completed_span_count, continuation_token, last_artifact_id, failure_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 0, NULL, 0, NULL,
                    NULL, NULL)""",
                _spec_values(spec) + (JobState.QUEUED.value,),
            )
            return self._required(connection, spec.job_id)

    def get(self, job_id: str) -> DurableJob | None:
        _identifier(job_id, "job_id")
        with self._connection() as connection:
            return self._select(connection, job_id)

    def list(self, *, state: JobState | None = None, limit: int = 100) -> tuple[DurableJob, ...]:
        if state is not None and not isinstance(state, JobState):
            raise ValueError("state must be a JobState.")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1_024:
            raise ValueError("limit must be between 1 and 1024.")
        with self._connection() as connection:
            if state is None:
                rows = connection.execute(
                    "SELECT * FROM analysis_jobs ORDER BY created_at, job_id LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM analysis_jobs WHERE state = ? "
                    "ORDER BY created_at, job_id LIMIT ?",
                    (state.value, limit),
                ).fetchall()
            return tuple(_row_to_job(row) for row in rows)

    def claim(
        self,
        job_id: str,
        *,
        owner_id: str,
        lease_seconds: float,
        source_sha256: str,
        adapter_fingerprint_sha256: str,
        runtime_fingerprint_sha256: str,
        settings_fingerprint_sha256: str,
        score_sha256: str | None,
    ) -> DurableJob | None:
        """Atomically claim one queued/paused job after exact resume checks."""
        _identifier(job_id, "job_id"); _identifier(owner_id, "owner_id")
        lease_until = time.time() + _lease_seconds(lease_seconds)
        with self._transaction() as connection:
            job = self._required(connection, job_id)
            self._verify_resume_identity(
                job,
                source_sha256,
                adapter_fingerprint_sha256,
                runtime_fingerprint_sha256,
                settings_fingerprint_sha256,
                score_sha256,
            )
            if job.state not in {JobState.QUEUED, JobState.PAUSED} or job.cancel_requested:
                return None
            changed = connection.execute(
                """UPDATE analysis_jobs SET state = ?, owner_id = ?, lease_expires_at = ?
                   WHERE job_id = ? AND state IN (?, ?) AND cancel_requested = 0""",
                (
                    JobState.RUNNING.value,
                    owner_id,
                    lease_until,
                    job_id,
                    JobState.QUEUED.value,
                    JobState.PAUSED.value,
                ),
            ).rowcount
            return self._required(connection, job_id) if changed else None

    def claim_next(
        self,
        *,
        owner_id: str,
        lease_seconds: float,
        source_sha256: str,
        adapter_fingerprint_sha256: str,
        runtime_fingerprint_sha256: str,
        settings_fingerprint_sha256: str,
        score_sha256: str | None,
    ) -> DurableJob | None:
        """Claim the oldest compatible resumable job without a read/claim race."""
        _identifier(owner_id, "owner_id")
        lease_until = time.time() + _lease_seconds(lease_seconds)
        with self._transaction() as connection:
            row = connection.execute(
                """SELECT job_id FROM analysis_jobs WHERE state IN (?, ?) AND cancel_requested = 0
                   AND source_sha256 = ? AND adapter_fingerprint_sha256 = ?
                   AND runtime_fingerprint_sha256 = ? AND settings_fingerprint_sha256 = ?
                   AND score_sha256 IS ? ORDER BY created_at, job_id LIMIT 1""",
                (
                    JobState.QUEUED.value,
                    JobState.PAUSED.value,
                    source_sha256,
                    adapter_fingerprint_sha256,
                    runtime_fingerprint_sha256,
                    settings_fingerprint_sha256,
                    score_sha256,
                ),
            ).fetchone()
            if row is None:
                return None
            job_id = str(row["job_id"])
            connection.execute(
                "UPDATE analysis_jobs SET state = ?, owner_id = ?, lease_expires_at = ? "
                "WHERE job_id = ?",
                (JobState.RUNNING.value, owner_id, lease_until, job_id),
            )
            return self._required(connection, job_id)

    resume = claim

    def heartbeat(self, job_id: str, *, owner_id: str, lease_seconds: float) -> DurableJob:
        return self._owned_update(job_id, owner_id, lease_seconds=lease_seconds)

    def checkpoint(
        self,
        job_id: str,
        *,
        owner_id: str,
        stage: AnalysisStage,
        completed_span_count: int,
        continuation_token: str | None,
        last_artifact_id: str | None,
        pause: bool = True,
    ) -> DurableJob:
        _validate_checkpoint_arguments(
            job_id,
            owner_id,
            stage,
            completed_span_count,
            continuation_token,
            last_artifact_id,
        )
        with self._transaction() as connection:
            job = self._owned(connection, job_id, owner_id)
            _validate_checkpoint_scope(job, stage, completed_span_count)
            state = JobState.PAUSED if pause else JobState.RUNNING
            connection.execute(
                """UPDATE analysis_jobs SET state = ?, owner_id = ?, lease_expires_at = ?,
                   checkpoint_stage = ?, completed_span_count = ?, continuation_token = ?,
                   last_artifact_id = ? WHERE job_id = ?""",
                (
                    state.value,
                    None if pause else owner_id,
                    None if pause else job.lease_expires_at,
                    stage.value,
                    completed_span_count,
                    continuation_token,
                    last_artifact_id,
                    job_id,
                ),
            )
            return self._required(connection, job_id)

    def request_cancellation(self, job_id: str) -> DurableJob:
        _identifier(job_id, "job_id")
        with self._transaction() as connection:
            job = self._required(connection, job_id)
            if job.state in {JobState.COMPLETED, JobState.CANCELLED, JobState.FAILED}:
                return job
            if job.state in {JobState.QUEUED, JobState.PAUSED}:
                connection.execute(
                    "UPDATE analysis_jobs SET state = ?, cancel_requested = 1, owner_id = NULL, "
                    "lease_expires_at = NULL WHERE job_id = ?",
                    (JobState.CANCELLED.value, job_id),
                )
            else:
                connection.execute(
                    "UPDATE analysis_jobs SET cancel_requested = 1 WHERE job_id = ?",
                    (job_id,),
                )
            return self._required(connection, job_id)

    cancel = request_cancellation

    def complete(
        self,
        job_id: str,
        *,
        owner_id: str,
        last_artifact_id: str | None = None,
    ) -> DurableJob:
        if last_artifact_id is not None:
            _identifier(last_artifact_id, "last_artifact_id", MAX_ARTIFACT_ID_CHARS)
        with self._transaction() as connection:
            job = self._owned(connection, job_id, owner_id)
            state = JobState.CANCELLED if job.cancel_requested else JobState.COMPLETED
            connection.execute(
                "UPDATE analysis_jobs SET state = ?, owner_id = NULL, lease_expires_at = NULL, "
                "continuation_token = CASE WHEN cancel_requested = 1 "
                "THEN continuation_token ELSE NULL END, "
                "last_artifact_id = COALESCE(?, last_artifact_id) "
                "WHERE job_id = ?",
                (state.value, last_artifact_id, job_id),
            )
            return self._required(connection, job_id)

    def fail(self, job_id: str, *, owner_id: str, reason: str) -> DurableJob:
        _identifier(reason, "reason", 1024)
        with self._transaction() as connection:
            job = self._owned(connection, job_id, owner_id)
            state = JobState.CANCELLED if job.cancel_requested else JobState.FAILED
            connection.execute(
                "UPDATE analysis_jobs SET state = ?, owner_id = NULL, lease_expires_at = NULL, "
                "failure_reason = ? WHERE job_id = ?",
                (state.value, reason, job_id),
            )
            return self._required(connection, job_id)

    def recover_stale_leases(self, *, now: float | None = None) -> int:
        timestamp = time.time() if now is None else float(now)
        with self._transaction() as connection:
            return connection.execute(
                """UPDATE analysis_jobs
                   SET state = CASE WHEN cancel_requested = 1 THEN ? ELSE ? END,
                   owner_id = NULL, lease_expires_at = NULL
                   WHERE state = ? AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?""",
                (
                    JobState.CANCELLED.value,
                    JobState.PAUSED.value,
                    JobState.RUNNING.value,
                    timestamp,
                ),
            ).rowcount

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self._prepare_path()
        connection = sqlite3.connect(
            self.path,
            timeout=self._busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
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
        _require_private_parent(self.path.parent)
        if self.path.exists() or self.path.is_symlink():
            _require_private_file(self.path)
        else:
            descriptor = os.open(
                self.path,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                _FILE_MODE,
            )
            os.close(descriptor)

    def _private_sidecars(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            _secure_private_sidecar(Path(f"{self.path}{suffix}"))

    @staticmethod
    def _select(connection: sqlite3.Connection, job_id: str) -> DurableJob | None:
        row = connection.execute(
            "SELECT * FROM analysis_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return None if row is None else _row_to_job(row)

    def _required(self, connection: sqlite3.Connection, job_id: str) -> DurableJob:
        job = self._select(connection, job_id)
        if job is None:
            raise JobStoreError("job does not exist")
        return job

    def _owned(self, connection: sqlite3.Connection, job_id: str, owner_id: str) -> DurableJob:
        _identifier(job_id, "job_id")
        _identifier(owner_id, "owner_id")
        job = self._required(connection, job_id)
        if (
            job.state is not JobState.RUNNING
            or job.owner_id != owner_id
            or job.lease_expires_at is None
            or job.lease_expires_at <= time.time()
        ):
            raise JobConflictError("job is not actively leased by this owner")
        return job

    def _owned_update(self, job_id: str, owner_id: str, *, lease_seconds: float) -> DurableJob:
        _identifier(job_id, "job_id")
        _identifier(owner_id, "owner_id")
        with self._transaction() as connection:
            self._owned(connection, job_id, owner_id)
            connection.execute(
                "UPDATE analysis_jobs SET lease_expires_at = ? WHERE job_id = ?",
                (time.time() + _lease_seconds(lease_seconds), job_id),
            )
            return self._required(connection, job_id)

    @staticmethod
    def _verify_resume_identity(
        job: DurableJob,
        source_sha256: str,
        adapter_fingerprint_sha256: str,
        runtime_fingerprint_sha256: str,
        settings_fingerprint_sha256: str,
        score_sha256: str | None,
    ) -> None:
        if (
            job.spec.source_sha256,
            job.spec.adapter_fingerprint_sha256,
            job.spec.runtime_fingerprint_sha256,
            job.spec.settings_fingerprint_sha256,
            job.spec.score_sha256,
        ) != (
            source_sha256,
            adapter_fingerprint_sha256,
            runtime_fingerprint_sha256,
            settings_fingerprint_sha256,
            score_sha256,
        ):
            raise JobConflictError(
                "source, adapter, runtime, settings, or score fingerprint changed; refusing resume"
            )


def _identifier(value: str, label: str, maximum: int = MAX_JOB_ID_CHARS) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{label} must be a bounded non-empty string.")


def _validate_checkpoint_arguments(
    job_id: str,
    owner_id: str,
    stage: AnalysisStage,
    completed_span_count: int,
    continuation_token: str | None,
    last_artifact_id: str | None,
) -> None:
    _identifier(job_id, "job_id")
    _identifier(owner_id, "owner_id")
    if not isinstance(stage, AnalysisStage):
        raise ValueError("stage must be an AnalysisStage.")
    _validate_completed_span_count(completed_span_count)
    _validate_continuation_token(continuation_token)
    if last_artifact_id is not None:
        _identifier(last_artifact_id, "last_artifact_id", MAX_ARTIFACT_ID_CHARS)


def _validate_completed_span_count(value: int) -> None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise ValueError("completed_span_count must be non-negative.")


def _validate_continuation_token(value: str | None) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value or len(value) > 4_096:
        raise ValueError("continuation_token exceeds its bounded contract.")


def _validate_checkpoint_scope(
    job: DurableJob, stage: AnalysisStage, completed_span_count: int
) -> None:
    if stage not in job.spec.stages or completed_span_count > len(job.spec.spans):
        raise JobConflictError("checkpoint is outside the immutable job specification")


def _lease_seconds(value: float) -> float:
    if isinstance(value, bool):
        raise ValueError("lease_seconds must be finite and between 0 and 86400.")
    if not isinstance(value, (int, float)):
        raise ValueError("lease_seconds must be finite and between 0 and 86400.")
    if not math.isfinite(value):
        raise ValueError("lease_seconds must be finite and between 0 and 86400.")
    if not 0 < value:
        raise ValueError("lease_seconds must be finite and between 0 and 86400.")
    if not value <= 86_400:
        raise ValueError("lease_seconds must be finite and between 0 and 86400.")
    return float(value)


def _require_private_parent(path: Path) -> None:
    if not path.is_absolute() or path == Path(os.path.sep):
        raise JobStoreError("database parent must be an existing private directory")
    descriptor = os.open(os.path.sep, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise JobStoreError("database parent must be owner-private")
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOENT, errno.ENOTDIR}:
            raise JobStoreError(
                "database parent must be an existing non-symlink private directory"
            ) from exc
        raise
    finally:
        os.close(descriptor)


def _trusted_path(path: Path) -> Path:
    """Canonicalize macOS's documented /var -> /private/var alias only."""
    var = Path("/var")
    private_var = Path("/private/var")
    if (
        (path == var or var in path.parents)
        and var.is_symlink()
        and Path(os.path.realpath(var)) == private_var
    ):
        return private_var / path.relative_to(var)
    return path


def _require_private_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise JobStoreError("database path must be a regular non-symlink file")
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise JobStoreError("database file must be owner-private")


def _secure_private_sidecar(path: Path) -> None:
    """Secure the opened SQLite file so a pathname swap cannot redirect chmod."""
    descriptor = _open_sidecar_descriptor(path)
    if descriptor is None:
        return
    try:
        _secure_private_sidecar_descriptor(descriptor)
    finally:
        active_failure = sys.exception()
        _close_sidecar_descriptor(descriptor, preserve_failure=active_failure is not None)


def _open_sidecar_descriptor(path: Path) -> int | None:
    """Open a sidecar or classify its race-safe absence and unsafe path errors."""
    try:
        return os.open(
            path,
            os.O_RDONLY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
    except FileNotFoundError:
        # SQLite may remove WAL and SHM files while its connection closes.
        return
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise JobStoreError("database path must be a regular non-symlink file") from exc
        raise JobStoreError("database sidecar could not be secured") from exc


def _secure_private_sidecar_descriptor(descriptor: int) -> None:
    """Validate and privatize the exact opened sidecar descriptor."""
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise JobStoreError("database path must be a regular non-symlink file")
        if metadata.st_uid != os.getuid():
            raise JobStoreError("database file must be owner-private")
        os.fchmod(descriptor, _FILE_MODE)
    except OSError as exc:
        raise JobStoreError("database sidecar could not be secured") from exc


def _close_sidecar_descriptor(descriptor: int, *, preserve_failure: bool) -> None:
    """Close an opened sidecar without obscuring an earlier failure."""
    try:
        os.close(descriptor)
    except OSError as exc:
        if not preserve_failure:
            raise JobStoreError("database sidecar could not be secured") from exc
