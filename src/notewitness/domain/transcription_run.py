"""Immutable transcription-run provenance and normalized evidence pointers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
import math
from typing import Any

from notewitness.domain.timeline import MediaSpan
from notewitness.domain.transcription_options import LanguageMode, TranscriptionJobSpec
from notewitness.domain.transcription_shared import (
    _SHA256,
    _freeze_settings,
    _json_ready,
    _require_bool,
    _require_enum,
    _span_contains,
    _validate_timestamp,
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
        if not isinstance(self.language_code, str) or not self.language_code:
            raise ValueError("Detected language requires a language code.")
        if self.span is not None and not isinstance(self.span, MediaSpan):
            raise ValueError("Detected language spans must be media spans.")
        if self.probability is not None and (
            isinstance(self.probability, bool)
            or not isinstance(self.probability, (int, float))
            or not math.isfinite(self.probability)
            or not 0.0 <= self.probability <= 1.0
        ):
            raise ValueError(
                "Language probability must be absent or finite and in [0, 1]."
            )


@dataclass(frozen=True, slots=True)
class SourceChecksum:
    source_id: str
    sha256: str

    def __post_init__(self) -> None:
        if not self.source_id or not _SHA256.fullmatch(self.sha256):
            raise ValueError("Source checksums require an ID and lowercase SHA-256.")


@dataclass(frozen=True, slots=True)
class ResolvedRunArtifact:
    """Exact code/model identity copied into a run, not only ledger references."""

    artifact_id: str
    sha256: str
    size_bytes: int
    license_expression: str

    def __post_init__(self) -> None:
        if not self.artifact_id or not _SHA256.fullmatch(self.sha256):
            raise ValueError("Resolved artifacts require an ID and lowercase SHA-256.")
        if (
            not isinstance(self.size_bytes, int)
            or isinstance(self.size_bytes, bool)
            or self.size_bytes < 0
        ):
            raise ValueError("Resolved artifact size must be a non-negative integer.")
        if not self.license_expression:
            raise ValueError("Resolved artifacts require an explicit license.")


@dataclass(frozen=True, slots=True)
class ResolvedModelProfile:
    """Applied profile identity tied to the request, adapter, artifacts, and config."""

    profile_id: str
    requested_profile_id: str
    adapter_id: str
    model_artifact_ids: tuple[str, ...]
    effective_settings_sha256: str

    def __post_init__(self) -> None:
        if not all((self.profile_id, self.requested_profile_id, self.adapter_id)):
            raise ValueError("Resolved model profiles require complete identity.")
        if not self.model_artifact_ids or any(
            not artifact_id for artifact_id in self.model_artifact_ids
        ):
            raise ValueError("Resolved model profiles require model artifact IDs.")
        if len(self.model_artifact_ids) != len(set(self.model_artifact_ids)):
            raise ValueError("Resolved-profile artifact IDs must be unique.")
        if not _SHA256.fullmatch(self.effective_settings_sha256):
            raise ValueError(
                "Resolved profiles require an effective-settings SHA-256."
            )


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
        _validate_manifest_identity(self)
        _require_enum(self.state, TranscriptionRunState, "state")
        _require_bool(self.partial, "partial")
        _validate_manifest_snapshots(self)
        _validate_manifest_sources_and_artifacts(self)
        _validate_runtime_fingerprint(self.runtime_fingerprint_sha256)
        object.__setattr__(
            self,
            "effective_settings",
            _freeze_settings(self.effective_settings, "effective_settings"),
        )
        _validate_resolved_profile(self)
        _validate_detected_languages(self)
        _validate_manifest_timestamps(self)
        _validate_manifest_state(self)
        _validate_manifest_failure_and_retry(self)

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


def _validate_manifest_identity(manifest: TranscriptionRunManifest) -> None:
    if not all((manifest.run_id, manifest.adapter_id, manifest.adapter_version)):
        raise ValueError("Run manifests require run, adapter, and resolved profile identity.")


def _validate_manifest_snapshots(manifest: TranscriptionRunManifest) -> None:
    if not isinstance(manifest.job, TranscriptionJobSpec):
        raise ValueError("Run manifests require a validated job snapshot.")
    if not isinstance(manifest.resolved_model_profile, ResolvedModelProfile):
        raise ValueError("Run manifests require a resolved model profile.")
    _validate_artifact_snapshots(manifest)
    _validate_evidence_snapshots(manifest)


def _validate_artifact_snapshots(manifest: TranscriptionRunManifest) -> None:
    artifacts = (
        manifest.code_artifact,
        *manifest.model_artifacts,
        *manifest.runtime_artifacts,
    )
    if any(not isinstance(artifact, ResolvedRunArtifact) for artifact in artifacts):
        raise ValueError("Run manifests require resolved artifact snapshots.")


def _validate_evidence_snapshots(manifest: TranscriptionRunManifest) -> None:
    checksums_are_typed = all(
        isinstance(checksum, SourceChecksum) for checksum in manifest.source_checksums
    )
    languages_are_typed = all(
        isinstance(language, DetectedLanguage) for language in manifest.detected_languages
    )
    if not checksums_are_typed or not languages_are_typed:
        raise ValueError("Run manifests require typed checksum and language evidence.")


def _validate_manifest_sources_and_artifacts(manifest: TranscriptionRunManifest) -> None:
    _validate_source_checksums(manifest)
    _validate_artifact_identities(manifest)


def _validate_source_checksums(manifest: TranscriptionRunManifest) -> None:
    source_ids = {span.source_id for span in manifest.job.spans}
    checksum_ids = {checksum.source_id for checksum in manifest.source_checksums}
    if source_ids != checksum_ids or len(checksum_ids) != len(manifest.source_checksums):
        raise ValueError("Run manifests require one checksum per input source.")


def _validate_artifact_identities(manifest: TranscriptionRunManifest) -> None:
    if not manifest.model_artifacts:
        raise ValueError("Run manifests require code and model artifacts.")
    artifact_ids = (manifest.code_artifact.artifact_id, *(artifact.artifact_id for artifact in manifest.model_artifacts), *(artifact.artifact_id for artifact in manifest.runtime_artifacts))
    if len(artifact_ids) != len(set(artifact_ids)):
        raise ValueError("Run manifest artifact IDs must be unique.")


def _validate_runtime_fingerprint(value: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError("Run manifests require a runtime fingerprint SHA-256.")


def _validate_resolved_profile(manifest: TranscriptionRunManifest) -> None:
    profile = manifest.resolved_model_profile
    if profile.requested_profile_id != manifest.job.model_profile_id:
        raise ValueError("Resolved profile does not match the requested profile.")
    if profile.adapter_id != manifest.adapter_id:
        raise ValueError("Resolved profile does not match the run adapter.")
    if profile.model_artifact_ids != tuple(artifact.artifact_id for artifact in manifest.model_artifacts):
        raise ValueError("Resolved profile does not match run model artifacts.")
    if profile.effective_settings_sha256 != transcription_settings_sha256(manifest.effective_settings):
        raise ValueError("Resolved profile does not match effective settings.")


def _validate_detected_languages(manifest: TranscriptionRunManifest) -> None:
    for detected in manifest.detected_languages:
        if detected.span is not None and not any(_span_contains(job_span, detected.span) for job_span in manifest.job.spans):
            raise ValueError("Detected-language spans must be inside a requested input span.")


def _validate_manifest_timestamps(manifest: TranscriptionRunManifest) -> None:
    started = _validate_timestamp(manifest.started_at, "started_at")
    finished = _validate_timestamp(manifest.finished_at, "finished_at")
    if manifest.finished_at is not None and manifest.started_at is None:
        raise ValueError("A finished run requires a start timestamp.")
    if started is not None and finished is not None and finished < started:
        raise ValueError("A run cannot finish before it starts.")


def _validate_manifest_state(manifest: TranscriptionRunManifest) -> None:
    _validate_state_timestamps(manifest)
    _validate_completed_language_evidence(manifest)
    _validate_terminal_finish_timestamp(manifest)
    if manifest.state is TranscriptionRunState.COMPLETED and manifest.partial:
        raise ValueError("A completed transcription run cannot be partial.")


def _validate_state_timestamps(manifest: TranscriptionRunManifest) -> None:
    _validate_queued_timestamps(manifest)
    _validate_completed_timestamps(manifest)
    _validate_active_timestamps(manifest)
    _validate_failed_or_cancelled_timestamps(manifest)


def _validate_queued_timestamps(manifest: TranscriptionRunManifest) -> None:
    has_timestamp = manifest.started_at is not None or manifest.finished_at is not None
    if manifest.state is TranscriptionRunState.QUEUED and has_timestamp:
        raise ValueError("A queued run cannot have execution timestamps.")


def _validate_completed_timestamps(manifest: TranscriptionRunManifest) -> None:
    missing_timestamp = manifest.started_at is None or manifest.finished_at is None
    if manifest.state is TranscriptionRunState.COMPLETED and missing_timestamp:
        raise ValueError("A completed run requires start and finish timestamps.")


def _validate_active_timestamps(manifest: TranscriptionRunManifest) -> None:
    active_states = {
        TranscriptionRunState.CONVERTING,
        TranscriptionRunState.DIARIZING,
        TranscriptionRunState.TRANSCRIBING,
        TranscriptionRunState.NORMALIZING,
        TranscriptionRunState.REVIEW,
    }
    if manifest.state in active_states and manifest.started_at is None:
        raise ValueError("An active transcription run requires a start timestamp.")


def _validate_failed_or_cancelled_timestamps(
    manifest: TranscriptionRunManifest,
) -> None:
    terminal_states = {TranscriptionRunState.CANCELLED, TranscriptionRunState.FAILED}
    started_without_finish = (
        manifest.started_at is not None and manifest.finished_at is None
    )
    if manifest.state in terminal_states and started_without_finish:
        raise ValueError("A started terminal run requires a finish timestamp.")


def _validate_completed_language_evidence(manifest: TranscriptionRunManifest) -> None:
    if manifest.state is TranscriptionRunState.COMPLETED and manifest.job.language_mode in {LanguageMode.AUTO, LanguageMode.MULTILINGUAL} and not manifest.detected_languages:
        raise ValueError("Completed automatic-language runs require detected language evidence.")


def _validate_terminal_finish_timestamp(manifest: TranscriptionRunManifest) -> None:
    if manifest.state not in {TranscriptionRunState.COMPLETED, TranscriptionRunState.CANCELLED, TranscriptionRunState.FAILED} and manifest.finished_at is not None:
        raise ValueError("Only terminal runs may have a finish timestamp.")


def _validate_manifest_failure_and_retry(manifest: TranscriptionRunManifest) -> None:
    if manifest.state is TranscriptionRunState.FAILED and not manifest.failure_code:
        raise ValueError("Failed transcription runs require a failure code.")
    if manifest.state is not TranscriptionRunState.FAILED and manifest.failure_code:
        raise ValueError("failure_code is only valid for failed runs.")
    if manifest.retry_parent_run_id == manifest.run_id:
        raise ValueError("A transcription run cannot retry itself.")


@dataclass(frozen=True, slots=True)
class TranscriptionRunLedger:
    """Validate retry ancestry as one acyclic, immutable run collection."""

    manifests: tuple[TranscriptionRunManifest, ...]

    def __post_init__(self) -> None:
        if not self.manifests or any(
            not isinstance(run, TranscriptionRunManifest) for run in self.manifests
        ):
            raise ValueError("Run ledgers require typed transcription manifests.")
        by_id = {run.run_id: run for run in self.manifests}
        if len(by_id) != len(self.manifests):
            raise ValueError("Run ledger IDs must be unique.")
        for run in self.manifests:
            parent_id = run.retry_parent_run_id
            if parent_id is None:
                continue
            parent = by_id.get(parent_id)
            if parent is None:
                raise ValueError("Run ledger retry parents must exist.")
            if not _same_retry_contract(parent, run):
                raise ValueError(
                    "Retry runs must preserve job, source, adapter, model, settings, "
                    "and runtime fingerprint."
                )
        _reject_run_cycles(by_id)


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
        _require_bool(self.partial, "partial")
        if not all(
            (
                self.evidence_id,
                self.run_id,
                self.raw_response_artifact_id,
                self.normalized_transcript_artifact_id,
                self.normalizer_id,
            )
        ):
            raise ValueError(
                "Canonical transcript evidence requires run, raw, and normalizer IDs."
            )
        if not _SHA256.fullmatch(self.raw_response_sha256) or not _SHA256.fullmatch(
            self.normalized_transcript_sha256
        ):
            raise ValueError("Canonical transcript artifacts require SHA-256 bindings.")
        for size in (
            self.raw_response_size_bytes,
            self.normalized_transcript_size_bytes,
        ):
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ValueError(
                    "Canonical transcript artifact sizes must be non-negative integers."
                )
        for record_ids in (
            self.segment_hypothesis_ids,
            self.word_hypothesis_ids,
            self.speaker_hypothesis_ids,
            self.overlap_event_ids,
            self.silence_event_ids,
        ):
            if any(not record_id for record_id in record_ids):
                raise ValueError("Canonical transcript evidence IDs must not be empty.")
            if len(record_ids) != len(set(record_ids)):
                raise ValueError("Canonical transcript evidence IDs must be unique.")
        if len(self.record_ids) != len(set(self.record_ids)):
            raise ValueError(
                "Canonical transcript evidence IDs must be globally unique."
            )

    @property
    def record_ids(self) -> tuple[str, ...]:
        return (
            *self.segment_hypothesis_ids,
            *self.word_hypothesis_ids,
            *self.speaker_hypothesis_ids,
            *self.overlap_event_ids,
            *self.silence_event_ids,
        )


def _same_retry_contract(
    parent: TranscriptionRunManifest,
    child: TranscriptionRunManifest,
) -> bool:
    return _retry_contract(parent) == _retry_contract(child)


def _retry_contract(manifest: TranscriptionRunManifest) -> tuple[object, ...]:
    return (
        manifest.job.as_dict(),
        manifest.source_checksums,
        manifest.adapter_id,
        manifest.adapter_version,
        manifest.code_artifact,
        manifest.model_artifacts,
        manifest.runtime_artifacts,
        manifest.resolved_model_profile,
        manifest.runtime_fingerprint_sha256,
        _json_ready(manifest.effective_settings),
    )


def _reject_run_cycles(
    by_id: Mapping[str, TranscriptionRunManifest],
) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(run_id: str) -> None:
        if run_id in visiting:
            raise ValueError("Run ledger retry ancestry must be acyclic.")
        if run_id in visited:
            return
        visiting.add(run_id)
        parent_id = by_id[run_id].retry_parent_run_id
        if parent_id is not None:
            visit(parent_id)
        visiting.remove(run_id)
        visited.add(run_id)

    for run_id in by_id:
        visit(run_id)
