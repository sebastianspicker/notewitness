from __future__ import annotations

from dataclasses import replace
import hashlib
from tempfile import TemporaryDirectory
import time
import unittest

from notewitness.adapters.analysis_cli import (
    AnalysisCLICancelled,
    AnalysisCLIExecutionError,
    LocalAnalysisCLIExecution,
)
from notewitness.domain.analysis import (
    AnalysisBatch,
    AnalysisResult,
    AnalysisStage,
    AnalysisState,
)
from notewitness.project_store import ProjectStore

from tests.support.resumable_analysis import SHA, fixture, configure_note_continuation


class ResumableAnalysisContinuationPublicationTests(unittest.TestCase):
    def test_running_stage_cancellation_preserves_checkpoint_without_publication(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            project, store, coordinator, spec, calls = fixture(temporary)
            configure_note_continuation(coordinator, calls)
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

            _, _, replacement, replacement_spec, replacement_calls = fixture(
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
            project, store, coordinator, spec, calls = fixture(temporary)
            requests = configure_note_continuation(coordinator, calls)
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
            project, store, coordinator, spec, calls = fixture(temporary)
            configure_note_continuation(coordinator, calls)
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

            _, _, resumed, _, resumed_calls = fixture(temporary, project=project, store=store)
            resumed_requests = configure_note_continuation(resumed, resumed_calls)
            finished = resumed.run(spec.job_id)

            self.assertEqual("completed", finished.state.value if finished else None)
            self.assertEqual(["resume:note:1"], resumed_requests)
            self.assertEqual(1, resumed_calls[AnalysisStage.NOTE_TRANSCRIPTION])

    def test_restart_after_committed_incomplete_checkpoint_does_not_duplicate(self) -> None:
        with TemporaryDirectory() as temporary:
            project, store, coordinator, spec, calls = fixture(temporary)
            configure_note_continuation(coordinator, calls)
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

            _, _, resumed, _, resumed_calls = fixture(
                temporary,
                project=project,
                store=store,
            )
            requests = configure_note_continuation(resumed, resumed_calls)
            finished = resumed.run(spec.job_id)

            self.assertEqual("completed", finished.state.value if finished else None)
            self.assertEqual(["resume:note:1"], requests)
            self.assertEqual(3, len(ProjectStore(project).load().payload["events"]))

    def test_restart_advances_raw_ahead_of_incomplete_checkpoint(self) -> None:
        with TemporaryDirectory() as temporary:
            project, store, coordinator, spec, calls = fixture(temporary)
            configure_note_continuation(coordinator, calls)
            coordinator.enqueue(spec)
            checkpoint = store.checkpoint

            def crash_before_final_checkpoint(*args: object, **kwargs: object) -> object:
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

            _, _, resumed, _, resumed_calls = fixture(
                temporary,
                project=project,
                store=store,
            )
            requests = configure_note_continuation(resumed, resumed_calls)
            finished = resumed.run(spec.job_id)

            self.assertEqual("completed", finished.state.value if finished else None)
            self.assertEqual([], requests)
            self.assertEqual(0, resumed_calls[AnalysisStage.NOTE_TRANSCRIPTION])

    def test_non_publishable_terminal_state_fails_without_graph_claims(self) -> None:
        with TemporaryDirectory() as temporary:
            project, _, coordinator, spec, _ = fixture(temporary)
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
            project, _, coordinator, spec, _ = fixture(temporary)
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
            project, _, coordinator, spec, calls = fixture(temporary)
            configure_note_continuation(coordinator, calls, duplicate_id=True)
            coordinator.enqueue(spec)

            finished = coordinator.run(spec.job_id)

            self.assertEqual("failed", finished.state.value if finished else None)
            self.assertEqual([], ProjectStore(project).load().payload["events"])

    def test_changed_executable_never_runs_or_publishes_raw_output(self) -> None:
        with TemporaryDirectory() as temporary:
            project, _, coordinator, spec, calls = fixture(temporary)
            coordinator.enqueue(spec)
            executable = coordinator._steps[0].adapter.tool.executable
            executable.write_text("#!/bin/sh\nexit 9\n", encoding="utf-8")
            executable.chmod(0o700)

            finished = coordinator.run(spec.job_id)

            self.assertEqual("failed", finished.state.value if finished else None)
            self.assertEqual(0, sum(calls.values()))
            run_directory = coordinator._run_directory(spec.job_id)
            self.assertEqual([], list(run_directory.glob("*.raw.json")))
            self.assertEqual([], ProjectStore(project).load().payload["events"])

    def test_executable_mutated_during_stage_never_publishes_raw_output(self) -> None:
        with TemporaryDirectory() as temporary:
            project, _, coordinator, spec, calls = fixture(temporary)
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


if __name__ == "__main__":
    unittest.main()
