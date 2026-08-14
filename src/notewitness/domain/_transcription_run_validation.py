"""Private validation for immutable transcription-run provenance records."""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from notewitness.domain.timeline import MediaSpan
from notewitness.domain.transcription_options import LanguageMode
from notewitness.domain.transcription_shared import (
    _SHA256,
    _freeze_settings,
    _json_ready,
    _require_bool,
    _require_enum,
    _span_contains,
    _validate_timestamp,
)


def _validate_detected_language(detected: object) -> None:
    if not isinstance(detected.language_code, str) or not detected.language_code:
        raise ValueError("Detected language requires a language code.")
    if detected.span is not None and not isinstance(detected.span, MediaSpan):
        raise ValueError("Detected language spans must be media spans.")
    if detected.probability is not None and (
        isinstance(detected.probability, bool)
        or not isinstance(detected.probability, (int, float))
        or not math.isfinite(detected.probability)
        or not 0.0 <= detected.probability <= 1.0
    ):
        raise ValueError("Language probability must be absent or finite and in [0, 1].")


def _validate_source_checksum(checksum: object) -> None:
    if not checksum.source_id or not _SHA256.fullmatch(checksum.sha256):
        raise ValueError("Source checksums require an ID and lowercase SHA-256.")


def _validate_resolved_run_artifact(artifact: object) -> None:
    if not artifact.artifact_id or not _SHA256.fullmatch(artifact.sha256):
        raise ValueError("Resolved artifacts require an ID and lowercase SHA-256.")
    if (
        not isinstance(artifact.size_bytes, int)
        or isinstance(artifact.size_bytes, bool)
        or artifact.size_bytes < 0
    ):
        raise ValueError("Resolved artifact size must be a non-negative integer.")
    if not artifact.license_expression:
        raise ValueError("Resolved artifacts require an explicit license.")


def _validate_resolved_model_profile(profile: object) -> None:
    if not all((profile.profile_id, profile.requested_profile_id, profile.adapter_id)):
        raise ValueError("Resolved model profiles require complete identity.")
    if not profile.model_artifact_ids or any(
        not artifact_id for artifact_id in profile.model_artifact_ids
    ):
        raise ValueError("Resolved model profiles require model artifact IDs.")
    if len(profile.model_artifact_ids) != len(set(profile.model_artifact_ids)):
        raise ValueError("Resolved-profile artifact IDs must be unique.")
    if not _SHA256.fullmatch(profile.effective_settings_sha256):
        raise ValueError("Resolved profiles require an effective-settings SHA-256.")


def _validate_canonical_transcript_evidence(evidence: object) -> None:
    _require_bool(evidence.partial, "partial")
    if not all(
        (
            evidence.evidence_id,
            evidence.run_id,
            evidence.raw_response_artifact_id,
            evidence.normalized_transcript_artifact_id,
            evidence.normalizer_id,
        )
    ):
        raise ValueError("Canonical transcript evidence requires run, raw, and normalizer IDs.")
    if not _SHA256.fullmatch(evidence.raw_response_sha256) or not _SHA256.fullmatch(
        evidence.normalized_transcript_sha256
    ):
        raise ValueError("Canonical transcript artifacts require SHA-256 bindings.")
    for size in (
        evidence.raw_response_size_bytes,
        evidence.normalized_transcript_size_bytes,
    ):
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError("Canonical transcript artifact sizes must be non-negative integers.")
    for record_ids in (
        evidence.segment_hypothesis_ids,
        evidence.word_hypothesis_ids,
        evidence.speaker_hypothesis_ids,
        evidence.overlap_event_ids,
        evidence.silence_event_ids,
    ):
        if any(not record_id for record_id in record_ids):
            raise ValueError("Canonical transcript evidence IDs must not be empty.")
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("Canonical transcript evidence IDs must be unique.")
    if len(evidence.record_ids) != len(set(evidence.record_ids)):
        raise ValueError("Canonical transcript evidence IDs must be globally unique.")


def _validate_manifest(
    manifest: object,
    *,
    run_state_type: type[Any],
    job_spec_type: type[Any],
    profile_type: type[Any],
    artifact_type: type[Any],
    checksum_type: type[Any],
    language_type: type[Any],
    settings_sha256: Any,
) -> None:
    """Apply the stable validation sequence for a run manifest."""

    _validate_manifest_identity(manifest)
    _require_enum(manifest.state, run_state_type, "state")
    _require_bool(manifest.partial, "partial")
    _validate_manifest_snapshots(
        manifest,
        job_spec_type=job_spec_type,
        profile_type=profile_type,
        artifact_type=artifact_type,
        checksum_type=checksum_type,
        language_type=language_type,
    )
    _validate_manifest_sources_and_artifacts(manifest)
    _validate_runtime_fingerprint(manifest.runtime_fingerprint_sha256)
    object.__setattr__(
        manifest,
        "effective_settings",
        _freeze_settings(manifest.effective_settings, "effective_settings"),
    )
    _validate_resolved_profile(manifest, settings_sha256=settings_sha256)
    _validate_detected_languages(manifest)
    _validate_manifest_timestamps(manifest)
    _validate_manifest_state(manifest, run_state_type=run_state_type)
    _validate_manifest_failure_and_retry(manifest, run_state_type=run_state_type)


def _validate_run_ledger(
    manifests: tuple[object, ...],
    *,
    manifest_type: type[Any],
) -> None:
    """Validate immutable retry ancestry for a collection of run manifests."""

    if not manifests or any(not isinstance(run, manifest_type) for run in manifests):
        raise ValueError("Run ledgers require typed transcription manifests.")
    by_id = {run.run_id: run for run in manifests}
    if len(by_id) != len(manifests):
        raise ValueError("Run ledger IDs must be unique.")
    for run in manifests:
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


def _validate_manifest_identity(manifest: object) -> None:
    if not all((manifest.run_id, manifest.adapter_id, manifest.adapter_version)):
        raise ValueError("Run manifests require run, adapter, and resolved profile identity.")


def _validate_manifest_snapshots(
    manifest: object,
    *,
    job_spec_type: type[Any],
    profile_type: type[Any],
    artifact_type: type[Any],
    checksum_type: type[Any],
    language_type: type[Any],
) -> None:
    if not isinstance(manifest.job, job_spec_type):
        raise ValueError("Run manifests require a validated job snapshot.")
    if not isinstance(manifest.resolved_model_profile, profile_type):
        raise ValueError("Run manifests require a resolved model profile.")
    _validate_artifact_snapshots(manifest, artifact_type=artifact_type)
    _validate_evidence_snapshots(
        manifest,
        checksum_type=checksum_type,
        language_type=language_type,
    )


def _validate_artifact_snapshots(
    manifest: object,
    *,
    artifact_type: type[Any],
) -> None:
    artifacts = (
        manifest.code_artifact,
        *manifest.model_artifacts,
        *manifest.runtime_artifacts,
    )
    if any(not isinstance(artifact, artifact_type) for artifact in artifacts):
        raise ValueError("Run manifests require resolved artifact snapshots.")


def _validate_evidence_snapshots(
    manifest: object,
    *,
    checksum_type: type[Any],
    language_type: type[Any],
) -> None:
    checksums_are_typed = all(
        isinstance(checksum, checksum_type) for checksum in manifest.source_checksums
    )
    languages_are_typed = all(
        isinstance(language, language_type) for language in manifest.detected_languages
    )
    if not checksums_are_typed or not languages_are_typed:
        raise ValueError("Run manifests require typed checksum and language evidence.")


def _validate_manifest_sources_and_artifacts(manifest: object) -> None:
    _validate_source_checksums(manifest)
    _validate_artifact_identities(manifest)


def _validate_source_checksums(manifest: object) -> None:
    source_ids = {span.source_id for span in manifest.job.spans}
    checksum_ids = {checksum.source_id for checksum in manifest.source_checksums}
    if source_ids != checksum_ids or len(checksum_ids) != len(manifest.source_checksums):
        raise ValueError("Run manifests require one checksum per input source.")


def _validate_artifact_identities(manifest: object) -> None:
    if not manifest.model_artifacts:
        raise ValueError("Run manifests require code and model artifacts.")
    artifact_ids = (
        manifest.code_artifact.artifact_id,
        *(artifact.artifact_id for artifact in manifest.model_artifacts),
        *(artifact.artifact_id for artifact in manifest.runtime_artifacts),
    )
    if len(artifact_ids) != len(set(artifact_ids)):
        raise ValueError("Run manifest artifact IDs must be unique.")


def _validate_runtime_fingerprint(value: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError("Run manifests require a runtime fingerprint SHA-256.")


def _validate_resolved_profile(manifest: object, *, settings_sha256: Any) -> None:
    profile = manifest.resolved_model_profile
    if profile.requested_profile_id != manifest.job.model_profile_id:
        raise ValueError("Resolved profile does not match the requested profile.")
    if profile.adapter_id != manifest.adapter_id:
        raise ValueError("Resolved profile does not match the run adapter.")
    if profile.model_artifact_ids != tuple(
        artifact.artifact_id for artifact in manifest.model_artifacts
    ):
        raise ValueError("Resolved profile does not match run model artifacts.")
    if profile.effective_settings_sha256 != settings_sha256(manifest.effective_settings):
        raise ValueError("Resolved profile does not match effective settings.")


def _validate_detected_languages(manifest: object) -> None:
    for detected in manifest.detected_languages:
        if detected.span is not None and not any(
            _span_contains(job_span, detected.span) for job_span in manifest.job.spans
        ):
            raise ValueError(
                "Detected-language spans must be inside a requested input span."
            )


def _validate_manifest_timestamps(manifest: object) -> None:
    started = _validate_timestamp(manifest.started_at, "started_at")
    finished = _validate_timestamp(manifest.finished_at, "finished_at")
    if manifest.finished_at is not None and manifest.started_at is None:
        raise ValueError("A finished run requires a start timestamp.")
    if started is not None and finished is not None and finished < started:
        raise ValueError("A run cannot finish before it starts.")


def _validate_manifest_state(manifest: object, *, run_state_type: type[Any]) -> None:
    _validate_state_timestamps(manifest, run_state_type=run_state_type)
    _validate_completed_language_evidence(manifest, run_state_type=run_state_type)
    _validate_terminal_finish_timestamp(manifest, run_state_type=run_state_type)
    if manifest.state is run_state_type.COMPLETED and manifest.partial:
        raise ValueError("A completed transcription run cannot be partial.")


def _validate_state_timestamps(manifest: object, *, run_state_type: type[Any]) -> None:
    _validate_queued_timestamps(manifest, run_state_type=run_state_type)
    _validate_completed_timestamps(manifest, run_state_type=run_state_type)
    _validate_active_timestamps(manifest, run_state_type=run_state_type)
    _validate_failed_or_cancelled_timestamps(manifest, run_state_type=run_state_type)


def _validate_queued_timestamps(manifest: object, *, run_state_type: type[Any]) -> None:
    has_timestamp = manifest.started_at is not None or manifest.finished_at is not None
    if manifest.state is run_state_type.QUEUED and has_timestamp:
        raise ValueError("A queued run cannot have execution timestamps.")


def _validate_completed_timestamps(
    manifest: object,
    *,
    run_state_type: type[Any],
) -> None:
    missing_timestamp = manifest.started_at is None or manifest.finished_at is None
    if manifest.state is run_state_type.COMPLETED and missing_timestamp:
        raise ValueError("A completed run requires start and finish timestamps.")


def _validate_active_timestamps(manifest: object, *, run_state_type: type[Any]) -> None:
    active_states = {
        run_state_type.CONVERTING,
        run_state_type.DIARIZING,
        run_state_type.TRANSCRIBING,
        run_state_type.NORMALIZING,
        run_state_type.REVIEW,
    }
    if manifest.state in active_states and manifest.started_at is None:
        raise ValueError("An active transcription run requires a start timestamp.")


def _validate_failed_or_cancelled_timestamps(
    manifest: object,
    *,
    run_state_type: type[Any],
) -> None:
    terminal_states = {run_state_type.CANCELLED, run_state_type.FAILED}
    started_without_finish = (
        manifest.started_at is not None and manifest.finished_at is None
    )
    if manifest.state in terminal_states and started_without_finish:
        raise ValueError("A started terminal run requires a finish timestamp.")


def _validate_completed_language_evidence(
    manifest: object,
    *,
    run_state_type: type[Any],
) -> None:
    if (
        manifest.state is run_state_type.COMPLETED
        and manifest.job.language_mode
        in {LanguageMode.AUTO, LanguageMode.MULTILINGUAL}
        and not manifest.detected_languages
    ):
        raise ValueError(
            "Completed automatic-language runs require detected language evidence."
        )


def _validate_terminal_finish_timestamp(
    manifest: object,
    *,
    run_state_type: type[Any],
) -> None:
    if (
        manifest.state
        not in {
            run_state_type.COMPLETED,
            run_state_type.CANCELLED,
            run_state_type.FAILED,
        }
        and manifest.finished_at is not None
    ):
        raise ValueError("Only terminal runs may have a finish timestamp.")


def _validate_manifest_failure_and_retry(
    manifest: object,
    *,
    run_state_type: type[Any],
) -> None:
    if manifest.state is run_state_type.FAILED and not manifest.failure_code:
        raise ValueError("Failed transcription runs require a failure code.")
    if manifest.state is not run_state_type.FAILED and manifest.failure_code:
        raise ValueError("failure_code is only valid for failed runs.")
    if manifest.retry_parent_run_id == manifest.run_id:
        raise ValueError("A transcription run cannot retry itself.")


def _same_retry_contract(parent: object, child: object) -> bool:
    return _retry_contract(parent) == _retry_contract(child)


def _retry_contract(manifest: object) -> tuple[object, ...]:
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


def _reject_run_cycles(by_id: Mapping[str, object]) -> None:
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
