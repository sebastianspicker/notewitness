"""Durable local execution and evidence publication for analysis CLI stages."""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

from notewitness.adapters.analysis_cli import (
    AnalysisCLIExecutionError,
    LocalAnalysisCLIAdapter,
    LocalAnalysisCLIExecution,
)
from notewitness.application.analysis_evidence import (
    AnalysisEvidenceContext,
    AnalysisEvidenceRecords,
    append_analysis_batches,
    require_unique_hypothesis_ids,
)
from notewitness.application.run_integration import (
    RunPublication,
    capture_source_identity,
    completed_artifact_sha256s,
    integrate_completed_run,
    select_publication_records,
    write_completed_publication,
)
from notewitness.domain.analysis import (
    AnalysisRequest,
    AnalysisStage,
    AnalysisState,
    NoteHypothesis,
    PitchPointHypothesis,
)
from notewitness.domain.timeline import MediaSpan
from notewitness.local_artifacts import (
    write_new_private_bytes,
    write_new_private_json,
)
from notewitness.project_store import ProjectSnapshot, ProjectStore


MAX_ANALYSIS_STAGES = 16
_PUBLISHABLE_STATES = frozenset(
    {
        AnalysisState.READY,
        AnalysisState.UNKNOWN,
        AnalysisState.NOT_DETECTED,
        AnalysisState.NOT_APPLICABLE,
        AnalysisState.NOT_ALIGNABLE,
        AnalysisState.UNCERTAIN,
    }
)


class LocalAnalysisRuntimeError(RuntimeError):
    """A local analysis run could not complete its durable contract."""


@dataclass(frozen=True, slots=True)
class LocalAnalysisStep:
    adapter: LocalAnalysisCLIAdapter
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.adapter, LocalAnalysisCLIAdapter):
            raise ValueError("Analysis steps require a LocalAnalysisCLIAdapter.")
        if not isinstance(self.parameters, Mapping):
            raise ValueError("Analysis step parameters must be a mapping.")


@dataclass(frozen=True, slots=True)
class LocalAnalysisRunRequest:
    project_root: Path
    source_id: str
    spans: tuple[MediaSpan, ...]
    steps: tuple[LocalAnalysisStep, ...]
    run_token: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.project_root, Path):
            raise ValueError("project_root must be a Path.")
        if not isinstance(self.source_id, str) or not self.source_id:
            raise ValueError("Analysis runs require a source ID.")
        if not self.spans or any(
            not isinstance(span, MediaSpan) or span.source_id != self.source_id
            for span in self.spans
        ):
            raise ValueError("Analysis spans must belong to the selected source.")
        if not self.steps or len(self.steps) > MAX_ANALYSIS_STAGES:
            raise ValueError(
                f"Analysis runs require 1-{MAX_ANALYSIS_STAGES} steps."
            )
        stages = tuple(step.adapter.stage for step in self.steps)
        if len(stages) != len(set(stages)):
            raise ValueError("Analysis run stages must be unique.")
        if self.run_token is not None and not _valid_run_token(self.run_token):
            raise ValueError("run_token must be exactly 32 lowercase hexadecimal characters.")


@dataclass(frozen=True, slots=True)
class LocalAnalysisRunResult:
    run_id: str
    run_directory: Path
    manifest_path: Path
    normalized_path: Path
    event_ids: tuple[str, ...]
    target_ids: tuple[str, ...]
    project_sha256: str
    stage_states: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class _ExecutedStep:
    step: LocalAnalysisStep
    execution: LocalAnalysisCLIExecution
    raw_path: Path
    raw_artifact_id: str
    executable_sha256: str
    executable_size_bytes: int


class LocalAnalysisRuntime:
    """Run explicit local engines, retain raw output, then append suggestions."""

    def run(
        self,
        request: LocalAnalysisRunRequest,
        *,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> LocalAnalysisRunResult:
        store = ProjectStore(request.project_root)
        snapshot = store.load()
        source, media_path = _source_media(store, snapshot, request.source_id)
        source_identity = capture_source_identity(snapshot.payload, request.source_id)
        expected_sha256 = str(source["sha256"])
        _require_checksum(media_path, expected_sha256)
        _validate_steps(request, media_path, expected_sha256)

        token = request.run_token or uuid4().hex
        run_id = f"run:analysis-{token}"
        run_directory = _create_run_directory(store, token)
        raw_directory = _create_private_directory(run_directory, "raw")
        write_new_private_json(
            run_directory / "status.queued.json",
            {
                "run_id": run_id,
                "source_id": request.source_id,
                "state": "queued",
                "timestamp": _now(),
                "network_mode": "offline",
            },
        )

        manifest_written = False
        try:
            executed: list[_ExecutedStep] = []
            for index, step in enumerate(request.steps, start=1):
                stage = step.adapter.stage
                executable_before = _file_identity(step.adapter.tool.executable)
                try:
                    cancellation_options = (
                        {"cancellation_requested": cancellation_requested}
                        if cancellation_requested is not None
                        else {}
                    )
                    execution = step.adapter.execute(
                        AnalysisRequest(
                            job_id=run_id,
                            source_id=request.source_id,
                            spans=request.spans,
                            parameters=step.parameters,
                        ),
                        **cancellation_options,
                    )
                except AnalysisCLIExecutionError as exc:
                    failed_raw_path = (
                        raw_directory / f"{index:02d}-{stage.value}.failed.json"
                    )
                    write_new_private_bytes(failed_raw_path, exc.raw_output)
                    raise
                executable_after = _file_identity(step.adapter.tool.executable)
                if executable_before != executable_after:
                    raise LocalAnalysisRuntimeError(
                        f"Analysis executable changed during {stage.value}."
                    )
                if execution.batch.result.state not in _PUBLISHABLE_STATES:
                    raise LocalAnalysisRuntimeError(
                        f"Analysis stage {stage.value} ended in "
                        f"{execution.batch.result.state.value}."
                    )
                raw_path = raw_directory / f"{index:02d}-{stage.value}.json"
                write_new_private_bytes(raw_path, execution.raw_output)
                executed.append(
                    _ExecutedStep(
                        step=step,
                        execution=execution,
                        raw_path=raw_path,
                        raw_artifact_id=(
                            f"artifact:analysis-{token}-{stage.value}"
                        ),
                        executable_sha256=executable_before[0],
                        executable_size_bytes=executable_before[1],
                    )
                )
                _require_checksum(media_path, expected_sha256)

            require_unique_hypothesis_ids(
                item.execution.batch for item in executed
            )
            normalized_path = run_directory / "analysis.normalized.json"
            write_new_private_json(
                normalized_path,
                {
                    "run_id": run_id,
                    "source_id": request.source_id,
                    "stages": [_normalized_step(item) for item in executed],
                },
            )
            normalized_identity = _file_identity(normalized_path)
            manifest_path = run_directory / "manifest.completed.json"
            write_new_private_json(
                manifest_path,
                _manifest(
                    run_id=run_id,
                    source_id=request.source_id,
                    source_sha256=expected_sha256,
                    executed=executed,
                    normalized_identity=normalized_identity,
                ),
            )
            manifest_written = True

            projected = copy.deepcopy(snapshot.payload)
            records: list[AnalysisEvidenceRecords] = []
            known_note_or_pitch_ids = tuple(
                hypothesis.hypothesis_id
                for item in executed
                for hypothesis in item.execution.batch.hypotheses
                if isinstance(hypothesis, (NoteHypothesis, PitchPointHypothesis))
            )

            for index, item in enumerate(executed, start=1):
                settings = item.step.adapter.settings
                records.append(
                    append_analysis_batches(
                        projected,
                        source_id=request.source_id,
                        batches=(item.execution.batch,),
                        context=AnalysisEvidenceContext(
                            run_token=(
                                f"{token}-{index}-{item.step.adapter.stage.value}"
                            ),
                            generator_id=item.step.adapter.generator_id,
                            generator_name=item.step.adapter.name,
                            generator_version=item.step.adapter.version,
                            model_name=settings.model.source_id,
                            weight_hash_state=f"sha256:{settings.model.sha256}",
                            raw_artifact_id=item.raw_artifact_id,
                            raw_artifact_sha256=item.execution.raw_output_sha256,
                            raw_artifact_size_bytes=len(item.execution.raw_output),
                            parameters={
                                "adapter_license": settings.adapter_license,
                                "code_sha256": item.executable_sha256,
                                "effective_stage_parameters": dict(
                                    item.step.parameters
                                ),
                                "model_license": settings.model_license,
                                "network_requirement": "none",
                            },
                            analysis_run_id=run_id,
                        ),
                        known_note_or_pitch_ids=known_note_or_pitch_ids,
                    )
                )
            event_ids = tuple(
                event_id for item in records for event_id in item.event_ids
            )
            target_ids = tuple(
                target_id for item in records for target_id in item.target_ids
            )
            publication = RunPublication(
                kind="analysis",
                run_id=run_id,
                source=source_identity,
                model_sha256s=tuple(
                    sorted(
                        {
                            item.step.adapter.settings.model.sha256
                            for item in executed
                        }
                    )
                ),
                artifact_sha256s=completed_artifact_sha256s(
                    run_directory,
                    (
                        "manifest.completed.json",
                        "analysis.normalized.json",
                        *(
                            item.raw_path.relative_to(run_directory).as_posix()
                            for item in executed
                        ),
                    ),
                ),
                records=select_publication_records(
                    projected,
                    actor_ids=(
                        item.actor_id
                        for item in records
                        if item.actor_id is not None
                    ),
                    generator_ids=(item.generator_id for item in records),
                    target_ids=target_ids,
                    event_ids=event_ids,
                ),
            )
            write_completed_publication(run_directory, publication)
            integrated = integrate_completed_run(request.project_root, run_id)
            _write_completed_status(
                run_directory,
                event_count=len(integrated.event_ids),
                run_id=run_id,
                source_id=request.source_id,
                stage_count=len(executed),
            )
            return LocalAnalysisRunResult(
                run_id=run_id,
                run_directory=run_directory,
                manifest_path=manifest_path,
                normalized_path=normalized_path,
                event_ids=integrated.event_ids,
                target_ids=integrated.target_ids,
                project_sha256=integrated.project_sha256,
                stage_states={
                    item.step.adapter.stage.value: (
                        item.execution.batch.result.state.value
                    )
                    for item in executed
                },
            )
        except Exception as exc:
            status_name = (
                "status.integration-failed.json"
                if manifest_written
                else "status.failed.json"
            )
            try:
                write_new_private_json(
                    run_directory / status_name,
                    {
                        "failure_code": type(exc).__name__,
                        "run_id": run_id,
                        "source_id": request.source_id,
                        "state": (
                            "integration_failed" if manifest_written else "failed"
                        ),
                        "timestamp": _now(),
                    },
                )
            except Exception:
                pass
            if isinstance(exc, LocalAnalysisRuntimeError):
                raise
            raise LocalAnalysisRuntimeError("Local analysis run failed.") from exc


def _validate_steps(
    request: LocalAnalysisRunRequest,
    media_path: Path,
    expected_sha256: str,
) -> None:
    generator_records: dict[str, tuple[Any, ...]] = {}
    for step in request.steps:
        settings = step.adapter.settings
        if (
            settings.media.source_id != request.source_id
            or settings.media.sha256 != expected_sha256
            or settings.media.path != media_path
        ):
            raise LocalAnalysisRuntimeError(
                "Analysis adapter media identity does not match the project source."
            )
        if (
            step.adapter.stage is AnalysisStage.SCORE_ALIGNMENT
            and settings.score is None
        ):
            raise LocalAnalysisRuntimeError(
                "Score alignment requires an explicit local score artifact."
            )
        identity = (
            step.adapter.name,
            step.adapter.version,
            settings.model.source_id,
            settings.model.sha256,
            settings.model_license,
            settings.adapter_license,
            _file_identity(step.adapter.tool.executable),
        )
        previous = generator_records.setdefault(step.adapter.generator_id, identity)
        if previous != identity:
            raise LocalAnalysisRuntimeError(
                "One generator ID cannot describe different adapter provenance."
            )


def _source_media(
    store: ProjectStore,
    snapshot: ProjectSnapshot,
    source_id: str,
) -> tuple[dict[str, Any], Path]:
    matches = [
        item
        for item in snapshot.payload.get("sources", [])
        if isinstance(item, dict) and item.get("id") == source_id
    ]
    if len(matches) != 1:
        raise LocalAnalysisRuntimeError("Analysis source is missing or ambiguous.")
    source = matches[0]
    uri = source.get("uri")
    if not isinstance(uri, str):
        raise LocalAnalysisRuntimeError("Analysis source URI is invalid.")
    relative = PurePosixPath(uri)
    if (
        relative.is_absolute()
        or "\\" in uri
        or not relative.parts
        or relative.parts[0] != "media"
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise LocalAnalysisRuntimeError(
            "Analysis source must be project-controlled media."
        )
    media_path = store.root.joinpath(*relative.parts)
    try:
        metadata = media_path.lstat()
    except OSError as exc:
        raise LocalAnalysisRuntimeError("Analysis media is unavailable.") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise LocalAnalysisRuntimeError(
            "Analysis media must be an owner-private regular file."
        )
    return source, media_path


def _require_checksum(path: Path, expected: str) -> None:
    digest, _ = _file_identity(path)
    if digest != expected:
        raise LocalAnalysisRuntimeError("Analysis media checksum changed.")


def _file_identity(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as input_file:
            for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise LocalAnalysisRuntimeError("Analysis artifact could not be read.") from exc
    return digest.hexdigest(), size


def _create_run_directory(store: ProjectStore, token: str) -> Path:
    runs = store.ensure_private_directory("runs")
    directory = runs / f"analysis-{token}"
    try:
        directory.mkdir(mode=0o700)
    except OSError as exc:
        raise LocalAnalysisRuntimeError("Analysis run directory could not be created.") from exc
    directory.chmod(0o700)
    return directory


def _valid_run_token(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 32
        and all(character in "0123456789abcdef" for character in value)
    )


def _create_private_directory(parent: Path, name: str) -> Path:
    directory = parent / name
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    return directory


def _normalized_step(item: _ExecutedStep) -> dict[str, Any]:
    return {
        "batch": asdict(item.execution.batch),
        "duration_ms": item.execution.duration_ms,
        "effective_parameters": dict(item.step.parameters),
        "network_isolated": item.execution.network_isolated,
        "raw_artifact_id": item.raw_artifact_id,
        "raw_output_sha256": item.execution.raw_output_sha256,
        "raw_output_size_bytes": len(item.execution.raw_output),
        "request_sha256": item.execution.request_sha256,
        "stage": item.step.adapter.stage.value,
    }


def _manifest(
    *,
    run_id: str,
    source_id: str,
    source_sha256: str,
    executed: Iterable[_ExecutedStep],
    normalized_identity: tuple[str, int],
) -> dict[str, Any]:
    items = tuple(executed)
    return {
        "finished_at": _now(),
        "network_mode": "offline",
        "normalized_sha256": normalized_identity[0],
        "normalized_size_bytes": normalized_identity[1],
        "run_id": run_id,
        "source_id": source_id,
        "source_sha256": source_sha256,
        "stages": [
            {
                "adapter_id": item.step.adapter.generator_id,
                "adapter_license": item.step.adapter.settings.adapter_license,
                "adapter_version": item.step.adapter.version,
                "code_sha256": item.executable_sha256,
                "code_size_bytes": item.executable_size_bytes,
                "effective_parameters": dict(item.step.parameters),
                "model_artifact_id": item.step.adapter.settings.model.source_id,
                "model_license": item.step.adapter.settings.model_license,
                "model_sha256": item.step.adapter.settings.model.sha256,
                "raw_artifact_id": item.raw_artifact_id,
                "raw_output_sha256": item.execution.raw_output_sha256,
                "raw_output_size_bytes": len(item.execution.raw_output),
                "request_sha256": item.execution.request_sha256,
                "stage": item.step.adapter.stage.value,
                "state": item.execution.batch.result.state.value,
                "score": (
                    {
                        "artifact_id": item.step.adapter.settings.score.source_id,
                        "license": item.step.adapter.settings.score_license,
                        "sha256": item.step.adapter.settings.score.sha256,
                        "size_bytes": item.step.adapter.settings.score.size_bytes,
                    }
                    if item.step.adapter.settings.score is not None
                    else None
                ),
            }
            for item in items
        ],
        "state": "completed",
    }


def _write_completed_status(
    run_directory: Path,
    *,
    event_count: int,
    run_id: str,
    source_id: str,
    stage_count: int,
) -> None:
    try:
        write_new_private_json(
            run_directory / "status.completed.json",
            {
                "event_count": event_count,
                "run_id": run_id,
                "source_id": source_id,
                "stage_count": stage_count,
                "state": "completed",
                "timestamp": _now(),
            },
        )
    except Exception:
        # The immutable publication and committed graph are authoritative.
        return


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
