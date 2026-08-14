"""End-to-end strict-local transcription composition for private projects."""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4

from notewitness.adapters.ffprobe import FFprobeMediaProbe
from notewitness.adapters.whisper_cli import WhisperCLIAdapter
from notewitness.application._transcription_runtime_artifacts import (
    create_private_directory,
    create_run_directory,
    file_identity,
    require_source_checksum,
    source_media,
    valid_run_token,
)
from notewitness.application._transcription_runtime_records import (
    canonical_evidence,
    completed_manifest,
    now,
    write_completed_status,
    write_failure_status,
    write_integration_failure_status,
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
from notewitness.domain.transcript_document import TranscriptDocument
from notewitness.domain.transcription import (
    CanonicalTranscriptEvidence,
    DisfluencyPolicy,
    TranscriptExportFormat,
    TranscriptionRunManifest,
    transcript_export_preflight,
)
from notewitness.local_artifacts import write_new_private_json
from notewitness.project_store import ProjectStore
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
        if self.run_token is not None and not valid_run_token(self.run_token):
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
        source, media_path = source_media(
            store, snapshot, request.source_id, error_type=LocalTranscriptionRuntimeError
        )
        source_identity = capture_source_identity(snapshot.payload, request.source_id)
        require_source_checksum(
            media_path,
            str(source["sha256"]),
            error_type=LocalTranscriptionRuntimeError,
        )
        media = self._media_probe.inspect(media_path)
        run_token = request.run_token or uuid4().hex
        run_id = f"run:{run_token}"
        run_directory = create_run_directory(
            store, run_token, error_type=LocalTranscriptionRuntimeError
        )
        raw_directory = create_private_directory(
            run_directory, "raw", error_type=LocalTranscriptionRuntimeError
        )
        write_new_private_json(
            run_directory / "status.queued.json",
            {
                "run_id": run_id,
                "source_id": request.source_id,
                "state": "queued",
                "timestamp": now(),
                "network_mode": "offline",
            },
        )

        started_at = now()
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
            require_source_checksum(
                media_path,
                str(source["sha256"]),
                error_type=LocalTranscriptionRuntimeError,
            )
            normalized_path = run_directory / "transcript.normalized.json"
            write_new_private_json(normalized_path, asdict(asr_result.document))
            normalized_identity = LocalArtifactIdentity(
                *file_identity(normalized_path, error_type=LocalTranscriptionRuntimeError)
            )
            canonical = canonical_evidence(
                asr_result,
                run_token,
                normalized_artifact_id=normalized_artifact_id,
                normalized_identity=normalized_identity,
            )
            canonical_path = run_directory / "transcript.evidence.json"
            write_new_private_json(canonical_path, asdict(canonical))
            manifest = completed_manifest(
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
                finished_at=now(),
                error_type=LocalTranscriptionRuntimeError,
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
            write_completed_status(
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
                write_integration_failure_status(
                    run_directory, run_id, request.source_id, exc
                )
                message = (
                    "Local transcription completed, but project integration failed; "
                    "private artifacts remain available for recovery."
                )
            else:
                write_failure_status(run_directory, run_id, request.source_id, exc)
                message = (
                    "Local transcription failed; partial artifacts remain in the "
                    "private run."
                )
            raise LocalTranscriptionRuntimeError(message) from exc


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
