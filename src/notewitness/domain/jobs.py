"""Immutable specifications and snapshots for durable local analysis jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re

from notewitness.domain.analysis import AnalysisStage, JobState, MAX_CONTINUATION_TOKEN_CHARS
from notewitness.domain.timeline import MediaSpan


MAX_JOB_ID_CHARS = 256
MAX_JOB_STAGES = 32
MAX_JOB_SPANS = 1_024
MAX_ARTIFACT_ID_CHARS = 512
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _bounded(value: str, label: str, maximum: int = MAX_JOB_ID_CHARS) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{label} must be a non-empty string of at most {maximum} characters.")


def _sha256(value: str, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest.")


@dataclass(frozen=True, slots=True)
class AnalysisJobSpec:
    """The immutable identity and bounded work selection for one analysis job."""

    job_id: str
    source_id: str
    source_sha256: str
    stages: tuple[AnalysisStage, ...]
    spans: tuple[MediaSpan, ...]
    adapter_fingerprint_sha256: str
    runtime_fingerprint_sha256: str
    settings_fingerprint_sha256: str
    score_sha256: str | None = None
    created_at: str = ""

    def __post_init__(self) -> None:
        self._validate_identity_digests()
        self._validate_stages()
        self._validate_spans()
        self._default_or_validate_created_at()

    def _validate_identity_digests(self) -> None:
        _bounded(self.job_id, "job_id")
        _bounded(self.source_id, "source_id")
        _sha256(self.source_sha256, "source_sha256")
        _sha256(self.adapter_fingerprint_sha256, "adapter_fingerprint_sha256")
        _sha256(self.runtime_fingerprint_sha256, "runtime_fingerprint_sha256")
        _sha256(self.settings_fingerprint_sha256, "settings_fingerprint_sha256")
        if self.score_sha256 is not None:
            _sha256(self.score_sha256, "score_sha256")

    def _validate_stages(self) -> None:
        if (
            not isinstance(self.stages, tuple)
            or not self.stages
            or len(self.stages) > MAX_JOB_STAGES
        ):
            raise ValueError(f"stages must contain 1-{MAX_JOB_STAGES} ordered items.")
        if any(not isinstance(stage, AnalysisStage) for stage in self.stages):
            raise ValueError("stages must contain AnalysisStage values.")
        if len(self.stages) != len(set(self.stages)):
            raise ValueError("stages must not contain duplicates.")

    def _validate_spans(self) -> None:
        if not isinstance(self.spans, tuple) or not self.spans or len(self.spans) > MAX_JOB_SPANS:
            raise ValueError(f"spans must contain 1-{MAX_JOB_SPANS} items.")
        if any(
            not isinstance(span, MediaSpan) or span.source_id != self.source_id
            for span in self.spans
        ):
            raise ValueError("every span must be a MediaSpan for source_id.")

    def _default_or_validate_created_at(self) -> None:
        if not self.created_at:
            object.__setattr__(self, "created_at", datetime.now(timezone.utc).isoformat())
        else:
            _bounded(self.created_at, "created_at", 64)
            try:
                datetime.fromisoformat(self.created_at)
            except ValueError as exc:
                raise ValueError("created_at must be an ISO-8601 timestamp.") from exc


@dataclass(frozen=True, slots=True)
class DurableJob:
    """The persisted state, lease and most recent checkpoint for a job."""

    spec: AnalysisJobSpec
    state: JobState
    owner_id: str | None
    lease_expires_at: float | None
    cancel_requested: bool
    checkpoint_stage: AnalysisStage | None = None
    completed_span_count: int = 0
    continuation_token: str | None = None
    last_artifact_id: str | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, JobState):
            raise ValueError("state must be a JobState.")
        if self.owner_id is not None:
            _bounded(self.owner_id, "owner_id")
        if self.lease_expires_at is not None and self.lease_expires_at <= 0:
            raise ValueError("lease_expires_at must be a positive timestamp.")
        if (
            not isinstance(self.completed_span_count, int)
            or isinstance(self.completed_span_count, bool)
            or not 0 <= self.completed_span_count <= len(self.spec.spans)
        ):
            raise ValueError("completed_span_count must be within the job spans.")
        if self.checkpoint_stage is not None and self.checkpoint_stage not in self.spec.stages:
            raise ValueError("checkpoint_stage must be one of the job stages.")
        if self.continuation_token is not None and (
            not self.continuation_token
            or len(self.continuation_token) > MAX_CONTINUATION_TOKEN_CHARS
        ):
            raise ValueError("continuation_token exceeds its bounded contract.")
        if self.last_artifact_id is not None:
            _bounded(self.last_artifact_id, "last_artifact_id", MAX_ARTIFACT_ID_CHARS)
        if self.failure_reason is not None:
            _bounded(self.failure_reason, "failure_reason", 1024)
