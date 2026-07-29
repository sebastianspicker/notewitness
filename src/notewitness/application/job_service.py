"""Deterministic, lease-backed execution of bounded analysis jobs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from notewitness.domain.analysis import AnalysisStage, MAX_CONTINUATION_TOKEN_CHARS
from notewitness.domain.jobs import DurableJob, MAX_ARTIFACT_ID_CHARS
from notewitness.domain.timeline import MediaSpan
from notewitness.infrastructure.sqlite_job_store import JobConflictError, SQLiteJobStore


MAX_FAILURE_CODE_CHARS = 96
EXECUTION_FAILURE_CODE = "stage_execution_failed"
INVALID_EXECUTION_CODE = "invalid_stage_execution"


@dataclass(frozen=True, slots=True)
class StageExecution:
    """One bounded stage result, suitable for a durable checkpoint."""

    completed_span_count: int
    completed: bool
    continuation_token: str | None = None
    last_artifact_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.completed_span_count, int)
            or isinstance(self.completed_span_count, bool)
            or self.completed_span_count < 0
        ):
            raise ValueError("completed_span_count must be a non-negative integer.")
        if not isinstance(self.completed, bool):
            raise ValueError("completed must be a boolean.")
        if self.completed and self.continuation_token is not None:
            raise ValueError("A completed stage cannot retain a continuation token.")
        if not self.completed and (
            not isinstance(self.continuation_token, str)
            or not self.continuation_token
            or len(self.continuation_token) > MAX_CONTINUATION_TOKEN_CHARS
        ):
            raise ValueError("An incomplete stage requires a bounded continuation token.")
        if self.last_artifact_id is not None and (
            not isinstance(self.last_artifact_id, str)
            or not self.last_artifact_id
            or len(self.last_artifact_id) > MAX_ARTIFACT_ID_CHARS
        ):
            raise ValueError("last_artifact_id exceeds its bounded contract.")


class StageExecutor(Protocol):
    """Executes one bounded unit of a local analysis stage."""

    def execute(
        self,
        job: DurableJob,
        stage: AnalysisStage,
        spans: tuple[MediaSpan, ...],
        completed_span_count: int,
        continuation_token: str | None,
    ) -> StageExecution:
        """Return progress for one bounded unit without mutating the job store."""


class DurableJobService:
    """Run one claimed job sequentially; callers choose process and scheduling policy."""

    def __init__(
        self,
        store: SQLiteJobStore,
        executor: StageExecutor,
        *,
        owner_id: str,
        lease_seconds: float,
        source_sha256: str,
        adapter_fingerprint_sha256: str,
        runtime_fingerprint_sha256: str,
        settings_fingerprint_sha256: str,
        score_sha256: str | None,
    ) -> None:
        if not isinstance(store, SQLiteJobStore):
            raise ValueError("store must be a SQLiteJobStore.")
        if not isinstance(owner_id, str) or not owner_id or len(owner_id) > 256:
            raise ValueError("owner_id must be a bounded non-empty string.")
        if not 0 < lease_seconds <= 86_400:
            raise ValueError("lease_seconds must be finite and between 0 and 86400.")
        self._store = store
        self._executor = executor
        self._owner_id = owner_id
        self._lease_seconds = float(lease_seconds)
        self._source_sha256 = source_sha256
        self._adapter_fingerprint_sha256 = adapter_fingerprint_sha256
        self._runtime_fingerprint_sha256 = runtime_fingerprint_sha256
        self._settings_fingerprint_sha256 = settings_fingerprint_sha256
        self._score_sha256 = score_sha256

    def run(self, job_id: str) -> DurableJob | None:
        """Claim and exhaust one job, returning ``None`` if it was unavailable."""
        job = self._store.claim(
            job_id,
            owner_id=self._owner_id,
            lease_seconds=self._lease_seconds,
            source_sha256=self._source_sha256,
            adapter_fingerprint_sha256=self._adapter_fingerprint_sha256,
            runtime_fingerprint_sha256=self._runtime_fingerprint_sha256,
            settings_fingerprint_sha256=self._settings_fingerprint_sha256,
            score_sha256=self._score_sha256,
        )
        return None if job is None else self._run_claimed(job)

    def run_next(self) -> DurableJob | None:
        """Claim and exhaust the oldest compatible queued or paused job."""
        job = self._store.claim_next(
            owner_id=self._owner_id,
            lease_seconds=self._lease_seconds,
            source_sha256=self._source_sha256,
            adapter_fingerprint_sha256=self._adapter_fingerprint_sha256,
            runtime_fingerprint_sha256=self._runtime_fingerprint_sha256,
            settings_fingerprint_sha256=self._settings_fingerprint_sha256,
            score_sha256=self._score_sha256,
        )
        return None if job is None else self._run_claimed(job)

    def _run_claimed(self, job: DurableJob) -> DurableJob:
        try:
            stage_index, completed_span_count, continuation_token = _resume_point(job)
        except ValueError:
            return self._fail(job, INVALID_EXECUTION_CODE)
        last_artifact_id = job.last_artifact_id
        while stage_index < len(job.spec.stages):
            job = self._store.heartbeat(
                job.spec.job_id,
                owner_id=self._owner_id,
                lease_seconds=self._lease_seconds,
            )
            if job.cancel_requested:
                return self._store.complete(job.spec.job_id, owner_id=self._owner_id)
            stage = job.spec.stages[stage_index]
            try:
                result = self._executor.execute(
                    job,
                    stage,
                    job.spec.spans,
                    completed_span_count,
                    continuation_token,
                )
            except Exception:
                return self._fail(job, EXECUTION_FAILURE_CODE)
            try:
                _validate_progress(result, completed_span_count, len(job.spec.spans))
            except ValueError:
                return self._fail(job, INVALID_EXECUTION_CODE)
            last_artifact_id = result.last_artifact_id or last_artifact_id
            job = self._store.checkpoint(
                job.spec.job_id,
                owner_id=self._owner_id,
                stage=stage,
                completed_span_count=result.completed_span_count,
                continuation_token=result.continuation_token,
                last_artifact_id=last_artifact_id,
                pause=False,
            )
            if job.cancel_requested:
                return self._store.complete(job.spec.job_id, owner_id=self._owner_id)
            if not result.completed:
                completed_span_count = result.completed_span_count
                continuation_token = result.continuation_token
                continue
            stage_index += 1
            completed_span_count = 0
            continuation_token = None
        return self._store.complete(
            job.spec.job_id,
            owner_id=self._owner_id,
            last_artifact_id=last_artifact_id,
        )

    def _fail(self, job: DurableJob, code: str) -> DurableJob:
        try:
            return self._store.fail(job.spec.job_id, owner_id=self._owner_id, reason=code)
        except JobConflictError:
            current = self._store.get(job.spec.job_id)
            if current is None:
                raise
            return current


JobService = DurableJobService


def _resume_point(job: DurableJob) -> tuple[int, int, str | None]:
    if job.checkpoint_stage is None:
        return 0, 0, None
    checkpoint_index = job.spec.stages.index(job.checkpoint_stage)
    if job.continuation_token is not None:
        return checkpoint_index, job.completed_span_count, job.continuation_token
    if job.completed_span_count == len(job.spec.spans):
        return checkpoint_index + 1, 0, None
    raise ValueError("A non-final checkpoint requires a continuation token.")


def _validate_progress(result: object, previous: int, span_count: int) -> None:
    if not isinstance(result, StageExecution):
        raise ValueError("Stage executors must return StageExecution.")
    if result.completed_span_count < previous or result.completed_span_count > span_count:
        raise ValueError("Stage execution progress is outside the bounded job spans.")
    if result.completed:
        if result.completed_span_count != span_count:
            raise ValueError("A completed stage must cover every bounded span.")
    elif result.completed_span_count == previous:
        raise ValueError("An incomplete stage must advance its durable checkpoint.")
