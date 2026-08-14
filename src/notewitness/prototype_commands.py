"""Public compatibility façade for strict-local prototype commands."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import sys
from tempfile import TemporaryDirectory

from notewitness.adapters.analysis_cli import AnalysisCLIError
from notewitness.adapters.ffprobe import MediaProbeError
from notewitness.adapters.whisper_cli import WhisperCLIError
from notewitness.application.analysis_runtime import LocalAnalysisRuntimeError
from notewitness.application.music_export import MusicExportError
from notewitness.application.resumable_analysis import ResumableAnalysisError
from notewitness.application.run_integration import RunIntegrationError
from notewitness.application.speaker_alignment import SpeakerAlignmentError
from notewitness.application.transcript_review_service import TranscriptReviewError
from notewitness.application.transcription_runtime import LocalTranscriptionRuntimeError
from notewitness.infrastructure.sqlite_job_store import JobStoreError
from notewitness.local_artifacts import LocalArtifactError
from notewitness.local_tools import BoundedLocalToolRunner, LocalTool, LocalToolError, discover_local_tool
from notewitness.media_ingest import MediaIngestError
from notewitness.project_store import ProjectStoreError
from notewitness._prototype_analysis import analysis_job as _analysis_job
from notewitness._prototype_analysis import analyze_local as _analyze_local
from notewitness._prototype_parser import PROTOTYPE_COMMANDS, register_prototype_commands
from notewitness._prototype_support import print_json as _print_json
from notewitness._prototype_transcription import add_actor as _add_actor
from notewitness._prototype_transcription import export_music as _export_music
from notewitness._prototype_transcription import ingest_media_command as _ingest_media
from notewitness._prototype_transcription import integrate_run as _integrate_run
from notewitness._prototype_transcription import review_accept as _review_accept
from notewitness._prototype_transcription import transcribe_local as _transcribe_local


def handle_prototype_command(args: argparse.Namespace) -> int | None:
    """Execute one prototype command; return ``None`` for another CLI area."""

    if args.command not in PROTOTYPE_COMMANDS:
        return None
    try:
        handlers = {
            "ingest-media": _ingest_media,
            "transcribe-local": _transcribe_local,
            "integrate-run": _integrate_run,
            "analyze-local": _analyze_local,
            "export-music": _export_music,
            "analysis-job": _analysis_job,
            "add-actor": _add_actor,
            "review-accept": _review_accept,
            "runtime-doctor": _runtime_doctor,
        }
        return handlers[args.command](args)
    except (
        AnalysisCLIError, LocalToolError, LocalAnalysisRuntimeError,
        LocalTranscriptionRuntimeError, MediaIngestError, MediaProbeError,
        ProjectStoreError, RunIntegrationError, JobStoreError, LocalArtifactError,
        MusicExportError, ResumableAnalysisError, SpeakerAlignmentError,
        TranscriptReviewError, WhisperCLIError, ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _runtime_doctor(args: argparse.Namespace) -> int:
    """Read-only prerequisite probe retained for façade-level patching."""

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
            whisper, ("--help",),
            search_paths=(ffmpeg.executable.parent,) if ffmpeg is not None else (),
        ),
    }
    model_arguments = (args.model_checkpoint, args.model_license, args.adapter_license, args.ffmpeg_license)
    model_configured = all(value is not None for value in model_arguments)
    model_issue: str | None = None
    if any(value is not None for value in model_arguments) and not model_configured:
        model_issue = "model-checkpoint, model-license, adapter-license, and ffmpeg-license must be supplied together"
    elif model_configured:
        from notewitness.adapters.whisper_cli import WhisperCLISettings
        try:
            WhisperCLISettings(
                model_checkpoint=Path(args.model_checkpoint), model_license=args.model_license,
                adapter_license=args.adapter_license, ffmpeg_license=args.ffmpeg_license,
            )
        except ValueError as exc:
            model_issue = str(exc)
            model_configured = False
    checks["explicit_model_configuration_valid"] = model_configured
    ready = all(checks.values())
    _print_json({
        "browser_capture_and_playback_implemented": True,
        "browser_device_backend_probe_performed": False,
        "checks": checks, "checkpoint_content_verified": False,
        "end_to_end_transcription_verified": False, "full_music_analysis_ready": False,
        "host_platform": host_platform,
        "missing_from_full_profile": [
            "activity_segmentation", "anonymous_diarization", "continuous_pitch",
            "instrument_detection", "note_detection", "score_alignment",
        ], "model_configuration_issue": model_issue, "network_used": False,
        "prerequisites_ready_for_transcription_attempt": ready,
    })
    return 0 if ready else 6


def _discover_tool(name: str, explicit_path: str | None) -> LocalTool | None:
    try:
        return discover_local_tool(name, explicit_path)
    except (LocalToolError, ValueError):
        return None


def _probe_tool(tool: LocalTool | None, arguments: tuple[str, ...], *, search_paths: tuple[Path, ...] = ()) -> bool:
    if tool is None:
        return False
    try:
        with TemporaryDirectory(prefix="notewitness-doctor-") as temporary:
            workdir = Path(temporary).resolve()
            workdir.chmod(0o700)
            BoundedLocalToolRunner(tool).run(
                arguments, working_directory=workdir, timeout_seconds=30,
                deny_network=True, executable_search_paths=search_paths,
            )
    except (LocalToolError, OSError, ValueError):
        return False
    return True


def _network_isolation_available() -> bool:
    sandbox = Path("/usr/bin/sandbox-exec")
    return platform.system() == "Darwin" and sandbox.is_file() and os.access(sandbox, os.X_OK)
