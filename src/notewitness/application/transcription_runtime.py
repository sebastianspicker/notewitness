"""End-to-end strict-local transcription composition for private projects."""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any, Callable
from uuid import uuid4

from notewitness.adapters.ffprobe import FFprobeMediaProbe
from notewitness.adapters.whisper_cli import (
    WhisperCLIAdapter,
    WhisperCLIResult,
    WhisperCLISettings,
)
from notewitness.application.run_integration import (
    RunPublication,
    capture_source_identity,
    completed_artifact_sha256s,
    integrate_completed_run,
    select_publication_records,
    write_completed_publication,
)
from notewitness.application.transcript_evidence import append_machine_transcript
from notewitness.domain.timeline import MediaSpan
from notewitness.domain.transcript_document import TranscriptDocument
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
    transcript_export_preflight,
    transcription_settings_sha256,
)
from notewitness.local_artifacts import write_new_private_json
from notewitness.local_tools import LocalToolFailure
from notewitness.project_store import ProjectSnapshot, ProjectStore
from notewitness.transcript_writers import (
    publish_new_private_text,
    render_html,
    render_txt,
    render_webvtt,
)


class LocalTranscriptionRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LocalArtifactIdentity:
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class LocalTranscriptionRequest:
    project_root: Path
    source_id: str
    export_format: TranscriptExportFormat | None = None
    authorize_local_export: bool = False
    acknowledge_export_losses: bool = False
    disfluency_policy: DisfluencyPolicy = DisfluencyPolicy.INCLUDE
    pause_threshold_ms: int | None = None
    visible_timestamps: bool = False
    timestamp_interval_ms: int = 60_000
    run_token: str | None = None

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("Local transcription requires a source ID.")
        if self.export_format is not None and not isinstance(
            self.export_format, TranscriptExportFormat
        ):
            raise ValueError("export_format must be a TranscriptExportFormat.")
        if not isinstance(self.disfluency_policy, DisfluencyPolicy):
            raise ValueError("disfluency_policy must be a DisfluencyPolicy.")
        if self.disfluency_policy is DisfluencyPolicy.SUPPRESS:
            raise ValueError(
                "Local Whisper transcription does not support disfluency suppression."
            )
        if self.pause_threshold_ms not in {None, 1_000, 2_000, 3_000}:
            raise ValueError("pause_threshold_ms must be off, 1000, 2000, or 3000.")
        if not isinstance(self.visible_timestamps, bool):
            raise ValueError("visible_timestamps must be a boolean.")
        if (
            not isinstance(self.timestamp_interval_ms, int)
            or isinstance(self.timestamp_interval_ms, bool)
            or self.timestamp_interval_ms <= 0
        ):
            raise ValueError("timestamp_interval_ms must be a positive integer.")
        if not isinstance(self.authorize_local_export, bool) or not isinstance(
            self.acknowledge_export_losses, bool
        ):
            raise ValueError("Export decisions must be booleans.")
        if self.run_token is not None and not _valid_run_token(self.run_token):
            raise ValueError("run_token must be exactly 32 lowercase hexadecimal characters.")
        if self.export_format is not None and not self.authorize_local_export:
            raise ValueError("Local transcript export requires explicit authorization.")
        if self.export_format is not None and not self.acknowledge_export_losses:
            raise ValueError("Local transcript export requires loss acknowledgement.")


@dataclass(frozen=True, slots=True)
class LocalTranscriptionResult:
    run_id: str
    run_directory: Path
    manifest_path: Path
    normalized_transcript_path: Path
    canonical_evidence_path: Path
    export_path: Path | None
    event_ids: tuple[str, ...]
    project_sha256: str
    segment_count: int
    word_count: int
    language: str


class LocalTranscriptionRuntime:
    """Compose verified media probing, ASR, graph append, and local export."""

    def __init__(
        self,
        *,
        media_probe: FFprobeMediaProbe,
        asr: WhisperCLIAdapter,
    ) -> None:
        self._media_probe = media_probe
        self._asr = asr

    def run(
        self,
        request: LocalTranscriptionRequest,
        *,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> LocalTranscriptionResult:
        store = ProjectStore(request.project_root)
        snapshot = store.load()
        source, media_path = _source_media(store, snapshot, request.source_id)
        source_identity = capture_source_identity(snapshot.payload, request.source_id)
        _require_source_checksum(media_path, str(source["sha256"]))
        media = self._media_probe.inspect(media_path)
        run_token = request.run_token or uuid4().hex
        run_id = f"run:{run_token}"
        run_directory = _create_run_directory(store, run_token)
        raw_directory = _create_private_directory(run_directory, "raw")
        queued_path = run_directory / "status.queued.json"
        write_new_private_json(
            queued_path,
            {
                "run_id": run_id,
                "source_id": request.source_id,
                "state": "queued",
                "timestamp": _now(),
                "network_mode": "offline",
            },
        )

        started_at = _now()
        manifest_written = False
        try:
            raw_artifact_id = f"artifact:raw-{run_token}"
            normalized_artifact_id = f"artifact:transcript-{run_token}"
            cancellation_options = (
                {"cancellation_requested": cancellation_requested}
                if cancellation_requested is not None
                else {}
            )
            asr_result = self._asr.transcribe(
                media_path=media_path,
                output_directory=raw_directory,
                source_id=request.source_id,
                stream_id="audio",
                run_id=run_id,
                raw_artifact_id=raw_artifact_id,
                duration_us=media.duration_us,
                **cancellation_options,
            )
            _require_source_checksum(media_path, str(source["sha256"]))
            normalized_path = run_directory / "transcript.normalized.json"
            write_new_private_json(normalized_path, asdict(asr_result.document))
            normalized_identity = _file_identity(normalized_path)
            canonical = _canonical_evidence(
                asr_result,
                run_token,
                normalized_artifact_id=normalized_artifact_id,
                normalized_identity=normalized_identity,
            )
            canonical_path = run_directory / "transcript.evidence.json"
            write_new_private_json(canonical_path, asdict(canonical))
            manifest = _completed_manifest(
                asr_result,
                config=self._asr.settings,
                source_sha256=str(source["sha256"]),
                duration_us=media.duration_us,
                export_format=request.export_format or TranscriptExportFormat.HTML,
                disfluency_policy=request.disfluency_policy,
                pause_threshold_ms=request.pause_threshold_ms,
                visible_timestamps=request.visible_timestamps,
                timestamp_interval_ms=request.timestamp_interval_ms,
                started_at=started_at,
                finished_at=_now(),
            )
            manifest_path = run_directory / "manifest.completed.json"
            write_new_private_json(manifest_path, manifest.as_dict())
            manifest_written = True

            projected = copy.deepcopy(snapshot.payload)
            projected_records = append_machine_transcript(
                projected,
                result=asr_result,
                canonical=canonical,
            )
            raw_relative_path = asr_result.raw_output_path.relative_to(
                run_directory
            ).as_posix()
            publication = RunPublication(
                kind="transcription",
                run_id=run_id,
                source=source_identity,
                model_sha256s=(asr_result.model.sha256,),
                artifact_sha256s=completed_artifact_sha256s(
                    run_directory,
                    (
                        "manifest.completed.json",
                        "transcript.normalized.json",
                        "transcript.evidence.json",
                        raw_relative_path,
                    ),
                ),
                records=select_publication_records(
                    projected,
                    actor_ids=(
                        (projected_records.actor_id,)
                        if projected_records.actor_id is not None
                        else ()
                    ),
                    generator_ids=(projected_records.generator_id,),
                    target_ids=projected_records.target_ids,
                    event_ids=projected_records.event_ids,
                ),
            )
            write_completed_publication(run_directory, publication)

            export_path = _publish_export(
                store=store,
                request=request,
                document=asr_result.document,
                manifest=manifest,
                canonical=canonical,
                run_token=run_token,
                run_directory=run_directory,
            )

            integrated = integrate_completed_run(request.project_root, run_id)
            _write_completed_status(
                run_directory,
                run_id=run_id,
                source_id=request.source_id,
                event_count=len(integrated.event_ids),
                segment_count=len(asr_result.document.segments),
                word_count=len(asr_result.document.words),
                exported=export_path is not None,
            )
            return LocalTranscriptionResult(
                run_id=run_id,
                run_directory=run_directory,
                manifest_path=manifest_path,
                normalized_transcript_path=normalized_path,
                canonical_evidence_path=canonical_path,
                export_path=export_path,
                event_ids=integrated.event_ids,
                project_sha256=integrated.project_sha256,
                segment_count=len(asr_result.document.segments),
                word_count=len(asr_result.document.words),
                language=asr_result.document.language,
            )
        except Exception as exc:
            if manifest_written:
                _write_integration_failure_status(
                    run_directory,
                    run_id,
                    request.source_id,
                    exc,
                )
                message = (
                    "Local transcription completed, but project integration failed; "
                    "private artifacts remain available for recovery."
                )
            else:
                _write_failure_status(run_directory, run_id, request.source_id, exc)
                message = (
                    "Local transcription failed; partial artifacts remain in the "
                    "private run."
                )
            raise LocalTranscriptionRuntimeError(
                message
            ) from exc


def _source_media(
    store: ProjectStore,
    snapshot: ProjectSnapshot,
    source_id: str,
) -> tuple[dict[str, Any], Path]:
    sources = snapshot.payload.get("sources")
    if not isinstance(sources, list):
        raise LocalTranscriptionRuntimeError("Project sources collection is malformed.")
    matches = [
        item
        for item in sources
        if isinstance(item, dict) and item.get("id") == source_id
    ]
    if len(matches) != 1:
        raise LocalTranscriptionRuntimeError("Transcription source was not found uniquely.")
    source = matches[0]
    uri = source.get("uri")
    if not isinstance(uri, str):
        raise LocalTranscriptionRuntimeError("Transcription source URI is invalid.")
    relative = PurePosixPath(uri)
    if (
        relative.is_absolute()
        or "\\" in uri
        or len(relative.parts) != 2
        or relative.parts[0] != "media"
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise LocalTranscriptionRuntimeError(
            "Transcription accepts only ingested project media."
        )
    media_path = store.root.joinpath(*relative.parts)
    if media_path.is_symlink():
        raise LocalTranscriptionRuntimeError("Transcription media must not be a symlink.")
    return source, media_path


def _require_source_checksum(path: Path, expected_sha256: str) -> None:
    digest = hashlib.sha256()
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise LocalTranscriptionRuntimeError(
                "Ingested media is not a regular file."
            )
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
    except OSError as exc:
        raise LocalTranscriptionRuntimeError("Ingested media is unavailable.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if digest.hexdigest() != expected_sha256:
        raise LocalTranscriptionRuntimeError("Ingested media checksum no longer matches.")


def _file_identity(path: Path) -> LocalArtifactIdentity:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise LocalTranscriptionRuntimeError(
                "Runtime artifact is not a regular file."
            )
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise LocalTranscriptionRuntimeError(
            "Runtime artifact could not be identified safely."
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if _stat_identity(before) != _stat_identity(after):
        raise LocalTranscriptionRuntimeError(
            "Runtime artifact changed during identity verification."
        )
    return LocalArtifactIdentity(digest.hexdigest(), size)


def _stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _valid_run_token(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 32
        and all(character in "0123456789abcdef" for character in value)
    )


def _create_run_directory(store: ProjectStore, run_token: str) -> Path:
    runs = store.ensure_private_directory("runs")
    return _create_private_directory(runs, run_token)


def _create_private_directory(parent: Path, name: str) -> Path:
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789-"
    if not name or any(character not in allowed for character in name):
        raise LocalTranscriptionRuntimeError("Private run directory name is invalid.")
    path = parent / name
    try:
        os.mkdir(path, 0o700)
        os.chmod(path, 0o700, follow_symlinks=False)
        metadata = path.lstat()
    except OSError as exc:
        raise LocalTranscriptionRuntimeError("Could not create private run storage.") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise LocalTranscriptionRuntimeError("Run storage is not an owner-private directory.")
    return path


def _canonical_evidence(
    result: WhisperCLIResult,
    run_token: str,
    *,
    normalized_artifact_id: str,
    normalized_identity: LocalArtifactIdentity,
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


def _completed_manifest(
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
        raise LocalTranscriptionRuntimeError(
            "Resolved ffmpeg provenance requires an explicit license."
        )
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
        effective_settings_sha256=transcription_settings_sha256(
            effective_settings
        ),
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


def _publish_export(
    *,
    store: ProjectStore,
    request: LocalTranscriptionRequest,
    document: TranscriptDocument,
    manifest: TranscriptionRunManifest,
    canonical: CanonicalTranscriptEvidence,
    run_token: str,
    run_directory: Path,
) -> Path | None:
    if request.export_format is None or not canonical.record_ids:
        return None
    exports = store.ensure_private_directory("exports")
    extension, renderer = {
        TranscriptExportFormat.HTML: ("html", render_html),
        TranscriptExportFormat.TEXT: ("txt", render_txt),
        TranscriptExportFormat.WEBVTT: ("vtt", render_webvtt),
    }[request.export_format]
    destination = exports / f"{run_token}.{extension}"
    preflight = transcript_export_preflight(
        manifest,
        canonical,
        destination=f"exports/{destination.name}",
        selected_record_ids=canonical.record_ids,
        rights_authorized=request.authorize_local_export,
        loss_preview_acknowledged=request.acknowledge_export_losses,
    )
    write_new_private_json(run_directory / "export.preflight.json", asdict(preflight))
    if not preflight.executable:
        raise LocalTranscriptionRuntimeError(
            "Transcript export preflight requires rights and loss acknowledgement."
        )
    return publish_new_private_text(
        destination,
        renderer(
            document,
            visible_timestamps=request.visible_timestamps,
            timestamp_interval_ms=request.timestamp_interval_ms,
            pause_threshold_ms=request.pause_threshold_ms,
        ),
    )


def _write_failure_status(
    run_directory: Path,
    run_id: str,
    source_id: str,
    error: Exception,
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
                "timestamp": _now(),
                "failure_code": _failure_code(error),
            },
        )
    except Exception:
        return


def _write_integration_failure_status(
    run_directory: Path,
    run_id: str,
    source_id: str,
    error: Exception,
) -> None:
    try:
        write_new_private_json(
            run_directory / "status.integration-failed.json",
            {
                "run_id": run_id,
                "source_id": source_id,
                "state": "integration_failed",
                "timestamp": _now(),
                "failure_code": _failure_code(error),
                "transcription_state": "completed",
            },
        )
    except Exception:
        return


def _write_completed_status(
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
                "timestamp": _now(),
                "event_count": event_count,
                "segment_count": segment_count,
                "word_count": word_count,
                "exported": exported,
            },
        )
    except Exception:
        # The completed manifest and committed graph are authoritative. A status
        # convenience artifact must not turn a valid transcription into failure.
        return


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _failure_code(error: Exception) -> str:
    if isinstance(error, LocalToolFailure):
        return f"local_tool:{error.tool_name}:exit:{error.return_code}"
    return type(error).__name__
