"""Operator commands for the production-hardened local prototype.

This module keeps filesystem, process, and model composition out of the
general-purpose CLI contract harness. Every automatic path is explicit:
local media, local binaries, a local model checkpoint, and declared licenses.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import sys
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from notewitness.adapters.ffprobe import FFprobeMediaProbe, MediaProbeError
from notewitness.adapters.analysis_cli import (
    AnalysisCLIError,
    LocalAnalysisCLIAdapter,
    LocalAnalysisCLISettings,
    LocalAnalysisSource,
    analysis_artifact_identity,
)
from notewitness.adapters.whisper_cli import (
    WhisperCLIAdapter,
    WhisperCLIError,
    WhisperCLISettings,
)
from notewitness.application.transcript_review_service import (
    TranscriptReviewDecision,
    TranscriptReviewError,
    accept_transcript_events,
    add_project_actor,
)
from notewitness.application.music_export import (
    MusicExportError,
    MusicExportFormat,
    SymbolicMusicExportService,
)
from notewitness.application.analysis_runtime import (
    LocalAnalysisRunRequest,
    LocalAnalysisRuntime,
    LocalAnalysisRuntimeError,
    LocalAnalysisStep,
)
from notewitness.application.resumable_analysis import (
    ResumableAnalysisCoordinator,
    ResumableAnalysisError,
    ResumableAnalysisStep,
)
from notewitness.application.run_integration import (
    RunIntegrationError,
    integrate_completed_run,
)
from notewitness.application.speaker_alignment import (
    SpeakerAlignmentError,
    align_speech_to_anonymous_speakers,
)
from notewitness.domain.analysis import AnalysisStage
from notewitness.domain.jobs import AnalysisJobSpec, DurableJob
from notewitness.domain.timeline import MediaSpan
from notewitness.application.transcription_runtime import (
    LocalTranscriptionRequest,
    LocalTranscriptionRuntime,
    LocalTranscriptionRuntimeError,
)
from notewitness.domain.transcription import DisfluencyPolicy, TranscriptExportFormat
from notewitness.local_tools import (
    BoundedLocalToolRunner,
    LocalTool,
    LocalToolError,
    discover_local_tool,
)
from notewitness.local_artifacts import LocalArtifactError
from notewitness.media_ingest import MediaIngestError, ingest_media
from notewitness.infrastructure.sqlite_job_store import (
    JobStoreError,
    SQLiteJobStore,
)
from notewitness.project_store import ProjectStore, ProjectStoreError


_AUTOMATIC_ANALYSIS_STAGES = (
    AnalysisStage.ACTIVITY_SEGMENTATION,
    AnalysisStage.ANONYMOUS_DIARIZATION,
    AnalysisStage.NOTE_TRANSCRIPTION,
    AnalysisStage.CONTINUOUS_PITCH,
    AnalysisStage.INSTRUMENT_DETECTION,
)
_ANALYSIS_STAGE_CHOICES = (
    *_AUTOMATIC_ANALYSIS_STAGES,
    AnalysisStage.INSTRUMENT_DIARIZATION,
    AnalysisStage.SCORE_ALIGNMENT,
)


PROTOTYPE_COMMANDS = frozenset(
    {
        "add-actor",
        "analysis-job",
        "analyze-local",
        "export-music",
        "ingest-media",
        "integrate-run",
        "review-accept",
        "runtime-doctor",
        "transcribe-local",
    }
)


def register_prototype_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the strict-local prototype without hiding required decisions."""

    ingest = subparsers.add_parser(
        "ingest-media",
        help="copy and checksum one local media file into a private project",
    )
    ingest.add_argument("project")
    ingest.add_argument("media")
    ingest_rights = ingest.add_mutually_exclusive_group(required=True)
    ingest_rights.add_argument("--rights-id")
    ingest_rights.add_argument("--create-restricted-rights", action="store_true")
    ingest.add_argument("--ffprobe-path")

    transcribe = subparsers.add_parser(
        "transcribe-local",
        help="run an explicit local Whisper checkpoint with network denied",
    )
    transcribe.add_argument("project")
    transcribe.add_argument("source_id")
    transcribe.add_argument("--model-checkpoint", required=True)
    transcribe.add_argument("--model-license", required=True)
    transcribe.add_argument("--adapter-license", required=True)
    transcribe.add_argument("--ffmpeg-license", required=True)
    transcribe.add_argument("--whisper-path")
    transcribe.add_argument("--ffprobe-path")
    transcribe.add_argument("--ffmpeg-path")
    transcribe.add_argument("--language")
    transcribe.add_argument("--beam-size", type=int, default=5)
    transcribe.add_argument("--threads", type=int, default=0)
    transcribe.add_argument(
        "--device", choices=("cpu", "cuda", "mps"), default="cpu"
    )
    transcribe.add_argument("--timeout-seconds", type=int, default=7_200)
    transcribe.add_argument(
        "--disfluencies",
        choices=tuple(policy.value for policy in DisfluencyPolicy),
        default=DisfluencyPolicy.INCLUDE.value,
    )
    transcribe.add_argument(
        "--pause-ms", type=int, choices=(0, 1_000, 2_000, 3_000), default=0
    )
    transcribe.add_argument("--visible-timestamps", action="store_true")
    transcribe.add_argument("--timestamp-interval-ms", type=int, default=60_000)
    transcribe.add_argument(
        "--format",
        choices=tuple(item.value for item in TranscriptExportFormat),
    )
    transcribe.add_argument("--authorize-local-export", action="store_true")
    transcribe.add_argument("--acknowledge-export-losses", action="store_true")

    integrate = subparsers.add_parser(
        "integrate-run",
        help="recover idempotent graph publication from a completed private run",
    )
    integrate.add_argument("project")
    integrate.add_argument("run_id")

    analyze = subparsers.add_parser(
        "analyze-local",
        help="run explicit local diarization and music-analysis engines",
    )
    analyze.add_argument("project")
    analyze.add_argument("source_id")
    analyze.add_argument("--analysis-path", required=True)
    analyze.add_argument("--adapter-version", required=True)
    analyze.add_argument("--adapter-license", required=True)
    analyze.add_argument("--model-path", required=True)
    analyze.add_argument("--model-license", required=True)
    analyze.add_argument(
        "--stage",
        action="append",
        choices=tuple(stage.value for stage in _ANALYSIS_STAGE_CHOICES),
        dest="stages",
    )
    analyze.add_argument("--start-us", type=int, default=0)
    analyze.add_argument("--duration-us", type=int, required=True)
    analyze.add_argument(
        "--diarization-mode",
        choices=("off", "auto", "exact"),
        default="auto",
    )
    analyze.add_argument("--exact-speaker-count", type=int)
    analyze.add_argument("--detect-overlap", action="store_true")
    analyze.add_argument("--score-path")
    analyze.add_argument("--score-id")
    analyze.add_argument("--score-license")
    analyze.add_argument("--timeout-seconds", type=int, default=3_600)
    analysis_mode = analyze.add_mutually_exclusive_group()
    analysis_mode.add_argument("--one-shot", action="store_true")
    analysis_mode.add_argument("--enqueue-only", action="store_true")
    analysis_mode.add_argument("--resume", action="store_true")
    analyze.add_argument("--job-id")
    analyze.add_argument("--worker-id")
    analyze.add_argument("--lease-seconds", type=float, default=120.0)

    music_export = subparsers.add_parser(
        "export-music",
        help="export reviewable note evidence to private CSV or MIDI",
    )
    music_export.add_argument("project")
    music_export.add_argument(
        "--format",
        choices=tuple(item.value for item in MusicExportFormat),
        required=True,
    )
    music_export.add_argument("--filename", required=True)
    music_export.add_argument("--source-id")
    music_export.add_argument("--authorize-local-export", action="store_true")
    music_export.add_argument("--acknowledge-export-losses", action="store_true")

    analysis_job = subparsers.add_parser(
        "analysis-job",
        help="inspect, cancel, or recover a durable local analysis job",
    )
    analysis_job.add_argument("project")
    analysis_job.add_argument("job_id", nargs="?")
    analysis_job_action = analysis_job.add_mutually_exclusive_group()
    analysis_job_action.add_argument("--cancel", action="store_true")
    analysis_job_action.add_argument("--recover-stale", action="store_true")

    actor = subparsers.add_parser(
        "add-actor",
        help="add a project-local human actor for transcript review",
    )
    actor.add_argument("project")
    actor.add_argument("--actor-id", required=True)
    actor.add_argument("--role", required=True)
    actor.add_argument(
        "--visibility",
        choices=("restricted", "project", "public"),
        default="restricted",
    )
    actor.add_argument("--instrument-role")

    review = subparsers.add_parser(
        "review-accept",
        help="append human acceptance without replacing machine suggestions",
    )
    review.add_argument("project")
    review.add_argument("--event", action="append", required=True, dest="event_ids")
    review.add_argument("--author", required=True)
    review.add_argument("--reason", required=True)
    review.add_argument("--replacement-text")
    review.add_argument("--speaker")

    doctor = subparsers.add_parser(
        "runtime-doctor",
        help="probe local prototype prerequisites without running a model",
    )
    doctor.add_argument("--model-checkpoint")
    doctor.add_argument("--model-license")
    doctor.add_argument("--adapter-license")
    doctor.add_argument("--ffmpeg-license")
    doctor.add_argument("--whisper-path")
    doctor.add_argument("--ffprobe-path")
    doctor.add_argument("--ffmpeg-path")


def handle_prototype_command(args: argparse.Namespace) -> int | None:
    """Execute one prototype command; return ``None`` for another CLI area."""

    if args.command not in PROTOTYPE_COMMANDS:
        return None
    try:
        if args.command == "ingest-media":
            return _ingest_media(args)
        if args.command == "transcribe-local":
            return _transcribe_local(args)
        if args.command == "integrate-run":
            return _integrate_run(args)
        if args.command == "analyze-local":
            return _analyze_local(args)
        if args.command == "export-music":
            return _export_music(args)
        if args.command == "analysis-job":
            return _analysis_job(args)
        if args.command == "add-actor":
            return _add_actor(args)
        if args.command == "review-accept":
            return _review_accept(args)
        if args.command == "runtime-doctor":
            return _runtime_doctor(args)
    except (
        AnalysisCLIError,
        LocalToolError,
        LocalAnalysisRuntimeError,
        LocalTranscriptionRuntimeError,
        MediaIngestError,
        MediaProbeError,
        ProjectStoreError,
        RunIntegrationError,
        JobStoreError,
        LocalArtifactError,
        MusicExportError,
        ResumableAnalysisError,
        SpeakerAlignmentError,
        TranscriptReviewError,
        WhisperCLIError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    raise AssertionError(f"Unhandled prototype command: {args.command}")


def _ingest_media(args: argparse.Namespace) -> int:
    ffprobe = discover_local_tool("ffprobe", args.ffprobe_path)
    imported = ingest_media(
        _project_root(args.project),
        args.media,
        rights_id=args.rights_id,
        create_restricted_rights=args.create_restricted_rights,
        probe=FFprobeMediaProbe(ffprobe),
    )
    _print_json(
        {
            "byte_count": imported.byte_count,
            "metadata": (
                asdict(imported.metadata) if imported.metadata is not None else None
            ),
            "network_used": False,
            "project_sha256": imported.project.sha256,
            "relative_path": imported.relative_path,
            "rights_id": imported.rights_id,
            "sha256": imported.sha256,
            "source_id": imported.source_id,
        }
    )
    return 0


def _transcribe_local(args: argparse.Namespace) -> int:
    export_format = (
        TranscriptExportFormat(args.format) if args.format is not None else None
    )
    project_root = _project_root(args.project)
    request = LocalTranscriptionRequest(
        project_root=project_root,
        source_id=args.source_id,
        export_format=export_format,
        authorize_local_export=args.authorize_local_export,
        acknowledge_export_losses=args.acknowledge_export_losses,
        disfluency_policy=DisfluencyPolicy(args.disfluencies),
        pause_threshold_ms=args.pause_ms or None,
        visible_timestamps=args.visible_timestamps,
        timestamp_interval_ms=args.timestamp_interval_ms,
    )
    settings = WhisperCLISettings(
        model_checkpoint=Path(args.model_checkpoint),
        model_license=args.model_license,
        adapter_license=args.adapter_license,
        ffmpeg_license=args.ffmpeg_license,
        language=args.language,
        beam_size=args.beam_size,
        threads=args.threads,
        device=args.device,
        timeout_seconds=args.timeout_seconds,
    )
    runtime = LocalTranscriptionRuntime(
        media_probe=FFprobeMediaProbe(
            discover_local_tool("ffprobe", args.ffprobe_path)
        ),
        asr=WhisperCLIAdapter(
            discover_local_tool("whisper", args.whisper_path),
            settings,
            ffmpeg=discover_local_tool("ffmpeg", args.ffmpeg_path),
        ),
    )
    result = runtime.run(request)
    speaker_alignment = align_speech_to_anonymous_speakers(project_root)
    _print_json(
        {
            "artifacts": {
                "canonical_evidence": _project_relative(
                    project_root, result.canonical_evidence_path
                ),
                "export": (
                    _project_relative(project_root, result.export_path)
                    if result.export_path is not None
                    else None
                ),
                "manifest": _project_relative(project_root, result.manifest_path),
                "normalized_transcript": _project_relative(
                    project_root, result.normalized_transcript_path
                ),
                "run_directory": _project_relative(
                    project_root, result.run_directory
                ),
            },
            "event_ids": list(result.event_ids),
            "language": result.language,
            "network_used": False,
            "project_sha256": result.project_sha256,
            "run_id": result.run_id,
            "segment_count": result.segment_count,
            "speaker_alignment_relation_ids": list(
                speaker_alignment.relation_ids
            ),
            "word_count": result.word_count,
        }
    )
    return 0


def _integrate_run(args: argparse.Namespace) -> int:
    project_root = _project_root(args.project)
    result = integrate_completed_run(project_root, args.run_id)
    speaker_alignment = align_speech_to_anonymous_speakers(project_root)
    _print_json(
        {
            "already_integrated": result.already_integrated,
            "event_ids": list(result.event_ids),
            "kind": result.kind,
            "network_used": False,
            "project_sha256": result.project_sha256,
            "run_id": result.run_id,
            "speaker_alignment_relation_ids": list(
                speaker_alignment.relation_ids
            ),
            "target_ids": list(result.target_ids),
        }
    )
    return 0


def _export_music(args: argparse.Namespace) -> int:
    project_root = _project_root(args.project)
    result = SymbolicMusicExportService.for_project(project_root).export(
        export_format=MusicExportFormat(args.format),
        filename=args.filename,
        rights_authorized=args.authorize_local_export,
        loss_preview_acknowledged=args.acknowledge_export_losses,
        source_id=args.source_id,
    )
    _print_json(
        {
            "checksum_sha256": result.checksum_sha256,
            "documented_losses": [
                asdict(loss) for loss in result.documented_losses
            ],
            "format": result.export_format.value,
            "network_used": False,
            "path": _project_relative(project_root, Path(result.path)),
            "record_count": result.record_count,
            "source_ids": list(result.source_ids),
        }
    )
    return 0


def _analyze_local(args: argparse.Namespace) -> int:
    project_root = _project_root(args.project)
    media = _project_media_source(project_root, args.source_id)
    model = _local_analysis_source("model", Path(args.model_path))
    score_arguments = (args.score_path, args.score_id, args.score_license)
    if any(item is not None for item in score_arguments) and not all(
        item is not None for item in score_arguments
    ):
        raise ValueError(
            "score-path, score-id, and score-license must be supplied together."
        )
    score = (
        _local_analysis_source(str(args.score_id), Path(args.score_path))
        if args.score_path is not None
        else None
    )
    selected = tuple(AnalysisStage(item) for item in (args.stages or ()))
    if not selected:
        selected = _AUTOMATIC_ANALYSIS_STAGES + (
            (AnalysisStage.SCORE_ALIGNMENT,) if score is not None else ()
        )
    if len(selected) != len(set(selected)):
        raise ValueError("Analysis stages must not be repeated.")
    if AnalysisStage.SCORE_ALIGNMENT in selected and score is None:
        raise ValueError("Score alignment requires explicit score configuration.")
    if args.diarization_mode == "exact":
        if (
            args.exact_speaker_count is None
            or not 1 <= args.exact_speaker_count <= 10
        ):
            raise ValueError("Exact diarization requires 1-10 speakers.")
    elif args.exact_speaker_count is not None:
        raise ValueError("exact-speaker-count requires exact diarization mode.")
    if args.resume and args.job_id is None:
        raise ValueError("--resume requires --job-id.")
    if args.one_shot and any(
        value is not None for value in (args.job_id, args.worker_id)
    ):
        raise ValueError("One-shot analysis does not accept job or worker IDs.")

    tool = discover_local_tool("analysis-suite", args.analysis_path)
    executable_sha256 = tool.identity.sha256
    settings = LocalAnalysisCLISettings(
        working_directory=ProjectStore(project_root).root,
        media=media,
        model=model,
        model_license=args.model_license,
        adapter_license=args.adapter_license,
        timeout_seconds=args.timeout_seconds,
        score=score,
        score_license=args.score_license,
    )
    planned_steps: list[LocalAnalysisStep] = []
    for stage in selected:
        parameters = _analysis_parameters(args, stage)
        planned_steps.append(LocalAnalysisStep(
            adapter=LocalAnalysisCLIAdapter(
                tool,
                stage=stage,
                version=args.adapter_version,
                generator_id=(
                    f"generator:analysis-{stage.value}-"
                    f"{executable_sha256[:8]}-{model.sha256[:8]}-"
                    f"{_json_sha256(parameters)[:8]}"
                ),
                settings=settings,
            ),
            parameters=parameters,
        ))
    steps = tuple(planned_steps)
    if not args.one_shot:
        return _run_resumable_analysis(
            args,
            project_root=project_root,
            media=media,
            model=model,
            score=score,
            steps=steps,
        )
    result = LocalAnalysisRuntime().run(
        LocalAnalysisRunRequest(
            project_root=project_root,
            source_id=args.source_id,
            spans=(
                MediaSpan(
                    args.source_id,
                    "audio",
                    args.start_us,
                    args.duration_us,
                ),
            ),
            steps=steps,
        )
    )
    speaker_alignment = align_speech_to_anonymous_speakers(project_root)
    _print_json(
        {
            "artifacts": {
                "manifest": _project_relative(project_root, result.manifest_path),
                "normalized": _project_relative(project_root, result.normalized_path),
                "run_directory": _project_relative(
                    project_root, result.run_directory
                ),
            },
            "event_ids": list(result.event_ids),
            "network_used": False,
            "project_sha256": result.project_sha256,
            "run_id": result.run_id,
            "stage_states": dict(result.stage_states),
            "speaker_alignment_relation_ids": list(
                speaker_alignment.relation_ids
            ),
            "target_ids": list(result.target_ids),
        }
    )
    return 0


def _run_resumable_analysis(
    args: argparse.Namespace,
    *,
    project_root: Path,
    media: LocalAnalysisSource,
    model: LocalAnalysisSource,
    score: LocalAnalysisSource | None,
    steps: tuple[LocalAnalysisStep, ...],
) -> int:
    job_id = args.job_id or f"job:analysis-{uuid4().hex}"
    worker_id = args.worker_id or f"worker:analysis-{os.getpid()}"
    spans = (
        MediaSpan(
            args.source_id,
            "audio",
            args.start_us,
            args.duration_us,
        ),
    )
    adapter_fingerprint = _json_sha256(
        {
            "adapter_license": args.adapter_license,
            "adapter_version": args.adapter_version,
            "executable": _tool_identity_payload(steps[0].adapter.tool),
            "stages": [step.adapter.stage.value for step in steps],
        }
    )
    runtime_fingerprint = _runtime_fingerprint()
    settings_fingerprint = _json_sha256(
        {
            "model_license": args.model_license,
            "model_sha256": model.sha256,
            "parameters": [dict(step.parameters) for step in steps],
            "score_license": args.score_license,
            "score_sha256": score.sha256 if score is not None else None,
            "spans": [
                {
                    "duration_us": span.duration_us,
                    "source_id": span.source_id,
                    "start_us": span.start_us,
                    "stream_id": span.stream_id,
                }
                for span in spans
            ],
        }
    )
    job_store = SQLiteJobStore(
        ProjectStore(project_root).ensure_private_directory("runs")
        / "analysis-jobs.sqlite"
    )
    coordinator = ResumableAnalysisCoordinator(
        job_store,
        project_root,
        tuple(
            ResumableAnalysisStep(step.adapter, step.parameters)
            for step in steps
        ),
        owner_id=worker_id,
        lease_seconds=args.lease_seconds,
        adapter_fingerprint_sha256=adapter_fingerprint,
        runtime_fingerprint_sha256=runtime_fingerprint,
        settings_fingerprint_sha256=settings_fingerprint,
        model_sha256=model.sha256,
    )
    if not args.resume:
        spec = AnalysisJobSpec(
            job_id=job_id,
            source_id=args.source_id,
            source_sha256=media.sha256,
            stages=tuple(step.adapter.stage for step in steps),
            spans=spans,
            adapter_fingerprint_sha256=adapter_fingerprint,
            runtime_fingerprint_sha256=runtime_fingerprint,
            settings_fingerprint_sha256=settings_fingerprint,
            score_sha256=score.sha256 if score is not None else None,
        )
        queued = coordinator.enqueue(spec)
        if args.enqueue_only:
            _print_json(_durable_job_output(queued, project_root))
            return 0
    job_store.recover_stale_leases()
    finished = coordinator.run(job_id)
    current = finished or job_store.get(job_id)
    if current is None:
        raise ResumableAnalysisError("Durable analysis job does not exist.")
    output = _durable_job_output(current, project_root)
    output["event_ids"] = _resumable_event_ids(project_root, job_id)
    output["speaker_alignment_relation_ids"] = (
        list(align_speech_to_anonymous_speakers(project_root).relation_ids)
        if current.state.value == "completed"
        else []
    )
    _print_json(output)
    return 0 if current.state.value in {"completed", "queued", "paused"} else 7


def _analysis_job(args: argparse.Namespace) -> int:
    project_root = _project_root(args.project)
    store = SQLiteJobStore(
        ProjectStore(project_root).ensure_private_directory("runs")
        / "analysis-jobs.sqlite"
    )
    if args.recover_stale:
        recovered = store.recover_stale_leases()
        _print_json({"network_used": False, "recovered_job_count": recovered})
        return 0
    if args.job_id is None:
        jobs = store.list(limit=1_024)
        _print_json(
            {
                "jobs": [_durable_job_output(job, project_root) for job in jobs],
                "network_used": False,
            }
        )
        return 0
    job = (
        store.request_cancellation(args.job_id)
        if args.cancel
        else store.get(args.job_id)
    )
    if job is None:
        raise ResumableAnalysisError("Durable analysis job does not exist.")
    _print_json(_durable_job_output(job, project_root))
    return 0


def _durable_job_output(job: DurableJob, project_root: Path) -> dict[str, Any]:
    token = hashlib.sha256(job.spec.job_id.encode("utf-8")).hexdigest()[:32]
    return {
        "artifacts": {
            "identity_manifest": f"runs/resumable-{token}/identity.json",
            "job_store": "runs/analysis-jobs.sqlite",
            "run_directory": f"runs/resumable-{token}",
        },
        "cancel_requested": job.cancel_requested,
        "checkpoint_stage": (
            job.checkpoint_stage.value if job.checkpoint_stage is not None else None
        ),
        "completed_span_count": job.completed_span_count,
        "job_id": job.spec.job_id,
        "network_used": False,
        "project": str(project_root),
        "source_id": job.spec.source_id,
        "stages": [stage.value for stage in job.spec.stages],
        "state": job.state.value,
    }


def _resumable_event_ids(project_root: Path, job_id: str) -> list[str]:
    token = hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:32]
    prefix = f"event:analysis-{token}-"
    return sorted(
        str(item["id"])
        for item in ProjectStore(project_root).load().payload["events"]
        if str(item.get("id", "")).startswith(prefix)
    )


def _json_sha256(payload: object) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _runtime_fingerprint() -> str:
    files = (
        Path(sys.modules[ResumableAnalysisCoordinator.__module__].__file__ or ""),
        Path(sys.modules[LocalAnalysisCLIAdapter.__module__].__file__ or ""),
    )
    digest = hashlib.sha256()
    for path in files:
        file_digest, size = _path_identity(path)
        digest.update(path.name.encode("utf-8"))
        digest.update(file_digest.encode("ascii"))
        digest.update(str(size).encode("ascii"))
    return digest.hexdigest()


def _analysis_parameters(
    args: argparse.Namespace,
    stage: AnalysisStage,
) -> dict[str, Any]:
    if stage is AnalysisStage.ANONYMOUS_DIARIZATION:
        return {
            "detect_overlap": args.detect_overlap,
            "diarization_mode": args.diarization_mode,
            "exact_speaker_count": args.exact_speaker_count,
        }
    if stage is AnalysisStage.SCORE_ALIGNMENT:
        return {"score_id": args.score_id}
    return {}


def _project_media_source(project_root: Path, source_id: str) -> LocalAnalysisSource:
    store = ProjectStore(project_root)
    snapshot = store.load()
    matches = [
        item
        for item in snapshot.payload["sources"]
        if item.get("id") == source_id
    ]
    if len(matches) != 1:
        raise ValueError("Project media source is missing or ambiguous.")
    source = matches[0]
    uri = source.get("uri")
    if not isinstance(uri, str):
        raise ValueError("Project media source URI is invalid.")
    relative = PurePosixPath(uri)
    if (
        relative.is_absolute()
        or "\\" in uri
        or not relative.parts
        or relative.parts[0] != "media"
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("Analysis requires project-controlled media.")
    path = store.root.joinpath(*relative.parts)
    digest, size = _path_identity(path)
    if digest != source.get("sha256"):
        raise ValueError("Project media checksum does not match its source record.")
    return LocalAnalysisSource(source_id, path, digest, size)


def _local_analysis_source(prefix: str, path: Path) -> LocalAnalysisSource:
    digest, size = analysis_artifact_identity(path)
    source_id = prefix if ":" in prefix else f"{prefix}:analysis-{digest[:32]}"
    return LocalAnalysisSource(source_id, path, digest, size)


def _path_identity(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as input_file:
            for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise ValueError("Configured local artifact could not be read.") from exc
    if size <= 0:
        raise ValueError("Configured local artifact must not be empty.")
    return digest.hexdigest(), size


def _tool_identity_payload(tool: LocalTool) -> dict[str, int | str]:
    identity = tool.identity
    return {
        "changed_ns": identity.changed_ns,
        "device": identity.device,
        "inode": identity.inode,
        "mode": identity.mode,
        "modified_ns": identity.modified_ns,
        "owner_uid": identity.owner_uid,
        "sha256": identity.sha256,
        "size_bytes": identity.size_bytes,
    }


def _add_actor(args: argparse.Namespace) -> int:
    snapshot = add_project_actor(
        _project_root(args.project),
        actor_id=args.actor_id,
        role=args.role,
        visibility=args.visibility,
        instrument_role=args.instrument_role,
    )
    _print_json(
        {
            "actor_id": args.actor_id,
            "network_used": False,
            "project_sha256": snapshot.sha256,
        }
    )
    return 0


def _review_accept(args: argparse.Namespace) -> int:
    if args.replacement_text is not None and len(args.event_ids) != 1:
        raise ValueError("Replacement text requires exactly one --event.")
    decisions = tuple(
        TranscriptReviewDecision(
            event_id=event_id,
            replacement_text=args.replacement_text,
            actor_id=args.speaker,
        )
        for event_id in args.event_ids
    )
    result = accept_transcript_events(
        _project_root(args.project),
        decisions=decisions,
        author_id=args.author,
        reason=args.reason,
    )
    _print_json(
        {
            "accepted_event_ids": list(result.accepted_event_ids),
            "network_used": False,
            "project_sha256": result.project_sha256,
            "revision_ids": list(result.revision_ids),
        }
    )
    return 0


def _runtime_doctor(args: argparse.Namespace) -> int:
    host_platform = platform.system()
    ffprobe = _discover_tool("ffprobe", args.ffprobe_path)
    ffmpeg = _discover_tool("ffmpeg", args.ffmpeg_path)
    whisper = _discover_tool("whisper", args.whisper_path)
    checks: dict[str, bool] = {
        "ffprobe_probe_passed": _probe_tool(ffprobe, ("-version",)),
        "ffmpeg_probe_passed": _probe_tool(ffmpeg, ("-version",)),
        "local_tool_platform_supported": host_platform == "Darwin",
        "network_isolation_available": _network_isolation_available(),
        "whisper_probe_passed": _probe_tool(
            whisper,
            ("--help",),
            search_paths=(ffmpeg.executable.parent,) if ffmpeg is not None else (),
        ),
    }
    model_arguments = (
        args.model_checkpoint,
        args.model_license,
        args.adapter_license,
        args.ffmpeg_license,
    )
    model_configured = all(value is not None for value in model_arguments)
    model_issue: str | None = None
    if any(value is not None for value in model_arguments) and not model_configured:
        model_issue = (
            "model-checkpoint, model-license, adapter-license, and ffmpeg-license "
            "must be supplied together"
        )
    elif model_configured:
        try:
            WhisperCLISettings(
                model_checkpoint=Path(args.model_checkpoint),
                model_license=args.model_license,
                adapter_license=args.adapter_license,
                ffmpeg_license=args.ffmpeg_license,
            )
        except ValueError as exc:
            model_issue = str(exc)
            model_configured = False
    checks["explicit_model_configuration_valid"] = model_configured
    ready = all(checks.values())
    _print_json(
        {
            "browser_capture_and_playback_implemented": True,
            "browser_device_backend_probe_performed": False,
            "checks": checks,
            "checkpoint_content_verified": False,
            "end_to_end_transcription_verified": False,
            "full_music_analysis_ready": False,
            "host_platform": host_platform,
            "missing_from_full_profile": [
                "activity_segmentation",
                "anonymous_diarization",
                "continuous_pitch",
                "instrument_detection",
                "note_detection",
                "score_alignment",
            ],
            "model_configuration_issue": model_issue,
            "network_used": False,
            "prerequisites_ready_for_transcription_attempt": ready,
        }
    )
    return 0 if ready else 6


def _discover_tool(name: str, explicit_path: str | None) -> LocalTool | None:
    try:
        return discover_local_tool(name, explicit_path)
    except (LocalToolError, ValueError):
        return None


def _probe_tool(
    tool: LocalTool | None,
    arguments: tuple[str, ...],
    *,
    search_paths: tuple[Path, ...] = (),
) -> bool:
    if tool is None:
        return False
    try:
        with TemporaryDirectory(prefix="notewitness-doctor-") as temporary:
            workdir = Path(temporary).resolve()
            workdir.chmod(0o700)
            BoundedLocalToolRunner(tool).run(
                arguments,
                working_directory=workdir,
                timeout_seconds=30,
                deny_network=True,
                executable_search_paths=search_paths,
            )
    except (LocalToolError, OSError, ValueError):
        return False
    return True


def _network_isolation_available() -> bool:
    sandbox = Path("/usr/bin/sandbox-exec")
    return (
        platform.system() == "Darwin"
        and sandbox.is_file()
        and os.access(sandbox, os.X_OK)
    )


def _project_root(value: str) -> Path:
    path = Path(value)
    return path.parent if path.name == "project.json" else path


def _project_relative(project_root: Path, path: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError as exc:
        raise LocalTranscriptionRuntimeError(
            "Runtime artifact escaped the project root."
        ) from exc


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
