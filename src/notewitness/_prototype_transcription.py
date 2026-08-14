"""Ingest, transcription, review, and export command implementations."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path

from notewitness.adapters.ffprobe import FFprobeMediaProbe
from notewitness.adapters.whisper_cli import WhisperCLIAdapter, WhisperCLISettings
from notewitness.application.music_export import MusicExportFormat, SymbolicMusicExportService
from notewitness.application.run_integration import integrate_completed_run
from notewitness.application.speaker_alignment import align_speech_to_anonymous_speakers
from notewitness.application.transcript_review_service import (
    TranscriptReviewDecision,
    accept_transcript_events,
    add_project_actor,
)
from notewitness.application.transcription_runtime import LocalTranscriptionRequest, LocalTranscriptionRuntime
from notewitness.domain.transcription import DisfluencyPolicy, TranscriptExportFormat
from notewitness.local_tools import discover_local_tool
from notewitness.media_ingest import ingest_media
from notewitness._prototype_support import print_json, project_relative, project_root


def ingest_media_command(args: argparse.Namespace) -> int:
    ffprobe = discover_local_tool("ffprobe", args.ffprobe_path)
    imported = ingest_media(
        project_root(args.project), args.media, rights_id=args.rights_id,
        create_restricted_rights=args.create_restricted_rights, probe=FFprobeMediaProbe(ffprobe),
    )
    print_json({
        "byte_count": imported.byte_count,
        "metadata": asdict(imported.metadata) if imported.metadata is not None else None,
        "network_used": False, "project_sha256": imported.project.sha256,
        "relative_path": imported.relative_path, "rights_id": imported.rights_id,
        "sha256": imported.sha256, "source_id": imported.source_id,
    })
    return 0


def transcribe_local(args: argparse.Namespace) -> int:
    export_format = TranscriptExportFormat(args.format) if args.format is not None else None
    root = project_root(args.project)
    request = LocalTranscriptionRequest(
        project_root=root, source_id=args.source_id, export_format=export_format,
        authorize_local_export=args.authorize_local_export,
        acknowledge_export_losses=args.acknowledge_export_losses,
        disfluency_policy=DisfluencyPolicy(args.disfluencies),
        pause_threshold_ms=args.pause_ms or None, visible_timestamps=args.visible_timestamps,
        timestamp_interval_ms=args.timestamp_interval_ms,
    )
    settings = WhisperCLISettings(
        model_checkpoint=Path(args.model_checkpoint), model_license=args.model_license,
        adapter_license=args.adapter_license, ffmpeg_license=args.ffmpeg_license,
        language=args.language, beam_size=args.beam_size, threads=args.threads,
        device=args.device, timeout_seconds=args.timeout_seconds,
    )
    runtime = LocalTranscriptionRuntime(
        media_probe=FFprobeMediaProbe(discover_local_tool("ffprobe", args.ffprobe_path)),
        asr=WhisperCLIAdapter(
            discover_local_tool("whisper", args.whisper_path), settings,
            ffmpeg=discover_local_tool("ffmpeg", args.ffmpeg_path),
        ),
    )
    result = runtime.run(request)
    speaker_alignment = align_speech_to_anonymous_speakers(root)
    print_json({
        "artifacts": {
            "canonical_evidence": project_relative(root, result.canonical_evidence_path),
            "export": project_relative(root, result.export_path) if result.export_path is not None else None,
            "manifest": project_relative(root, result.manifest_path),
            "normalized_transcript": project_relative(root, result.normalized_transcript_path),
            "run_directory": project_relative(root, result.run_directory),
        },
        "event_ids": list(result.event_ids), "language": result.language,
        "network_used": False, "project_sha256": result.project_sha256,
        "run_id": result.run_id, "segment_count": result.segment_count,
        "speaker_alignment_relation_ids": list(speaker_alignment.relation_ids),
        "word_count": result.word_count,
    })
    return 0


def integrate_run(args: argparse.Namespace) -> int:
    root = project_root(args.project)
    result = integrate_completed_run(root, args.run_id)
    speaker_alignment = align_speech_to_anonymous_speakers(root)
    print_json({
        "already_integrated": result.already_integrated, "event_ids": list(result.event_ids),
        "kind": result.kind, "network_used": False, "project_sha256": result.project_sha256,
        "run_id": result.run_id,
        "speaker_alignment_relation_ids": list(speaker_alignment.relation_ids),
        "target_ids": list(result.target_ids),
    })
    return 0


def export_music(args: argparse.Namespace) -> int:
    root = project_root(args.project)
    result = SymbolicMusicExportService.for_project(root).export(
        export_format=MusicExportFormat(args.format), filename=args.filename,
        rights_authorized=args.authorize_local_export,
        loss_preview_acknowledged=args.acknowledge_export_losses, source_id=args.source_id,
    )
    print_json({
        "checksum_sha256": result.checksum_sha256,
        "documented_losses": [asdict(loss) for loss in result.documented_losses],
        "format": result.export_format.value, "network_used": False,
        "path": project_relative(root, Path(result.path)), "record_count": result.record_count,
        "source_ids": list(result.source_ids),
    })
    return 0


def add_actor(args: argparse.Namespace) -> int:
    snapshot = add_project_actor(
        project_root(args.project), actor_id=args.actor_id, role=args.role,
        visibility=args.visibility, instrument_role=args.instrument_role,
    )
    print_json({"actor_id": args.actor_id, "network_used": False, "project_sha256": snapshot.sha256})
    return 0


def review_accept(args: argparse.Namespace) -> int:
    if args.replacement_text is not None and len(args.event_ids) != 1:
        raise ValueError("Replacement text requires exactly one --event.")
    decisions = tuple(
        TranscriptReviewDecision(event_id=event_id, replacement_text=args.replacement_text, actor_id=args.speaker)
        for event_id in args.event_ids
    )
    result = accept_transcript_events(
        project_root(args.project), decisions=decisions, author_id=args.author, reason=args.reason
    )
    print_json({
        "accepted_event_ids": list(result.accepted_event_ids), "network_used": False,
        "project_sha256": result.project_sha256, "revision_ids": list(result.revision_ids),
    })
    return 0
