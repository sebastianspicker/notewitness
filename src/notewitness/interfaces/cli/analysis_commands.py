"""Local music-analysis commands: argument validation and JSON output."""

from __future__ import annotations

import argparse
from pathlib import Path

from notewitness.analysis.local_tools.discovery import discover_local_tool
from notewitness.analysis.suite.jobs import (
    analysis_job as _analysis_job,
    analysis_steps,
    durable_job_output,
    list_analysis_jobs,
    local_analysis_source,
    project_media_source,
    recover_stale_analysis_jobs,
    run_one_shot_analysis,
    run_resumable_analysis,
)
from notewitness.core.analysis.analysis import AnalysisStage
from notewitness.core.time import MediaSpan

from .parser import AUTOMATIC_ANALYSIS_STAGES
from .support import print_json, project_relative, project_root


def analyze_local(args: argparse.Namespace) -> int:
    root = project_root(args.project)
    media = project_media_source(root, args.source_id)
    model = local_analysis_source("model", Path(args.model_path))
    score_arguments = (args.score_path, args.score_id, args.score_license)
    if any(item is not None for item in score_arguments) and not all(item is not None for item in score_arguments):
        raise ValueError("score-path, score-id, and score-license must be supplied together.")
    score = local_analysis_source(str(args.score_id), Path(args.score_path)) if args.score_path is not None else None
    selected = tuple(AnalysisStage(item) for item in (args.stages or ()))
    if not selected:
        selected = AUTOMATIC_ANALYSIS_STAGES + ((AnalysisStage.SCORE_ALIGNMENT,) if score is not None else ())
    if len(selected) != len(set(selected)):
        raise ValueError("Analysis stages must not be repeated.")
    if AnalysisStage.SCORE_ALIGNMENT in selected and score is None:
        raise ValueError("Score alignment requires explicit score configuration.")
    if args.diarization_mode == "exact":
        if args.exact_speaker_count is None or not 1 <= args.exact_speaker_count <= 10:
            raise ValueError("Exact diarization requires 1-10 speakers.")
    elif args.exact_speaker_count is not None:
        raise ValueError("exact-speaker-count requires exact diarization mode.")
    if args.resume and args.job_id is None:
        raise ValueError("--resume requires --job-id.")
    if args.one_shot and any(value is not None for value in (args.job_id, args.worker_id)):
        raise ValueError("One-shot analysis does not accept job or worker IDs.")

    tool = discover_local_tool("analysis-suite", args.analysis_path)
    steps = analysis_steps(
        tool, selected, project_root=root, media=media, model=model, score=score,
        model_license=args.model_license, adapter_license=args.adapter_license,
        adapter_version=args.adapter_version, score_license=args.score_license,
        score_id=args.score_id, timeout_seconds=args.timeout_seconds,
        diarization_mode=args.diarization_mode, exact_speaker_count=args.exact_speaker_count,
        detect_overlap=args.detect_overlap,
    )
    spans = (MediaSpan(args.source_id, "audio", args.start_us, args.duration_us),)
    if not args.one_shot:
        run = run_resumable_analysis(
            project_root=root, source_id=args.source_id, spans=spans, media=media, model=model,
            score=score, steps=steps, adapter_license=args.adapter_license,
            adapter_version=args.adapter_version, model_license=args.model_license,
            score_license=args.score_license, job_id=args.job_id, worker_id=args.worker_id,
            lease_seconds=args.lease_seconds, resume=args.resume, enqueue_only=args.enqueue_only,
        )
        print_json(run.output)
        if args.enqueue_only and not args.resume:
            return 0
        return 0 if run.job.state.value in {"completed", "queued", "paused"} else 7
    result, speaker_alignment = run_one_shot_analysis(root, args.source_id, spans, steps)
    print_json({
        "artifacts": {
            "manifest": project_relative(root, result.manifest_path),
            "normalized": project_relative(root, result.normalized_path),
            "run_directory": project_relative(root, result.run_directory),
        }, "event_ids": list(result.event_ids), "network_used": False,
        "project_sha256": result.project_sha256, "run_id": result.run_id,
        "stage_states": dict(result.stage_states),
        "speaker_alignment_relation_ids": list(speaker_alignment.relation_ids),
        "target_ids": list(result.target_ids),
    })
    return 0


def analysis_job(args: argparse.Namespace) -> int:
    root = project_root(args.project)
    if args.recover_stale:
        print_json({"network_used": False, "recovered_job_count": recover_stale_analysis_jobs(root)})
        return 0
    if args.job_id is None:
        print_json({"jobs": [durable_job_output(job, root) for job in list_analysis_jobs(root)], "network_used": False})
        return 0
    print_json(durable_job_output(_analysis_job(root, args.job_id, cancel=args.cancel), root))
    return 0
