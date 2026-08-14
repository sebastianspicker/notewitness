"""Local music-analysis commands and durable analysis-job orchestration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
from typing import Any
from uuid import uuid4

from notewitness.adapters.analysis_cli import (
    LocalAnalysisCLIAdapter, LocalAnalysisCLISettings, LocalAnalysisSource,
    analysis_artifact_identity,
)
from notewitness.application.analysis_runtime import LocalAnalysisRunRequest, LocalAnalysisRuntime, LocalAnalysisStep
from notewitness.application.resumable_analysis import ResumableAnalysisCoordinator, ResumableAnalysisStep
from notewitness.application.speaker_alignment import align_speech_to_anonymous_speakers
from notewitness.domain.analysis import AnalysisStage
from notewitness.domain.jobs import AnalysisJobSpec, DurableJob
from notewitness.domain.timeline import MediaSpan
from notewitness.infrastructure.sqlite_job_store import SQLiteJobStore
from notewitness.local_tools import LocalTool, discover_local_tool
from notewitness.project_store import ProjectStore
from notewitness._prototype_parser import AUTOMATIC_ANALYSIS_STAGES
from notewitness._prototype_support import path_identity, print_json, project_relative, project_root, tool_identity_payload


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
    settings = LocalAnalysisCLISettings(
        working_directory=ProjectStore(root).root, media=media, model=model,
        model_license=args.model_license, adapter_license=args.adapter_license,
        timeout_seconds=args.timeout_seconds, score=score, score_license=args.score_license,
    )
    steps = tuple(
        LocalAnalysisStep(
            adapter=LocalAnalysisCLIAdapter(
                tool, stage=stage, version=args.adapter_version,
                generator_id=(
                    f"generator:analysis-{stage.value}-{tool.identity.sha256[:8]}-"
                    f"{model.sha256[:8]}-{json_sha256(analysis_parameters(args, stage))[:8]}"
                ), settings=settings,
            ), parameters=analysis_parameters(args, stage),
        )
        for stage in selected
    )
    if not args.one_shot:
        return run_resumable_analysis(args, project_root=root, media=media, model=model, score=score, steps=steps)
    result = LocalAnalysisRuntime().run(LocalAnalysisRunRequest(
        project_root=root, source_id=args.source_id,
        spans=(MediaSpan(args.source_id, "audio", args.start_us, args.duration_us),), steps=steps,
    ))
    speaker_alignment = align_speech_to_anonymous_speakers(root)
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


def run_resumable_analysis(
    args: argparse.Namespace, *, project_root: Path, media: LocalAnalysisSource,
    model: LocalAnalysisSource, score: LocalAnalysisSource | None,
    steps: tuple[LocalAnalysisStep, ...],
) -> int:
    job_id = args.job_id or f"job:analysis-{uuid4().hex}"
    worker_id = args.worker_id or f"worker:analysis-{os.getpid()}"
    spans = (MediaSpan(args.source_id, "audio", args.start_us, args.duration_us),)
    adapter_fingerprint = json_sha256({
        "adapter_license": args.adapter_license, "adapter_version": args.adapter_version,
        "executable": tool_identity_payload(steps[0].adapter.tool),
        "stages": [step.adapter.stage.value for step in steps],
    })
    runtime_fingerprint_sha256 = runtime_fingerprint()
    settings_fingerprint = json_sha256({
        "model_license": args.model_license, "model_sha256": model.sha256,
        "parameters": [dict(step.parameters) for step in steps],
        "score_license": args.score_license, "score_sha256": score.sha256 if score is not None else None,
        "spans": [{"duration_us": span.duration_us, "source_id": span.source_id, "start_us": span.start_us, "stream_id": span.stream_id} for span in spans],
    })
    job_store = SQLiteJobStore(ProjectStore(project_root).ensure_private_directory("runs") / "analysis-jobs.sqlite")
    coordinator = ResumableAnalysisCoordinator(
        job_store, project_root, tuple(ResumableAnalysisStep(step.adapter, step.parameters) for step in steps),
        owner_id=worker_id, lease_seconds=args.lease_seconds,
        adapter_fingerprint_sha256=adapter_fingerprint, runtime_fingerprint_sha256=runtime_fingerprint_sha256,
        settings_fingerprint_sha256=settings_fingerprint, model_sha256=model.sha256,
    )
    if not args.resume:
        spec = AnalysisJobSpec(
            job_id=job_id, source_id=args.source_id, source_sha256=media.sha256,
            stages=tuple(step.adapter.stage for step in steps), spans=spans,
            adapter_fingerprint_sha256=adapter_fingerprint,
            runtime_fingerprint_sha256=runtime_fingerprint_sha256,
            settings_fingerprint_sha256=settings_fingerprint,
            score_sha256=score.sha256 if score is not None else None,
        )
        queued = coordinator.enqueue(spec)
        if args.enqueue_only:
            print_json(durable_job_output(queued, project_root))
            return 0
    job_store.recover_stale_leases()
    finished = coordinator.run(job_id)
    current = finished or job_store.get(job_id)
    if current is None:
        from notewitness.application.resumable_analysis import ResumableAnalysisError
        raise ResumableAnalysisError("Durable analysis job does not exist.")
    output = durable_job_output(current, project_root)
    output["event_ids"] = resumable_event_ids(project_root, job_id)
    output["speaker_alignment_relation_ids"] = list(align_speech_to_anonymous_speakers(project_root).relation_ids) if current.state.value == "completed" else []
    print_json(output)
    return 0 if current.state.value in {"completed", "queued", "paused"} else 7


def analysis_job(args: argparse.Namespace) -> int:
    root = project_root(args.project)
    store = SQLiteJobStore(ProjectStore(root).ensure_private_directory("runs") / "analysis-jobs.sqlite")
    if args.recover_stale:
        print_json({"network_used": False, "recovered_job_count": store.recover_stale_leases()})
        return 0
    if args.job_id is None:
        print_json({"jobs": [durable_job_output(job, root) for job in store.list(limit=1_024)], "network_used": False})
        return 0
    job = store.request_cancellation(args.job_id) if args.cancel else store.get(args.job_id)
    if job is None:
        from notewitness.application.resumable_analysis import ResumableAnalysisError
        raise ResumableAnalysisError("Durable analysis job does not exist.")
    print_json(durable_job_output(job, root))
    return 0


def durable_job_output(job: DurableJob, root: Path) -> dict[str, Any]:
    token = hashlib.sha256(job.spec.job_id.encode("utf-8")).hexdigest()[:32]
    return {
        "artifacts": {"identity_manifest": f"runs/resumable-{token}/identity.json", "job_store": "runs/analysis-jobs.sqlite", "run_directory": f"runs/resumable-{token}"},
        "cancel_requested": job.cancel_requested,
        "checkpoint_stage": job.checkpoint_stage.value if job.checkpoint_stage is not None else None,
        "completed_span_count": job.completed_span_count, "job_id": job.spec.job_id,
        "network_used": False, "project": str(root), "source_id": job.spec.source_id,
        "stages": [stage.value for stage in job.spec.stages], "state": job.state.value,
    }


def resumable_event_ids(root: Path, job_id: str) -> list[str]:
    token = hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:32]
    prefix = f"event:analysis-{token}-"
    return sorted(str(item["id"]) for item in ProjectStore(root).load().payload["events"] if str(item.get("id", "")).startswith(prefix))


def json_sha256(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def runtime_fingerprint() -> str:
    files = (Path(sys.modules[ResumableAnalysisCoordinator.__module__].__file__ or ""), Path(sys.modules[LocalAnalysisCLIAdapter.__module__].__file__ or ""))
    digest = hashlib.sha256()
    for path in files:
        file_digest, size = path_identity(path)
        digest.update(path.name.encode("utf-8"))
        digest.update(file_digest.encode("ascii"))
        digest.update(str(size).encode("ascii"))
    return digest.hexdigest()


def analysis_parameters(args: argparse.Namespace, stage: AnalysisStage) -> dict[str, Any]:
    if stage is AnalysisStage.ANONYMOUS_DIARIZATION:
        return {"detect_overlap": args.detect_overlap, "diarization_mode": args.diarization_mode, "exact_speaker_count": args.exact_speaker_count}
    if stage is AnalysisStage.SCORE_ALIGNMENT:
        return {"score_id": args.score_id}
    return {}


def project_media_source(root: Path, source_id: str) -> LocalAnalysisSource:
    store = ProjectStore(root)
    matches = [item for item in store.load().payload["sources"] if item.get("id") == source_id]
    if len(matches) != 1:
        raise ValueError("Project media source is missing or ambiguous.")
    source = matches[0]
    uri = source.get("uri")
    if not isinstance(uri, str):
        raise ValueError("Project media source URI is invalid.")
    relative = PurePosixPath(uri)
    if relative.is_absolute() or "\\" in uri or not relative.parts or relative.parts[0] != "media" or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("Analysis requires project-controlled media.")
    path = store.root.joinpath(*relative.parts)
    digest, size = path_identity(path)
    if digest != source.get("sha256"):
        raise ValueError("Project media checksum does not match its source record.")
    return LocalAnalysisSource(source_id, path, digest, size)


def local_analysis_source(prefix: str, path: Path) -> LocalAnalysisSource:
    """Use adapter-specific artifact identity; do not substitute a plain checksum."""

    digest, size = analysis_artifact_identity(path)
    source_id = prefix if ":" in prefix else f"{prefix}:analysis-{digest[:32]}"
    return LocalAnalysisSource(source_id, path, digest, size)
