"""Command-line contract harness for NoteWitness."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import sys
from typing import Sequence

from notewitness import __version__
from notewitness.application.capabilities import (
    PROFILES,
    capability_manifest,
    profile_readiness,
)
from notewitness.application.lesson_notes import LessonNotesProjector
from notewitness.application.workbench_processing import WorkbenchProcessingError
from notewitness.domain.timeline import MediaSpan
from notewitness.domain.transcription import (
    DiarizationMode,
    DisfluencyPolicy,
    LanguageMode,
    TranscriptExportFormat,
    TranscriptionJobSpec,
    transcript_export_losses,
)
from notewitness.domain.utilities import MetronomePlan, tuner_reading
from notewitness.evidence import EvidenceGraph, EvidenceGraphError
from notewitness.local_artifacts import LocalArtifactError, write_new_private_json
from notewitness.network import NetworkAccessDenied, TransportFailure
from notewitness.project import ProjectInitializationError, initialize_project
from notewitness.prototype_commands import (
    handle_prototype_command,
    register_prototype_commands,
)
from notewitness.providers.openai_responses import (
    OpenAIConfigurationError,
    OpenAIOutputError,
    OpenAIRelationSuggester,
)
from notewitness.presentation.workbench_server import serve_workbench
from notewitness.presentation.workbench_server import WorkbenchServerError
from notewitness.project_store import ProjectStoreError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="notewitness",
        description="Local-first NoteWitness research prototype.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create an offline project")
    init_parser.add_argument("directory")
    init_parser.add_argument("--name")

    validate_parser = subparsers.add_parser(
        "validate", help="validate an evidence-graph project"
    )
    validate_parser.add_argument("project")

    inspect_parser = subparsers.add_parser(
        "inspect", help="show non-sensitive project counts"
    )
    inspect_parser.add_argument("project")

    notes_parser = subparsers.add_parser(
        "lesson-notes",
        help="create a private local lesson-notes artifact from validated evidence",
    )
    notes_parser.add_argument("project")
    notes_parser.add_argument(
        "--output",
        required=True,
        help="new JSON path; existing files are never replaced",
    )

    subparsers.add_parser(
        "capabilities", help="show the truthful production capability inventory"
    )

    doctor_parser = subparsers.add_parser(
        "doctor", help="check whether a production profile is actually ready"
    )
    doctor_parser.add_argument(
        "--profile", choices=tuple(PROFILES), default="tonic-local"
    )

    workbench_parser = subparsers.add_parser(
        "workbench",
        help="open the loopback-only graphical lesson and research workbench",
    )
    workbench_parser.add_argument("project")
    workbench_parser.add_argument("--port", type=int, default=0)
    workbench_parser.add_argument("--no-open-browser", action="store_true")
    workbench_parser.add_argument(
        "--runtime-config",
        help=(
            "owner-private JSON approving local executables, checkpoints, licenses, "
            "and analysis stages for GUI processing"
        ),
    )

    tuner_parser = subparsers.add_parser(
        "tuner-reading",
        help="calculate an offline tuner reading from a local frequency estimate",
    )
    tuner_parser.add_argument("frequency_hz", type=float)
    tuner_parser.add_argument("--a4-hz", type=float, default=440.0)

    metronome_parser = subparsers.add_parser(
        "metronome-plan",
        help="generate a bounded offline metronome click schedule",
    )
    metronome_parser.add_argument("--bpm", type=float, required=True)
    metronome_parser.add_argument("--bars", type=int, default=1)
    metronome_parser.add_argument("--beats-per-bar", type=int, default=4)
    metronome_parser.add_argument("--subdivisions", type=int, default=1)

    transcription_parser = subparsers.add_parser(
        "transcription-plan",
        help="validate a local research-transcription job without running a model",
    )
    transcription_parser.add_argument("--job-id", required=True)
    transcription_parser.add_argument("--source-id", required=True)
    transcription_parser.add_argument("--start-us", type=int, default=0)
    transcription_parser.add_argument("--duration-us", type=int, required=True)
    transcription_parser.add_argument("--model-profile", required=True)
    transcription_parser.add_argument(
        "--language-mode",
        choices=tuple(mode.value for mode in LanguageMode),
        default=LanguageMode.AUTO.value,
    )
    transcription_parser.add_argument("--language")
    transcription_parser.add_argument(
        "--diarization",
        choices=tuple(mode.value for mode in DiarizationMode),
        default=DiarizationMode.AUTO.value,
    )
    transcription_parser.add_argument("--speakers", type=int)
    transcription_parser.add_argument("--detect-overlap", action="store_true")
    transcription_parser.add_argument(
        "--disfluencies",
        choices=tuple(policy.value for policy in DisfluencyPolicy),
        default=DisfluencyPolicy.INCLUDE.value,
    )
    transcription_parser.add_argument(
        "--pause-ms", type=int, choices=(0, 1_000, 2_000, 3_000), default=0
    )
    transcription_parser.add_argument("--visible-timestamps", action="store_true")
    transcription_parser.add_argument(
        "--timestamp-interval-ms", type=int, default=60_000
    )
    transcription_parser.add_argument(
        "--format",
        choices=tuple(item.value for item in TranscriptExportFormat),
        default=TranscriptExportFormat.HTML.value,
    )
    transcription_parser.add_argument("--model-vocabulary-artifact")
    transcription_parser.add_argument("--adapter-prompt-artifact")
    transcription_parser.add_argument("--project-lexicon")
    transcription_parser.add_argument("--beam-size", type=int)
    transcription_parser.add_argument("--vad-threshold", type=float)
    transcription_parser.add_argument("--compute-type")
    transcription_parser.add_argument("--device")
    transcription_parser.add_argument("--backend")

    preview_parser = subparsers.add_parser(
        "preview-relations",
        help="preview the minimized relation-suggestion projection locally",
    )
    preview_parser.add_argument("project")
    preview_parser.add_argument(
        "--event", action="append", required=True, dest="event_ids"
    )
    preview_parser.add_argument(
        "--include-text",
        action="store_true",
        help="print the selected text that a remote request would contain",
    )

    suggest_parser = subparsers.add_parser(
        "suggest-relations",
        help="send selected, rights-authorized event text to OpenAI",
    )
    suggest_parser.add_argument("project")
    suggest_parser.add_argument(
        "--event", action="append", required=True, dest="event_ids"
    )
    suggest_parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="confirm this individual remote request",
    )
    register_prototype_commands(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return _dispatch_command(args, parser)


def _dispatch_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    direct_handlers = {
        "capabilities": _handle_capabilities,
        "doctor": _handle_doctor,
        "workbench": _handle_workbench,
        "tuner-reading": _handle_tuner_reading,
        "metronome-plan": _handle_metronome_plan,
        "transcription-plan": _handle_transcription_plan,
        "init": _handle_init,
    }
    handler = direct_handlers.get(args.command)
    if handler is not None:
        return handler(args)

    prototype_status = handle_prototype_command(args)
    if prototype_status is not None:
        return prototype_status

    graph = _load_valid_graph(args.project)
    if graph is None:
        return 2

    graph_handlers = {
        "validate": _handle_validate,
        "inspect": _handle_inspect,
        "lesson-notes": _handle_lesson_notes,
        "preview-relations": _handle_preview_relations,
        "suggest-relations": _handle_suggest_relations,
    }
    handler = graph_handlers.get(args.command)
    if handler is not None:
        return handler(args, graph)

    parser.error("unsupported command")
    return 2


def _handle_capabilities(_: argparse.Namespace) -> int:
    print(
        json.dumps(capability_manifest(), ensure_ascii=False, indent=2, sort_keys=True)
    )
    return 0


def _handle_doctor(args: argparse.Namespace) -> int:
    readiness = profile_readiness(args.profile)
    print(json.dumps(readiness, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if readiness["ready"] else 6


def _handle_workbench(args: argparse.Namespace) -> int:
    try:
        serve_workbench(
            args.project,
            port=args.port,
            open_browser=not args.no_open_browser,
            runtime_config_path=args.runtime_config,
        )
    except (
        OSError,
        ProjectStoreError,
        ValueError,
        WorkbenchProcessingError,
        WorkbenchServerError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def _handle_tuner_reading(args: argparse.Namespace) -> int:
    try:
        reading = tuner_reading(args.frequency_hz, a4_hz=args.a4_hz)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(asdict(reading), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _handle_metronome_plan(args: argparse.Namespace) -> int:
    try:
        plan = MetronomePlan(
            bpm=args.bpm,
            beats_per_bar=args.beats_per_bar,
            subdivisions_per_beat=args.subdivisions,
        )
        ticks = plan.schedule(args.bars)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    output = {
        "bpm": plan.bpm,
        "beats_per_bar": plan.beats_per_bar,
        "subdivisions_per_beat": plan.subdivisions_per_beat,
        "ticks": [asdict(tick) for tick in ticks],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _handle_transcription_plan(args: argparse.Namespace) -> int:
    try:
        spec = _transcription_job_spec(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    output = {
        "job": spec.as_dict(),
        "export_losses": [asdict(loss) for loss in transcript_export_losses(spec)],
        "executes_model": False,
        "network_used": False,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _transcription_job_spec(args: argparse.Namespace) -> TranscriptionJobSpec:
    return TranscriptionJobSpec(
        job_id=args.job_id,
        spans=(
            MediaSpan(
                source_id=args.source_id,
                stream_id="audio",
                start_us=args.start_us,
                duration_us=args.duration_us,
            ),
        ),
        model_profile_id=args.model_profile,
        language_mode=LanguageMode(args.language_mode),
        requested_language=args.language,
        diarization_mode=DiarizationMode(args.diarization),
        exact_speaker_count=args.speakers,
        detect_overlap=args.detect_overlap,
        disfluency_policy=DisfluencyPolicy(args.disfluencies),
        pause_threshold_ms=args.pause_ms or None,
        visible_timestamps=args.visible_timestamps,
        timestamp_interval_ms=args.timestamp_interval_ms,
        output_format=TranscriptExportFormat(args.format),
        model_vocabulary_artifact_id=args.model_vocabulary_artifact,
        adapter_prompt_artifact_id=args.adapter_prompt_artifact,
        project_lexicon_id=args.project_lexicon,
        adapter_settings={
            key: value
            for key, value in (
                ("beam_size", args.beam_size),
                ("vad_threshold", args.vad_threshold),
                ("compute_type", args.compute_type),
                ("device", args.device),
                ("backend", args.backend),
            )
            if value is not None
        },
    )


def _handle_init(args: argparse.Namespace) -> int:
    try:
        path = initialize_project(args.directory, name=args.name)
    except ProjectInitializationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(path)
    return 0


def _load_valid_graph(project: str) -> EvidenceGraph | None:
    try:
        graph = EvidenceGraph.load(project)
        graph.require_valid()
    except EvidenceGraphError as exc:
        for issue in exc.issues:
            print(f"error: {issue}", file=sys.stderr)
        return None
    return graph


def _handle_validate(args: argparse.Namespace, _: EvidenceGraph) -> int:
    print(f"valid: {args.project}")
    return 0


def _handle_inspect(_: argparse.Namespace, graph: EvidenceGraph) -> int:
    summary = {
        "schema_version": graph.payload.get("schema_version"),
        "project_id": graph.payload.get("project", {}).get("id"),
        "network_mode": graph.network_policy().mode.value,
        "counts": {
            collection: len(graph.records(collection))
            for collection in (
                "sources",
                "actors",
                "targets",
                "events",
                "relations",
                "revisions",
            )
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _handle_lesson_notes(args: argparse.Namespace, graph: EvidenceGraph) -> int:
    notes = LessonNotesProjector.project(graph)
    try:
        output_path = write_new_private_json(args.output, notes.as_dict())
    except LocalArtifactError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(output_path)
    return 0


def _handle_preview_relations(args: argparse.Namespace, graph: EvidenceGraph) -> int:
    try:
        projection = OpenAIRelationSuggester.preview(
            graph=graph, event_ids=args.event_ids
        )
    except OpenAIOutputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 5
    preview = projection.preview_dict(include_text=args.include_text)
    preview["network_mode"] = graph.network_policy().mode.value
    preview["rights_allow_remote"] = graph.selected_events_allow_remote(args.event_ids)
    print(json.dumps(preview, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _handle_suggest_relations(args: argparse.Namespace, graph: EvidenceGraph) -> int:
    try:
        result = OpenAIRelationSuggester.suggest_authorized(
            graph=graph,
            event_ids=args.event_ids,
            confirmed=args.allow_remote,
        )
    except NetworkAccessDenied as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except OpenAIConfigurationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except TransportFailure as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4
    except OpenAIOutputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 5
    print(
        json.dumps(result.as_safe_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    )
    return 0
