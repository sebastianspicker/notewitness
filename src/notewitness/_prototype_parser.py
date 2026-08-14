"""Argument parser registration for strict-local prototype commands."""

from __future__ import annotations

import argparse

from notewitness.application.music_export import MusicExportFormat
from notewitness.domain.analysis import AnalysisStage
from notewitness.domain.transcription import DisfluencyPolicy, TranscriptExportFormat


AUTOMATIC_ANALYSIS_STAGES = (
    AnalysisStage.ACTIVITY_SEGMENTATION,
    AnalysisStage.ANONYMOUS_DIARIZATION,
    AnalysisStage.NOTE_TRANSCRIPTION,
    AnalysisStage.CONTINUOUS_PITCH,
    AnalysisStage.INSTRUMENT_DETECTION,
)
ANALYSIS_STAGE_CHOICES = (
    *AUTOMATIC_ANALYSIS_STAGES,
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
    transcribe.add_argument("--device", choices=("cpu", "cuda", "mps"), default="cpu")
    transcribe.add_argument("--timeout-seconds", type=int, default=7_200)
    transcribe.add_argument(
        "--disfluencies",
        choices=tuple(policy.value for policy in DisfluencyPolicy),
        default=DisfluencyPolicy.INCLUDE.value,
    )
    transcribe.add_argument("--pause-ms", type=int, choices=(0, 1_000, 2_000, 3_000), default=0)
    transcribe.add_argument("--visible-timestamps", action="store_true")
    transcribe.add_argument("--timestamp-interval-ms", type=int, default=60_000)
    transcribe.add_argument("--format", choices=tuple(item.value for item in TranscriptExportFormat))
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
    analyze.add_argument("--stage", action="append", choices=tuple(stage.value for stage in ANALYSIS_STAGE_CHOICES), dest="stages")
    analyze.add_argument("--start-us", type=int, default=0)
    analyze.add_argument("--duration-us", type=int, required=True)
    analyze.add_argument("--diarization-mode", choices=("off", "auto", "exact"), default="auto")
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
        "export-music", help="export reviewable note evidence to private CSV or MIDI"
    )
    music_export.add_argument("project")
    music_export.add_argument("--format", choices=tuple(item.value for item in MusicExportFormat), required=True)
    music_export.add_argument("--filename", required=True)
    music_export.add_argument("--source-id")
    music_export.add_argument("--authorize-local-export", action="store_true")
    music_export.add_argument("--acknowledge-export-losses", action="store_true")

    analysis_job = subparsers.add_parser(
        "analysis-job", help="inspect, cancel, or recover a durable local analysis job"
    )
    analysis_job.add_argument("project")
    analysis_job.add_argument("job_id", nargs="?")
    analysis_job_action = analysis_job.add_mutually_exclusive_group()
    analysis_job_action.add_argument("--cancel", action="store_true")
    analysis_job_action.add_argument("--recover-stale", action="store_true")

    actor = subparsers.add_parser(
        "add-actor", help="add a project-local human actor for transcript review"
    )
    actor.add_argument("project")
    actor.add_argument("--actor-id", required=True)
    actor.add_argument("--role", required=True)
    actor.add_argument("--visibility", choices=("restricted", "project", "public"), default="restricted")
    actor.add_argument("--instrument-role")

    review = subparsers.add_parser(
        "review-accept", help="append human acceptance without replacing machine suggestions"
    )
    review.add_argument("project")
    review.add_argument("--event", action="append", required=True, dest="event_ids")
    review.add_argument("--author", required=True)
    review.add_argument("--reason", required=True)
    review.add_argument("--replacement-text")
    review.add_argument("--speaker")

    doctor = subparsers.add_parser(
        "runtime-doctor", help="probe local prototype prerequisites without running a model"
    )
    doctor.add_argument("--model-checkpoint")
    doctor.add_argument("--model-license")
    doctor.add_argument("--adapter-license")
    doctor.add_argument("--ffmpeg-license")
    doctor.add_argument("--whisper-path")
    doctor.add_argument("--ffprobe-path")
    doctor.add_argument("--ffmpeg-path")
