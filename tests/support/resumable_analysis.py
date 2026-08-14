from __future__ import annotations

import hashlib
from pathlib import Path

from notewitness.adapters.analysis_cli import (
    LocalAnalysisCLIExecution,
    LocalAnalysisCLISettings,
    LocalAnalysisSource,
    LocalAnalysisCLIAdapter,
)
from notewitness.application.resumable_analysis import ResumableAnalysisCoordinator, ResumableAnalysisStep
from notewitness.domain.analysis import (
    AnalysisBatch,
    AnalysisResult,
    AnalysisStage,
    AnalysisState,
    InstrumentHypothesis,
    NoteHypothesis,
)
from notewitness.domain.jobs import AnalysisJobSpec
from notewitness.domain.timeline import MediaSpan
from notewitness.infrastructure.sqlite_job_store import SQLiteJobStore
from notewitness.local_tools import LocalTool
from notewitness.media_ingest import ingest_media
from notewitness.project import initialize_project
from notewitness.project_store import ProjectStore


SHA = "a" * 64


def fixture(
    temporary: str,
    *,
    project: Path | None = None,
    store: SQLiteJobStore | None = None,
    crash_second: bool = False,
    lease_seconds: float = 30,
) -> tuple[
    Path,
    SQLiteJobStore,
    ResumableAnalysisCoordinator,
    AnalysisJobSpec,
    dict[AnalysisStage, int],
]:
    parent = Path(temporary).resolve()
    parent.chmod(0o700)
    if project is None:
        project = parent / "study"
        initialize_project(project)
        media_source = parent / "lesson.wav"
        media_source.write_bytes(b"resumable media")
        media_source.chmod(0o600)
        imported = ingest_media(project, media_source, create_restricted_rights=True)
    else:
        imported = type(
            "Imported",
            (),
            {"source_id": source_id(project), "relative_path": source_path(project)},
        )
    model = parent / "model.bin"
    if not model.exists():
        model.write_bytes(b"resumable model")
        model.chmod(0o600)
    source_identifier = str(getattr(imported, "source_id"))
    media_path = project / str(getattr(imported, "relative_path"))
    settings = LocalAnalysisCLISettings(
        working_directory=project,
        media=source(source_identifier, media_path),
        model=source("model:resumable", model),
        model_license="LicenseRef-test-model",
        adapter_license="MIT-test-adapter",
    )
    tool = parent / "tool"
    if not tool.exists():
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool.chmod(0o700)
    calls = {AnalysisStage.NOTE_TRANSCRIPTION: 0, AnalysisStage.INSTRUMENT_DETECTION: 0}
    steps = tuple(step(stage, settings, tool, calls, crash_second) for stage in calls)
    coordinator = ResumableAnalysisCoordinator(
        store or SQLiteJobStore(parent / "jobs.sqlite"),
        project,
        steps,
        owner_id="worker:a",
        lease_seconds=lease_seconds,
        adapter_fingerprint_sha256="b" * 64,
        runtime_fingerprint_sha256="c" * 64,
        settings_fingerprint_sha256="d" * 64,
        model_sha256=settings.model.sha256,
    )
    span = MediaSpan(source_identifier, "audio", 0, 1_000)
    spec = AnalysisJobSpec(
        "job:resumable", source_identifier, settings.media.sha256, tuple(calls), (span,), "b" * 64,
        "c" * 64, "d" * 64,
    )
    return project, coordinator._store, coordinator, spec, calls


def step(
    stage: AnalysisStage,
    settings: LocalAnalysisCLISettings,
    tool: Path,
    calls: dict[AnalysisStage, int],
    crash_second: bool,
) -> ResumableAnalysisStep:
    adapter = LocalAnalysisCLIAdapter(
        LocalTool("resumable-tool", tool), stage=stage, version="1",
        generator_id=f"generator:resumable-{stage.value}", settings=settings,
    )
    batch = analysis_batch(stage, settings.media.source_id)
    raw = stage.value.encode("utf-8")

    def execute(request: object, *, cancellation_requested: object = None) -> LocalAnalysisCLIExecution:
        calls[stage] += 1
        if crash_second and stage is AnalysisStage.INSTRUMENT_DETECTION:
            raise SystemExit("simulated process crash")
        return LocalAnalysisCLIExecution(batch, SHA, raw, hashlib.sha256(raw).hexdigest(), 1, True)

    adapter.execute = execute  # type: ignore[method-assign]
    adapter.replay = lambda request, output: batch  # type: ignore[method-assign]
    return ResumableAnalysisStep(adapter, {})


def analysis_batch(stage: AnalysisStage, source_identifier: str) -> AnalysisBatch:
    span = MediaSpan(source_identifier, "audio", 0, 1_000)
    if stage is AnalysisStage.NOTE_TRANSCRIPTION:
        hypothesis = NoteHypothesis("note:resumable", span, AnalysisState.READY, 69, 440, 0.9,
                                    "generator:resumable-note_transcription")
    else:
        hypothesis = InstrumentHypothesis("instrument:resumable", span, AnalysisState.READY,
                                          "piano", None, 0.8,
                                          "generator:resumable-instrument_detection")
    result = AnalysisResult(stage, AnalysisState.READY, (hypothesis.hypothesis_id,), ())
    return AnalysisBatch(result, (hypothesis,))


def configure_note_continuation(
    coordinator: ResumableAnalysisCoordinator,
    calls: dict[AnalysisStage, int],
    *,
    duplicate_id: bool = False,
) -> list[str | None]:
    """Configure two adapter responses whose raw payloads remain replayable."""
    adapter = coordinator._steps[0].adapter
    source_identifier = adapter.settings.media.source_id
    first_base = analysis_batch(AnalysisStage.NOTE_TRANSCRIPTION, source_identifier)
    first = AnalysisBatch(
        AnalysisResult(AnalysisStage.NOTE_TRANSCRIPTION, AnalysisState.INCOMPLETE,
                       first_base.result.hypothesis_ids, (), "resume:note:1"),
        first_base.hypotheses,
    )
    second = analysis_batch(AnalysisStage.NOTE_TRANSCRIPTION, source_identifier)
    second_hypothesis = NoteHypothesis(
        "note:resumable" if duplicate_id else "note:resumable:second",
        second.hypotheses[0].span,
        AnalysisState.READY,
        72, 523.25, 0.8, "generator:resumable-note_transcription",
    )
    second = AnalysisBatch(
        AnalysisResult(AnalysisStage.NOTE_TRANSCRIPTION, AnalysisState.READY,
                       (second_hypothesis.hypothesis_id,), ()),
        (second_hypothesis,),
    )
    requested: list[str | None] = []

    def execute(request: object, *, cancellation_requested: object = None) -> LocalAnalysisCLIExecution:
        token = getattr(request, "continuation_token")
        requested.append(token)
        calls[AnalysisStage.NOTE_TRANSCRIPTION] += 1
        raw = b"note-first" if token is None else b"note-second"
        batch = first if token is None else second
        return LocalAnalysisCLIExecution(batch, SHA, raw, hashlib.sha256(raw).hexdigest(), 1, True)

    def replay(request: object, raw: bytes) -> AnalysisBatch:
        return {b"note-first": first, b"note-second": second}[raw]

    adapter.execute = execute  # type: ignore[method-assign]
    adapter.replay = replay  # type: ignore[method-assign]
    return requested


def source(identifier: str, path: Path) -> LocalAnalysisSource:
    raw = path.read_bytes()
    return LocalAnalysisSource(identifier, path, hashlib.sha256(raw).hexdigest(), len(raw))


def source_id(project: Path) -> str:
    return str(ProjectStore(project).load().payload["sources"][0]["id"])


def source_path(project: Path) -> str:
    return str(ProjectStore(project).load().payload["sources"][0]["uri"])
