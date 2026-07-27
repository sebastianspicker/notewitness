from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest

from notewitness.adapters.analysis_cli import (
    AnalysisCLICancelled,
    AnalysisCLIExecutionError,
    LocalAnalysisCLIAdapter,
    LocalAnalysisCLIExecution,
    LocalAnalysisCLISettings,
    LocalAnalysisSource,
)
from notewitness.application.resumable_analysis import (
    ResumableAnalysisCoordinator,
    ResumableAnalysisStep,
)
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
from notewitness.infrastructure.sqlite_job_store import JobConflictError, SQLiteJobStore
from notewitness.local_tools import LocalTool
from notewitness.media_ingest import ingest_media
from notewitness.project import initialize_project
from notewitness.project_store import ProjectStore


SHA = "a" * 64


class ResumableAnalysisTests(unittest.TestCase):
    def test_crash_after_raw_write_replays_without_invoking_stage_again(self) -> None:
        with TemporaryDirectory() as temporary:
            project, store, coordinator, spec, calls = _fixture(temporary)
            coordinator.enqueue(spec)
            checkpoint = store.checkpoint

            def crash_before_checkpoint(*args: object, **kwargs: object) -> object:
                raise SystemExit("simulated crash after raw persistence")

            store.checkpoint = crash_before_checkpoint  # type: ignore[method-assign]
            with self.assertRaises(SystemExit):
                coordinator.run(spec.job_id)
            store.checkpoint = checkpoint  # type: ignore[method-assign]
            self.assertEqual(1, calls[AnalysisStage.NOTE_TRANSCRIPTION])
            store.recover_stale_leases(now=time.time() + 31)

            _, _, resumed, _, resumed_calls = _fixture(
                temporary,
                project=project,
                store=store,
            )
            finished = resumed.run(spec.job_id)

            self.assertEqual("completed", finished.state.value if finished else None)
            self.assertEqual(0, resumed_calls[AnalysisStage.NOTE_TRANSCRIPTION])
            self.assertEqual(1, resumed_calls[AnalysisStage.INSTRUMENT_DETECTION])

    def test_stale_lease_replays_raw_without_rerunning_completed_stage(self) -> None:
        with TemporaryDirectory() as temporary:
            project, store, coordinator, spec, calls = _fixture(temporary, crash_second=True)
            coordinator.enqueue(spec)
            with self.assertRaises(SystemExit):
                coordinator.run(spec.job_id)
            self.assertEqual(1, calls[AnalysisStage.NOTE_TRANSCRIPTION])
            self.assertEqual(1, store.recover_stale_leases(now=time.time() + 31))

            _, _, resumed, _, resumed_calls = _fixture(
                temporary, project=project, store=store
            )
            finished = resumed.run(spec.job_id)
            self.assertEqual("completed", finished.state.value if finished else None)
            self.assertEqual(0, resumed_calls[AnalysisStage.NOTE_TRANSCRIPTION])
            self.assertEqual(1, resumed_calls[AnalysisStage.INSTRUMENT_DETECTION])
            events = ProjectStore(project).load().payload["events"]
            self.assertEqual(2, len(events))
            self.assertIsNone(resumed.run(spec.job_id))
            self.assertEqual(2, len(ProjectStore(project).load().payload["events"]))

    def test_identity_mismatch_refuses_resume(self) -> None:
        with TemporaryDirectory() as temporary:
            project, store, coordinator, spec, _ = _fixture(temporary)
            coordinator.enqueue(spec)
            changed = ResumableAnalysisCoordinator(
                store,
                project,
                coordinator._steps,
                owner_id="worker:b",
                lease_seconds=30,
                adapter_fingerprint_sha256="f" * 64,
                runtime_fingerprint_sha256="c" * 64,
                settings_fingerprint_sha256="d" * 64,
                model_sha256=coordinator._model_sha256,
            )
            with self.assertRaises(JobConflictError):
                changed.run(spec.job_id)

    def test_publication_is_not_repeated_after_completion_crash(self) -> None:
        with TemporaryDirectory() as temporary:
            project, store, coordinator, spec, calls = _fixture(temporary)
            coordinator.enqueue(spec)
            complete = store.complete

            def crash_before_completion(*args: object, **kwargs: object) -> object:
                raise SystemExit("simulated crash after evidence publication")

            store.complete = crash_before_completion  # type: ignore[method-assign]
            with self.assertRaises(SystemExit):
                coordinator.run(spec.job_id)
            store.complete = complete  # type: ignore[method-assign]
            self.assertEqual(2, len(ProjectStore(project).load().payload["events"]))
            store.recover_stale_leases(now=time.time() + 31)
            finished = coordinator.run(spec.job_id)
            self.assertEqual("completed", finished.state.value if finished else None)
            self.assertEqual(1, calls[AnalysisStage.NOTE_TRANSCRIPTION])
            self.assertEqual(1, calls[AnalysisStage.INSTRUMENT_DETECTION])
            self.assertEqual(2, len(ProjectStore(project).load().payload["events"]))

    def test_long_stage_renews_lease_before_competing_recovery(self) -> None:
        with TemporaryDirectory() as temporary:
            project, store, coordinator, spec, calls = _fixture(
                temporary,
                lease_seconds=0.15,
            )
            coordinator.enqueue(spec)
            started = threading.Event()
            release = threading.Event()
            adapter = coordinator._steps[0].adapter
            execute = adapter.execute

            def long_execute(
                request: object,
                *,
                cancellation_requested: object = None,
            ) -> LocalAnalysisCLIExecution:
                started.set()
                self.assertTrue(release.wait(2))
                return execute(  # type: ignore[call-arg]
                    request,
                    cancellation_requested=cancellation_requested,
                )

            adapter.execute = long_execute  # type: ignore[method-assign]
            result: list[object] = []
            worker = threading.Thread(target=lambda: result.append(coordinator.run(spec.job_id)))
            worker.start()
            self.assertTrue(started.wait(1))
            time.sleep(0.2)
            self.assertEqual(0, store.recover_stale_leases())
            competing = ResumableAnalysisCoordinator(
                store,
                project,
                coordinator._steps,
                owner_id="worker:b",
                lease_seconds=0.15,
                adapter_fingerprint_sha256=coordinator._adapter_fingerprint_sha256,
                runtime_fingerprint_sha256=coordinator._runtime_fingerprint_sha256,
                settings_fingerprint_sha256=coordinator._settings_fingerprint_sha256,
                model_sha256=coordinator._model_sha256,
            )
            self.assertIsNone(competing.run(spec.job_id))
            release.set()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(1, calls[AnalysisStage.NOTE_TRANSCRIPTION])
            self.assertEqual("completed", getattr(result[0], "state").value)

    def test_running_stage_cancellation_preserves_checkpoint_without_publication(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            project, store, coordinator, spec, calls = _fixture(temporary)
            _configure_note_continuation(coordinator, calls)
            adapter = coordinator._steps[0].adapter
            execute = adapter.execute

            def cancel_continuation(
                request: object,
                *,
                cancellation_requested: object = None,
            ) -> LocalAnalysisCLIExecution:
                if getattr(request, "continuation_token") is None:
                    return execute(  # type: ignore[call-arg]
                        request,
                        cancellation_requested=cancellation_requested,
                    )
                store.request_cancellation(spec.job_id)
                self.assertTrue(callable(cancellation_requested))
                self.assertTrue(cancellation_requested())  # type: ignore[operator]
                raise AnalysisCLICancelled("fixture cancellation")

            adapter.execute = cancel_continuation  # type: ignore[method-assign]
            coordinator.enqueue(spec)

            finished = coordinator.run(spec.job_id)

            self.assertEqual("cancelled", finished.state.value if finished else None)
            self.assertIsNone(finished.failure_reason if finished else "missing")
            self.assertEqual(
                AnalysisStage.NOTE_TRANSCRIPTION,
                finished.checkpoint_stage if finished else None,
            )
            self.assertEqual(
                "resume:note:1",
                finished.continuation_token if finished else None,
            )
            self.assertIsNotNone(finished.last_artifact_id if finished else None)
            run_directory = coordinator._run_directory(spec.job_id)
            self.assertEqual(
                ["note_transcription.chunk-001.raw.json"],
                sorted(path.name for path in run_directory.glob("*.raw.json")),
            )
            self.assertEqual([], ProjectStore(project).load().payload["events"])
            self.assertIsNone(coordinator.run(spec.job_id))

            _, _, replacement, replacement_spec, replacement_calls = _fixture(
                temporary,
                project=project,
                store=store,
            )
            replacement_spec = replace(
                replacement_spec,
                job_id="job:resumable:replacement",
            )
            replacement.enqueue(replacement_spec)
            replacement_result = replacement.run(replacement_spec.job_id)
            self.assertEqual(
                "completed",
                replacement_result.state.value if replacement_result else None,
            )
            self.assertEqual(2, sum(replacement_calls.values()))
            self.assertEqual(2, len(ProjectStore(project).load().payload["events"]))

    def test_incomplete_batch_continues_with_deterministic_chunks(self) -> None:
        with TemporaryDirectory() as temporary:
            project, store, coordinator, spec, calls = _fixture(temporary)
            requests = _configure_note_continuation(coordinator, calls)
            coordinator.enqueue(spec)

            finished = coordinator.run(spec.job_id)

            self.assertEqual("completed", finished.state.value if finished else None)
            self.assertEqual([None, "resume:note:1"], requests)
            self.assertEqual(2, calls[AnalysisStage.NOTE_TRANSCRIPTION])
            run_directory = coordinator._run_directory(spec.job_id)
            self.assertTrue((run_directory / "note_transcription.chunk-001.raw.json").exists())
            self.assertTrue((run_directory / "note_transcription.chunk-002.raw.json").exists())
            events = ProjectStore(project).load().payload["events"]
            self.assertEqual(3, len(events))
            token = coordinator._token(spec.job_id)
            self.assertEqual(
                {f"artifact:analysis-{token}-note_transcription-chunk-001",
                 f"artifact:analysis-{token}-note_transcription-chunk-002"},
                {event["body"]["raw_artifact_id"] for event in events
                 if event["generator_id"] == "generator:resumable-note_transcription"},
            )

    def test_restart_replays_incomplete_chunk_without_rerunning_it(self) -> None:
        with TemporaryDirectory() as temporary:
            project, store, coordinator, spec, calls = _fixture(temporary)
            _configure_note_continuation(coordinator, calls)
            coordinator.enqueue(spec)
            checkpoint = store.checkpoint

            def crash_after_first_raw(*args: object, **kwargs: object) -> object:
                if kwargs["continuation_token"] is not None:
                    raise SystemExit("simulated crash after incomplete raw persistence")
                return checkpoint(*args, **kwargs)

            store.checkpoint = crash_after_first_raw  # type: ignore[method-assign]
            with self.assertRaises(SystemExit):
                coordinator.run(spec.job_id)
            store.checkpoint = checkpoint  # type: ignore[method-assign]
            self.assertEqual(1, calls[AnalysisStage.NOTE_TRANSCRIPTION])
            store.recover_stale_leases(now=time.time() + 31)

            _, _, resumed, _, resumed_calls = _fixture(temporary, project=project, store=store)
            resumed_requests = _configure_note_continuation(resumed, resumed_calls)
            finished = resumed.run(spec.job_id)

            self.assertEqual("completed", finished.state.value if finished else None)
            self.assertEqual(["resume:note:1"], resumed_requests)
            self.assertEqual(1, resumed_calls[AnalysisStage.NOTE_TRANSCRIPTION])

    def test_restart_after_committed_incomplete_checkpoint_does_not_duplicate(self) -> None:
        with TemporaryDirectory() as temporary:
            project, store, coordinator, spec, calls = _fixture(temporary)
            _configure_note_continuation(coordinator, calls)
            execute = coordinator._steps[0].adapter.execute

            def crash_before_continuation(
                request: object,
                *,
                cancellation_requested: object = None,
            ) -> LocalAnalysisCLIExecution:
                if getattr(request, "continuation_token") is not None:
                    raise SystemExit("simulated crash after committed checkpoint")
                return execute(  # type: ignore[call-arg]
                    request,
                    cancellation_requested=cancellation_requested,
                )

            coordinator._steps[0].adapter.execute = (  # type: ignore[method-assign]
                crash_before_continuation
            )
            coordinator.enqueue(spec)
            with self.assertRaises(SystemExit):
                coordinator.run(spec.job_id)
            checkpoint = store.get(spec.job_id)
            self.assertEqual(
                "resume:note:1",
                checkpoint.continuation_token if checkpoint else None,
            )
            store.recover_stale_leases(now=time.time() + 31)

            _, _, resumed, _, resumed_calls = _fixture(
                temporary,
                project=project,
                store=store,
            )
            requests = _configure_note_continuation(resumed, resumed_calls)
            finished = resumed.run(spec.job_id)

            self.assertEqual("completed", finished.state.value if finished else None)
            self.assertEqual(["resume:note:1"], requests)
            self.assertEqual(3, len(ProjectStore(project).load().payload["events"]))

    def test_restart_advances_raw_ahead_of_incomplete_checkpoint(self) -> None:
        with TemporaryDirectory() as temporary:
            project, store, coordinator, spec, calls = _fixture(temporary)
            _configure_note_continuation(coordinator, calls)
            coordinator.enqueue(spec)
            checkpoint = store.checkpoint

            def crash_before_final_checkpoint(
                *args: object,
                **kwargs: object,
            ) -> object:
                if (
                    kwargs["stage"] is AnalysisStage.NOTE_TRANSCRIPTION
                    and kwargs["continuation_token"] is None
                ):
                    raise SystemExit("simulated crash after final raw response")
                return checkpoint(*args, **kwargs)

            store.checkpoint = crash_before_final_checkpoint  # type: ignore[method-assign]
            with self.assertRaises(SystemExit):
                coordinator.run(spec.job_id)
            store.checkpoint = checkpoint  # type: ignore[method-assign]
            store.recover_stale_leases(now=time.time() + 31)

            _, _, resumed, _, resumed_calls = _fixture(
                temporary,
                project=project,
                store=store,
            )
            requests = _configure_note_continuation(resumed, resumed_calls)
            finished = resumed.run(spec.job_id)

            self.assertEqual("completed", finished.state.value if finished else None)
            self.assertEqual([], requests)
            self.assertEqual(0, resumed_calls[AnalysisStage.NOTE_TRANSCRIPTION])

    def test_non_publishable_terminal_state_fails_without_graph_claims(self) -> None:
        with TemporaryDirectory() as temporary:
            project, _, coordinator, spec, _ = _fixture(temporary)
            adapter = coordinator._steps[0].adapter
            failed = AnalysisBatch(
                AnalysisResult(
                    AnalysisStage.NOTE_TRANSCRIPTION,
                    AnalysisState.FAILED,
                    (),
                    ("fixture failure",),
                ),
                (),
            )
            raw = b'{"state":"failed"}'
            adapter.execute = (  # type: ignore[method-assign]
                lambda request, **kwargs: LocalAnalysisCLIExecution(
                    failed,
                    SHA,
                    raw,
                    hashlib.sha256(raw).hexdigest(),
                    1,
                    True,
                )
            )
            coordinator.enqueue(spec)

            finished = coordinator.run(spec.job_id)

            self.assertEqual("failed", finished.state.value if finished else None)
            self.assertTrue(
                coordinator._raw_path(
                    spec.job_id,
                    AnalysisStage.NOTE_TRANSCRIPTION,
                ).exists()
            )
            self.assertEqual([], ProjectStore(project).load().payload["events"])

    def test_execution_failure_retains_raw_output_outside_replay_chunks(self) -> None:
        with TemporaryDirectory() as temporary:
            project, _, coordinator, spec, _ = _fixture(temporary)
            raw = b'{"adapter":"failed before normalization"}'

            def fail(
                request: object,
                *,
                cancellation_requested: object = None,
            ) -> LocalAnalysisCLIExecution:
                raise AnalysisCLIExecutionError(
                    "fixture failure",
                    request_sha256=SHA,
                    raw_output=raw,
                )

            coordinator._steps[0].adapter.execute = fail  # type: ignore[method-assign]
            coordinator.enqueue(spec)

            finished = coordinator.run(spec.job_id)

            self.assertEqual("failed", finished.state.value if finished else None)
            failure_path = (
                coordinator._run_directory(spec.job_id)
                / "note_transcription.failure-001.raw.json"
            )
            self.assertEqual(raw, failure_path.read_bytes())
            self.assertEqual([], ProjectStore(project).load().payload["events"])

    def test_duplicate_hypothesis_ids_across_chunks_fail_before_publication(self) -> None:
        with TemporaryDirectory() as temporary:
            project, _, coordinator, spec, calls = _fixture(temporary)
            _configure_note_continuation(coordinator, calls, duplicate_id=True)
            coordinator.enqueue(spec)

            finished = coordinator.run(spec.job_id)

            self.assertEqual("failed", finished.state.value if finished else None)
            self.assertEqual([], ProjectStore(project).load().payload["events"])

    def test_changed_executable_never_runs_or_publishes_raw_output(self) -> None:
        with TemporaryDirectory() as temporary:
            project, _, coordinator, spec, calls = _fixture(temporary)
            coordinator.enqueue(spec)
            executable = coordinator._steps[0].adapter.tool.executable
            executable.write_text("#!/bin/sh\nexit 9\n", encoding="utf-8")
            executable.chmod(0o700)

            finished = coordinator.run(spec.job_id)

            self.assertEqual("failed", finished.state.value if finished else None)
            self.assertEqual(
                0,
                sum(calls.values()),
            )
            run_directory = coordinator._run_directory(spec.job_id)
            self.assertEqual([], list(run_directory.glob("*.raw.json")))
            self.assertEqual([], ProjectStore(project).load().payload["events"])

    def test_executable_mutated_during_stage_never_publishes_raw_output(self) -> None:
        with TemporaryDirectory() as temporary:
            project, _, coordinator, spec, calls = _fixture(temporary)
            adapter = coordinator._steps[0].adapter
            execute = adapter.execute

            def mutate_after_execute(
                request: object,
                *,
                cancellation_requested: object = None,
            ) -> LocalAnalysisCLIExecution:
                result = execute(  # type: ignore[call-arg]
                    request,
                    cancellation_requested=cancellation_requested,
                )
                adapter.tool.executable.write_text(
                    "#!/bin/sh\nexit 10\n",
                    encoding="utf-8",
                )
                adapter.tool.executable.chmod(0o700)
                return result

            adapter.execute = mutate_after_execute  # type: ignore[method-assign]
            coordinator.enqueue(spec)

            finished = coordinator.run(spec.job_id)

            self.assertEqual("failed", finished.state.value if finished else None)
            self.assertEqual(1, calls[AnalysisStage.NOTE_TRANSCRIPTION])
            run_directory = coordinator._run_directory(spec.job_id)
            self.assertEqual([], list(run_directory.glob("*.raw.json")))
            self.assertEqual([], ProjectStore(project).load().payload["events"])


def _fixture(
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
        source = parent / "lesson.wav"
        source.write_bytes(b"resumable media")
        source.chmod(0o600)
        imported = ingest_media(project, source, create_restricted_rights=True)
    else:
        imported = type(
            "Imported",
            (),
            {"source_id": _source_id(project), "relative_path": _source_path(project)},
        )
    model = parent / "model.bin"
    if not model.exists():
        model.write_bytes(b"resumable model")
        model.chmod(0o600)
    source_id = str(getattr(imported, "source_id"))
    media_path = project / str(getattr(imported, "relative_path"))
    settings = LocalAnalysisCLISettings(
        working_directory=project,
        media=_source(source_id, media_path),
        model=_source("model:resumable", model),
        model_license="LicenseRef-test-model",
        adapter_license="MIT-test-adapter",
    )
    tool = parent / "tool"
    if not tool.exists():
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool.chmod(0o700)
    calls = {AnalysisStage.NOTE_TRANSCRIPTION: 0, AnalysisStage.INSTRUMENT_DETECTION: 0}
    steps = tuple(_step(stage, settings, tool, calls, crash_second) for stage in calls)
    model_sha = settings.model.sha256
    coordinator = ResumableAnalysisCoordinator(
        store or SQLiteJobStore(parent / "jobs.sqlite"),
        project,
        steps,
        owner_id="worker:a",
        lease_seconds=lease_seconds,
        adapter_fingerprint_sha256="b" * 64,
        runtime_fingerprint_sha256="c" * 64,
        settings_fingerprint_sha256="d" * 64,
        model_sha256=model_sha,
    )
    span = MediaSpan(source_id, "audio", 0, 1_000)
    spec = AnalysisJobSpec(
        "job:resumable", source_id, settings.media.sha256, tuple(calls), (span,), "b" * 64,
        "c" * 64, "d" * 64,
    )
    return project, coordinator._store, coordinator, spec, calls


def _step(
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
    batch = _batch(stage, settings.media.source_id)
    raw = stage.value.encode("utf-8")

    def execute(
        request: object,
        *,
        cancellation_requested: object = None,
    ) -> LocalAnalysisCLIExecution:
        calls[stage] += 1
        if crash_second and stage is AnalysisStage.INSTRUMENT_DETECTION:
            raise SystemExit("simulated process crash")
        return LocalAnalysisCLIExecution(batch, SHA, raw, hashlib.sha256(raw).hexdigest(), 1, True)

    adapter.execute = execute  # type: ignore[method-assign]
    adapter.replay = lambda request, output: batch  # type: ignore[method-assign]
    return ResumableAnalysisStep(adapter, {})


def _batch(stage: AnalysisStage, source_id: str) -> AnalysisBatch:
    span = MediaSpan(source_id, "audio", 0, 1_000)
    if stage is AnalysisStage.NOTE_TRANSCRIPTION:
        hypothesis = NoteHypothesis("note:resumable", span, AnalysisState.READY, 69, 440, 0.9,
                                    "generator:resumable-note_transcription")
    else:
        hypothesis = InstrumentHypothesis("instrument:resumable", span, AnalysisState.READY,
                                          "piano", None, 0.8,
                                          "generator:resumable-instrument_detection")
    result = AnalysisResult(stage, AnalysisState.READY, (hypothesis.hypothesis_id,), ())
    return AnalysisBatch(result, (hypothesis,))


def _configure_note_continuation(
    coordinator: ResumableAnalysisCoordinator,
    calls: dict[AnalysisStage, int],
    *,
    duplicate_id: bool = False,
) -> list[str | None]:
    """Configure two adapter responses whose raw payloads remain replayable."""
    adapter = coordinator._steps[0].adapter
    source_id = adapter.settings.media.source_id
    first_base = _batch(AnalysisStage.NOTE_TRANSCRIPTION, source_id)
    first = AnalysisBatch(
        AnalysisResult(AnalysisStage.NOTE_TRANSCRIPTION, AnalysisState.INCOMPLETE,
                       first_base.result.hypothesis_ids, (), "resume:note:1"),
        first_base.hypotheses,
    )
    second = _batch(AnalysisStage.NOTE_TRANSCRIPTION, source_id)
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

    def execute(
        request: object,
        *,
        cancellation_requested: object = None,
    ) -> LocalAnalysisCLIExecution:
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


def _source(identifier: str, path: Path) -> LocalAnalysisSource:
    raw = path.read_bytes()
    return LocalAnalysisSource(identifier, path, hashlib.sha256(raw).hexdigest(), len(raw))


def _source_id(project: Path) -> str:
    return str(ProjectStore(project).load().payload["sources"][0]["id"])


def _source_path(project: Path) -> str:
    return str(ProjectStore(project).load().payload["sources"][0]["uri"])
