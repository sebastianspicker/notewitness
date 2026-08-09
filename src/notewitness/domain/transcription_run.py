"""Immutable transcription-run provenance and normalized evidence pointers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
from typing import Any

from notewitness.domain.timeline import MediaSpan
from notewitness.domain.transcription_options import TranscriptionJobSpec
from notewitness.domain.transcription_shared import (
    _freeze_settings,
    _json_ready,
)
from notewitness.domain._transcription_run_validation import (
    _validate_canonical_transcript_evidence,
    _validate_detected_language,
    _validate_manifest,
    _validate_resolved_model_profile,
    _validate_resolved_run_artifact,
    _validate_run_ledger,
    _validate_source_checksum,
)


class TranscriptionRunState(StrEnum):
    QUEUED = "queued"
    CONVERTING = "converting"
    DIARIZING = "diarizing"
    TRANSCRIBING = "transcribing"
    NORMALIZING = "normalizing"
    REVIEW = "review"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DetectedLanguage:
    language_code: str
    probability: float | None
    span: MediaSpan | None = None

    def __post_init__(self) -> None:
        _validate_detected_language(self)


@dataclass(frozen=True, slots=True)
class SourceChecksum:
    source_id: str
    sha256: str

    def __post_init__(self) -> None:
        _validate_source_checksum(self)


@dataclass(frozen=True, slots=True)
class ResolvedRunArtifact:
    """Exact code/model identity copied into a run, not only ledger references."""

    artifact_id: str
    sha256: str
    size_bytes: int
    license_expression: str

    def __post_init__(self) -> None:
        _validate_resolved_run_artifact(self)


@dataclass(frozen=True, slots=True)
class ResolvedModelProfile:
    """Applied profile identity tied to the request, adapter, artifacts, and config."""

    profile_id: str
    requested_profile_id: str
    adapter_id: str
    model_artifact_ids: tuple[str, ...]
    effective_settings_sha256: str

    def __post_init__(self) -> None:
        _validate_resolved_model_profile(self)


def transcription_settings_sha256(settings: Mapping[str, Any]) -> str:
    """Return the canonical digest used to bind applied adapter settings."""

    frozen = _freeze_settings(settings, "effective_settings")
    return hashlib.sha256(
        json.dumps(
            _json_ready(frozen),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class TranscriptionRunManifest:
    run_id: str
    job: TranscriptionJobSpec
    adapter_id: str
    adapter_version: str
    resolved_model_profile: ResolvedModelProfile
    code_artifact: ResolvedRunArtifact
    model_artifacts: tuple[ResolvedRunArtifact, ...]
    source_checksums: tuple[SourceChecksum, ...]
    detected_languages: tuple[DetectedLanguage, ...]
    effective_settings: Mapping[str, Any]
    runtime_fingerprint_sha256: str
    state: TranscriptionRunState
    partial: bool
    started_at: str | None = None
    finished_at: str | None = None
    retry_parent_run_id: str | None = None
    failure_code: str | None = None
    runtime_artifacts: tuple[ResolvedRunArtifact, ...] = ()

    def __post_init__(self) -> None:
        _validate_manifest(
            self,
            run_state_type=TranscriptionRunState,
            job_spec_type=TranscriptionJobSpec,
            profile_type=ResolvedModelProfile,
            artifact_type=ResolvedRunArtifact,
            checksum_type=SourceChecksum,
            language_type=DetectedLanguage,
            settings_sha256=transcription_settings_sha256,
        )

    @property
    def code_artifact_id(self) -> str:
        return self.code_artifact.artifact_id

    @property
    def model_artifact_ids(self) -> tuple[str, ...]:
        return tuple(artifact.artifact_id for artifact in self.model_artifacts)

    @property
    def resolved_model_profile_id(self) -> str:
        return self.resolved_model_profile.profile_id

    def as_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True, slots=True)
class TranscriptionRunLedger:
    """Validate retry ancestry as one acyclic, immutable run collection."""

    manifests: tuple[TranscriptionRunManifest, ...]

    def __post_init__(self) -> None:
        _validate_run_ledger(self.manifests, manifest_type=TranscriptionRunManifest)


@dataclass(frozen=True, slots=True)
class CanonicalTranscriptEvidence:
    """Pointers joining raw output and normalized evidence without flattening it."""

    evidence_id: str
    run_id: str
    raw_response_artifact_id: str
    raw_response_sha256: str
    raw_response_size_bytes: int
    normalized_transcript_artifact_id: str
    normalized_transcript_sha256: str
    normalized_transcript_size_bytes: int
    normalizer_id: str
    segment_hypothesis_ids: tuple[str, ...]
    word_hypothesis_ids: tuple[str, ...]
    speaker_hypothesis_ids: tuple[str, ...]
    overlap_event_ids: tuple[str, ...]
    silence_event_ids: tuple[str, ...]
    partial: bool

    def __post_init__(self) -> None:
        _validate_canonical_transcript_evidence(self)

    @property
    def record_ids(self) -> tuple[str, ...]:
        return (
            *self.segment_hypothesis_ids,
            *self.word_hypothesis_ids,
            *self.speaker_hypothesis_ids,
            *self.overlap_event_ids,
            *self.silence_event_ids,
        )
