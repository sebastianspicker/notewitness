"""Private evidence, manifest, and status-record helpers for transcription."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from notewitness.adapters.whisper_cli import WhisperCLIResult, WhisperCLISettings
from notewitness.domain.timeline import MediaSpan
from notewitness.domain.transcription import (
    CanonicalTranscriptEvidence,
    DetectedLanguage,
    DiarizationMode,
    DisfluencyPolicy,
    LanguageMode,
    ResolvedModelProfile,
    ResolvedRunArtifact,
    SourceChecksum,
    TranscriptExportFormat,
    TranscriptionJobSpec,
    TranscriptionRunManifest,
    TranscriptionRunState,
    transcription_settings_sha256,
)
from notewitness.local_artifacts import write_new_private_json
from notewitness.local_tools import LocalToolFailure


def canonical_evidence(
    result: WhisperCLIResult,
    run_token: str,
    *,
    normalized_artifact_id: str,
    normalized_identity: Any,
) -> CanonicalTranscriptEvidence:
    return CanonicalTranscriptEvidence(
        evidence_id=f"transcript-evidence:{run_token}",
        run_id=result.document.run_id,
        raw_response_artifact_id=result.document.raw_artifact_id,
        raw_response_sha256=result.raw_output.sha256,
        raw_response_size_bytes=result.raw_output.size_bytes,
        normalized_transcript_artifact_id=normalized_artifact_id,
        normalized_transcript_sha256=normalized_identity.sha256,
        normalized_transcript_size_bytes=normalized_identity.size_bytes,
        normalizer_id="normalizer:whisper-cli-v1",
        segment_hypothesis_ids=tuple(
            segment.segment_id for segment in result.document.segments
        ),
        word_hypothesis_ids=tuple(word.word_id for word in result.document.words),
        speaker_hypothesis_ids=(),
        overlap_event_ids=(),
        silence_event_ids=(),
        partial=False,
    )


def completed_manifest(
    result: WhisperCLIResult,
    *,
    config: WhisperCLISettings,
    source_sha256: str,
    duration_us: int,
    export_format: TranscriptExportFormat,
    disfluency_policy: DisfluencyPolicy,
    pause_threshold_ms: int | None,
    visible_timestamps: bool,
    timestamp_interval_ms: int,
    started_at: str,
    finished_at: str,
    error_type: type[Exception],
) -> TranscriptionRunManifest:
    effective_settings = {
        "beam_size": config.beam_size,
        "device": config.device,
        "ffmpeg_sha256": (
            result.ffmpeg.sha256 if result.ffmpeg is not None else None
        ),
        "language": config.language,
        "network_isolated": result.network_isolated,
        "runtime_artifact_scope": (
            "launcher+ffmpeg" if result.ffmpeg is not None else "launcher-only"
        ),
        "threads": config.threads,
    }
    span = MediaSpan(result.document.source_id, result.document.stream_id, 0, duration_us)
    language_mode = (
        LanguageMode.FIXED if config.language is not None else LanguageMode.AUTO
    )
    model_profile_id = f"profile:whisper-{result.model.sha256[:16]}"
    job = TranscriptionJobSpec(
        job_id=f"job:{result.document.run_id.rpartition(':')[2]}",
        spans=(span,),
        model_profile_id=model_profile_id,
        language_mode=language_mode,
        requested_language=config.language,
        diarization_mode=DiarizationMode.OFF,
        exact_speaker_count=None,
        detect_overlap=False,
        disfluency_policy=disfluency_policy,
        pause_threshold_ms=pause_threshold_ms,
        visible_timestamps=visible_timestamps,
        timestamp_interval_ms=timestamp_interval_ms,
        output_format=export_format,
        adapter_settings=effective_settings,
    )
    model_artifact = ResolvedRunArtifact(
        artifact_id=f"artifact:model-{result.model.sha256[:24]}",
        sha256=result.model.sha256,
        size_bytes=result.model.size_bytes,
        license_expression=config.model_license,
    )
    code_artifact = ResolvedRunArtifact(
        artifact_id=f"artifact:whisper-launcher-{result.launcher.sha256[:24]}",
        sha256=result.launcher.sha256,
        size_bytes=result.launcher.size_bytes,
        license_expression=config.adapter_license,
    )
    if result.ffmpeg is not None and not (config.ffmpeg_license or "").strip():
        raise error_type("Resolved ffmpeg provenance requires an explicit license.")
    runtime_artifacts = (
        (
            ResolvedRunArtifact(
                artifact_id=f"artifact:ffmpeg-{result.ffmpeg.sha256[:24]}",
                sha256=result.ffmpeg.sha256,
                size_bytes=result.ffmpeg.size_bytes,
                license_expression=str(config.ffmpeg_license),
            ),
        )
        if result.ffmpeg is not None
        else ()
    )
    profile = ResolvedModelProfile(
        profile_id=f"{model_profile_id}@{model_artifact.artifact_id}",
        requested_profile_id=model_profile_id,
        adapter_id="adapter:openai-whisper-cli",
        model_artifact_ids=(model_artifact.artifact_id,),
        effective_settings_sha256=transcription_settings_sha256(effective_settings),
    )
    detected = (
        ()
        if language_mode is LanguageMode.FIXED
        else (DetectedLanguage(result.document.language, None, span),)
    )
    return TranscriptionRunManifest(
        run_id=result.document.run_id,
        job=job,
        adapter_id="adapter:openai-whisper-cli",
        adapter_version=result.launcher.sha256[:16],
        resolved_model_profile=profile,
        code_artifact=code_artifact,
        model_artifacts=(model_artifact,),
        runtime_artifacts=runtime_artifacts,
        source_checksums=(SourceChecksum(result.document.source_id, source_sha256),),
        detected_languages=detected,
        effective_settings=effective_settings,
        runtime_fingerprint_sha256=result.runtime_fingerprint_sha256,
        state=TranscriptionRunState.COMPLETED,
        partial=False,
        started_at=started_at,
        finished_at=finished_at,
    )


def write_failure_status(
    run_directory: Path, run_id: str, source_id: str, error: Exception
) -> None:
    path = run_directory / "status.failed.json"
    if path.exists():
        return
    try:
        write_new_private_json(
            path,
            {
                "run_id": run_id,
                "source_id": source_id,
                "state": "failed",
                "timestamp": now(),
                "failure_code": failure_code(error),
            },
        )
    except Exception:
        return


def write_integration_failure_status(
    run_directory: Path, run_id: str, source_id: str, error: Exception
) -> None:
    try:
        write_new_private_json(
            run_directory / "status.integration-failed.json",
            {
                "run_id": run_id,
                "source_id": source_id,
                "state": "integration_failed",
                "timestamp": now(),
                "failure_code": failure_code(error),
                "transcription_state": "completed",
            },
        )
    except Exception:
        return


def write_completed_status(
    run_directory: Path,
    *,
    run_id: str,
    source_id: str,
    event_count: int,
    segment_count: int,
    word_count: int,
    exported: bool,
) -> None:
    try:
        write_new_private_json(
            run_directory / "status.completed.json",
            {
                "run_id": run_id,
                "source_id": source_id,
                "state": "completed",
                "timestamp": now(),
                "event_count": event_count,
                "segment_count": segment_count,
                "word_count": word_count,
                "exported": exported,
            },
        )
    except Exception:
        return


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def failure_code(error: Exception) -> str:
    if isinstance(error, LocalToolFailure):
        return f"local_tool:{error.tool_name}:exit:{error.return_code}"
    return type(error).__name__
