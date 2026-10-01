"""Analysis-suite job orchestration for local callers.

This module turns explicit, already-validated caller choices into adapter
steps, durable-job identities, and job runs. Delivery layers (CLI, workbench)
keep argument validation and output formatting; they do not derive generator
IDs, fingerprints, or run paths themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from uuid import uuid4

from notewitness.analysis.local_tools.discovery import LocalTool
from notewitness.analysis.speaker_alignment import (
    SpeakerAlignmentResult,
    align_speech_to_anonymous_speakers,
)
from notewitness.analysis.suite import (
    adapter,
    checkpoints,
    coordinator,
    evidence,
    identity,
    protocol,
)
from notewitness.analysis.suite.adapter import (
    LocalAnalysisCLIAdapter,
    LocalAnalysisCLISettings,
    LocalAnalysisSource,
)
from notewitness.analysis.suite.checkpoints import ResumableAnalysisError
from notewitness.analysis.suite.coordinator import (
    ResumableAnalysisCoordinator,
    ResumableAnalysisStep,
)
from notewitness.analysis.suite.identity import analysis_artifact_identity
from notewitness.analysis.suite.job_store import SQLiteJobStore
from notewitness.analysis.suite.runtime import (
    LocalAnalysisRunRequest,
    LocalAnalysisRunResult,
    LocalAnalysisRuntime,
    LocalAnalysisStep,
)
from notewitness.core.analysis import analysis as core_analysis
from notewitness.core.analysis.analysis import AnalysisStage
from notewitness.core.analysis.jobs import AnalysisJobSpec, DurableJob
from notewitness.core.time import MediaSpan
from notewitness.projects.media import is_project_media_uri
from notewitness.projects.store import ProjectStore


JOB_STORE_FILENAME = "analysis-jobs.sqlite"
MAX_LISTED_JOBS = 1_024

# The modules whose code determines resumable output. Their file name, digest,
# and size form the runtime fingerprint, so editing any of them rejects
# resumption of jobs enqueued before the change.
RESUMABLE_RUNTIME_MODULES = (
    coordinator,
    checkpoints,
    adapter,
    protocol,
    identity,
    evidence,
    core_analysis,
)


@dataclass(frozen=True, slots=True)
class ResumableAnalysisRun:
    """The durable job after an enqueue or run, and its JSON projection."""

    job: DurableJob
    output: dict[str, Any]


def analysis_generator_id(
    stage: AnalysisStage,
    tool_sha256: str,
    model_sha256: str,
    parameters_sha256: str,
) -> str:
    """Derive the generator ID for one stage from its provenance digests.

    Callers compute ``parameters_sha256`` with their own established encoding.
    """

    return (
        f"generator:analysis-{stage.value}-{tool_sha256[:8]}-"
        f"{model_sha256[:8]}-{parameters_sha256[:8]}"
    )


def stage_parameters(
    stage: AnalysisStage,
    *,
    diarization_mode: str,
    exact_speaker_count: int | None,
    detect_overlap: bool,
    score_id: str | None,
) -> dict[str, Any]:
    """Return the bounded request parameters one stage receives."""

    if stage is AnalysisStage.ANONYMOUS_DIARIZATION:
        return {
            "detect_overlap": detect_overlap,
            "diarization_mode": diarization_mode,
            "exact_speaker_count": exact_speaker_count,
        }
    if stage is AnalysisStage.SCORE_ALIGNMENT:
        return {"score_id": score_id}
    return {}


def analysis_steps(
    tool: LocalTool,
    stages: tuple[AnalysisStage, ...],
    *,
    project_root: Path,
    media: LocalAnalysisSource,
    model: LocalAnalysisSource,
    score: LocalAnalysisSource | None,
    model_license: str,
    adapter_license: str,
    adapter_version: str,
    score_license: str | None,
    score_id: str | None,
    timeout_seconds: int,
    diarization_mode: str,
    exact_speaker_count: int | None,
    detect_overlap: bool,
) -> tuple[LocalAnalysisStep, ...]:
    """Build one adapter step per selected stage with shared settings."""

    settings = LocalAnalysisCLISettings(
        working_directory=ProjectStore(project_root).root,
        media=media,
        model=model,
        model_license=model_license,
        adapter_license=adapter_license,
        timeout_seconds=timeout_seconds,
        score=score,
        score_license=score_license,
    )
    steps: list[LocalAnalysisStep] = []
    for stage in stages:
        parameters = stage_parameters(
            stage,
            diarization_mode=diarization_mode,
            exact_speaker_count=exact_speaker_count,
            detect_overlap=detect_overlap,
            score_id=score_id,
        )
        steps.append(
            LocalAnalysisStep(
                adapter=LocalAnalysisCLIAdapter(
                    tool,
                    stage=stage,
                    version=adapter_version,
                    generator_id=analysis_generator_id(
                        stage,
                        tool.identity.sha256,
                        model.sha256,
                        _json_sha256(parameters),
                    ),
                    settings=settings,
                ),
                parameters=parameters,
            )
        )
    return tuple(steps)


def project_media_source(root: Path, source_id: str) -> LocalAnalysisSource:
    """Resolve the analysis media source named by a project source record.

    This is deliberately more permissive than the runtimes' resolver in
    ``notewitness.analysis.runs.workspace``: nested ``media/`` paths are
    accepted and symlinks are followed when hashing. Keep the two separate;
    this one only identifies the media a caller is about to configure.
    """

    store = ProjectStore(root)
    matches = [
        item for item in store.load().payload["sources"] if item.get("id") == source_id
    ]
    if len(matches) != 1:
        raise ValueError("Project media source is missing or ambiguous.")
    source = matches[0]
    uri = source.get("uri")
    if not isinstance(uri, str):
        raise ValueError("Project media source URI is invalid.")
    if not is_project_media_uri(uri, nested=True):
        raise ValueError("Analysis requires project-controlled media.")
    path = store.root.joinpath(*PurePosixPath(uri).parts)
    digest, size = _path_identity(path)
    if digest != source.get("sha256"):
        raise ValueError("Project media checksum does not match its source record.")
    return LocalAnalysisSource(source_id, path, digest, size)


def local_analysis_source(prefix: str, path: Path) -> LocalAnalysisSource:
    """Use adapter-specific artifact identity; do not substitute a plain checksum."""

    digest, size = analysis_artifact_identity(path)
    source_id = prefix if ":" in prefix else f"{prefix}:analysis-{digest[:32]}"
    return LocalAnalysisSource(source_id, path, digest, size)


def run_one_shot_analysis(
    project_root: Path,
    source_id: str,
    spans: tuple[MediaSpan, ...],
    steps: tuple[LocalAnalysisStep, ...],
) -> tuple[LocalAnalysisRunResult, SpeakerAlignmentResult]:
    """Run all steps once, publish them, then align speech to speakers."""

    result = LocalAnalysisRuntime().run(
        LocalAnalysisRunRequest(
            project_root=project_root,
            source_id=source_id,
            spans=spans,
            steps=steps,
        )
    )
    return result, align_speech_to_anonymous_speakers(project_root)


def run_resumable_analysis(
    *,
    project_root: Path,
    source_id: str,
    spans: tuple[MediaSpan, ...],
    media: LocalAnalysisSource,
    model: LocalAnalysisSource,
    score: LocalAnalysisSource | None,
    steps: tuple[LocalAnalysisStep, ...],
    adapter_license: str,
    adapter_version: str,
    model_license: str,
    score_license: str | None,
    job_id: str | None,
    worker_id: str | None,
    lease_seconds: float,
    resume: bool,
    enqueue_only: bool,
) -> ResumableAnalysisRun:
    """Enqueue and/or run one durable job; resume reuses the persisted spec."""

    job_id = job_id or f"job:analysis-{uuid4().hex}"
    worker_id = worker_id or f"worker:analysis-{os.getpid()}"
    adapter_fingerprint = _json_sha256({
        "adapter_license": adapter_license,
        "adapter_version": adapter_version,
        "executable": _tool_identity_payload(steps[0].adapter.tool),
        "stages": [step.adapter.stage.value for step in steps],
    })
    runtime_fingerprint_sha256 = runtime_fingerprint()
    settings_fingerprint = _json_sha256({
        "model_license": model_license,
        "model_sha256": model.sha256,
        "parameters": [dict(step.parameters) for step in steps],
        "score_license": score_license,
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
    })
    job_store = analysis_job_store(project_root)
    resumable = ResumableAnalysisCoordinator(
        job_store,
        project_root,
        tuple(ResumableAnalysisStep(step.adapter, step.parameters) for step in steps),
        owner_id=worker_id,
        lease_seconds=lease_seconds,
        adapter_fingerprint_sha256=adapter_fingerprint,
        runtime_fingerprint_sha256=runtime_fingerprint_sha256,
        settings_fingerprint_sha256=settings_fingerprint,
        model_sha256=model.sha256,
    )
    if not resume:
        spec = AnalysisJobSpec(
            job_id=job_id,
            source_id=source_id,
            source_sha256=media.sha256,
            stages=tuple(step.adapter.stage for step in steps),
            spans=spans,
            adapter_fingerprint_sha256=adapter_fingerprint,
            runtime_fingerprint_sha256=runtime_fingerprint_sha256,
            settings_fingerprint_sha256=settings_fingerprint,
            score_sha256=score.sha256 if score is not None else None,
        )
        queued = resumable.enqueue(spec)
        if enqueue_only:
            return ResumableAnalysisRun(queued, durable_job_output(queued, project_root))
    job_store.recover_stale_leases()
    finished = resumable.run(job_id)
    current = _require_job(finished or job_store.get(job_id))
    output = durable_job_output(current, project_root)
    output["event_ids"] = resumable_event_ids(project_root, job_id)
    output["speaker_alignment_relation_ids"] = (
        list(align_speech_to_anonymous_speakers(project_root).relation_ids)
        if current.state.value == "completed"
        else []
    )
    return ResumableAnalysisRun(current, output)


def analysis_job_store(project_root: Path) -> SQLiteJobStore:
    """Open the project's durable analysis-job store under ``runs/``."""

    runs = ProjectStore(project_root).ensure_private_directory("runs")
    return SQLiteJobStore(runs / JOB_STORE_FILENAME)


def recover_stale_analysis_jobs(project_root: Path) -> int:
    return analysis_job_store(project_root).recover_stale_leases()


def list_analysis_jobs(project_root: Path) -> tuple[DurableJob, ...]:
    return tuple(analysis_job_store(project_root).list(limit=MAX_LISTED_JOBS))


def analysis_job(project_root: Path, job_id: str, *, cancel: bool) -> DurableJob:
    """Read one durable job, optionally requesting its cancellation first."""

    store = analysis_job_store(project_root)
    return _require_job(store.request_cancellation(job_id) if cancel else store.get(job_id))


def durable_job_output(job: DurableJob, root: Path) -> dict[str, Any]:
    """Project one durable job into its stable JSON summary."""

    token = _resumable_token(job.spec.job_id)
    return {
        "artifacts": {
            "identity_manifest": f"runs/resumable-{token}/identity.json",
            "job_store": f"runs/{JOB_STORE_FILENAME}",
            "run_directory": f"runs/resumable-{token}",
        },
        "cancel_requested": job.cancel_requested,
        "checkpoint_stage": (
            job.checkpoint_stage.value if job.checkpoint_stage is not None else None
        ),
        "completed_span_count": job.completed_span_count,
        "job_id": job.spec.job_id,
        "network_used": False,
        "project": str(root),
        "source_id": job.spec.source_id,
        "stages": [stage.value for stage in job.spec.stages],
        "state": job.state.value,
    }


def resumable_event_ids(root: Path, job_id: str) -> list[str]:
    """Return the project event IDs published by one durable job."""

    prefix = f"event:analysis-{_resumable_token(job_id)}-"
    return sorted(
        str(item["id"])
        for item in ProjectStore(root).load().payload["events"]
        if str(item.get("id", "")).startswith(prefix)
    )


def runtime_fingerprint() -> str:
    """Fingerprint the code in ``RESUMABLE_RUNTIME_MODULES``, in tuple order."""

    digest = hashlib.sha256()
    for module in RESUMABLE_RUNTIME_MODULES:
        path = Path(module.__file__ or "")
        file_digest, size = _path_identity(path)
        digest.update(path.name.encode("utf-8"))
        digest.update(file_digest.encode("ascii"))
        digest.update(str(size).encode("ascii"))
    return digest.hexdigest()


def _require_job(job: DurableJob | None) -> DurableJob:
    if job is None:
        raise ResumableAnalysisError("Durable analysis job does not exist.")
    return job


def _resumable_token(job_id: str) -> str:
    return hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:32]


def _json_sha256(payload: object) -> str:
    raw = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _path_identity(path: Path) -> tuple[str, int]:
    """Hash a non-empty local file for simple source-checksum comparisons."""

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


def _tool_identity_payload(tool: LocalTool) -> Mapping[str, int | str]:
    """Keep durable-job fingerprints tied to the discovered executable identity."""

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
