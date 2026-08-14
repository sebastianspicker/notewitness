from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest

from notewitness.adapters.analysis_cli import LocalAnalysisCLIExecution
from notewitness.application.resumable_analysis import ResumableAnalysisCoordinator
from notewitness.domain.analysis import AnalysisStage
from notewitness.infrastructure.sqlite_job_store import JobConflictError
from notewitness.project_store import ProjectStore

from tests.support.resumable_analysis import fixture, configure_note_continuation


class ResumableAnalysisRecoveryLeaseTests(unittest.TestCase):
    def test_crash_after_raw_write_replays_without_invoking_stage_again(self) -> None:
        with TemporaryDirectory() as temporary:
            project, store, coordinator, spec, calls = fixture(temporary)
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

            _, _, resumed, _, resumed_calls = fixture(
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
            project, store, coordinator, spec, calls = fixture(temporary, crash_second=True)
            coordinator.enqueue(spec)
            with self.assertRaises(SystemExit):
                coordinator.run(spec.job_id)
            self.assertEqual(1, calls[AnalysisStage.NOTE_TRANSCRIPTION])
            self.assertEqual(1, store.recover_stale_leases(now=time.time() + 31))

            _, _, resumed, _, resumed_calls = fixture(
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
            project, store, coordinator, spec, _ = fixture(temporary)
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
            project, store, coordinator, spec, calls = fixture(temporary)
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
            project, store, coordinator, spec, calls = fixture(
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


if __name__ == "__main__":
    unittest.main()
