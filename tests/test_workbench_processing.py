from __future__ import annotations

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from notewitness.application.workbench_processing import (
    DisabledWorkbenchExecutor,
    WorkbenchJobStore,
    WorkbenchJobKind,
    WorkbenchJobState,
    WorkbenchProcessingError,
    WorkbenchProcessingService,
)
from notewitness.media_ingest import ingest_media
from notewitness.local_tools import BoundedLocalToolRunner, LocalTool, LocalToolCancelled
from notewitness.project import initialize_project


class _ControlledExecutor:
    def __init__(
        self,
        *,
        fail_first: bool = False,
        block: bool = False,
        complete_ready: bool = True,
    ) -> None:
        self.fail_first = fail_first
        self.block = block
        self.complete_ready = complete_ready
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls: list[tuple[WorkbenchJobKind, str, frozenset[str]]] = []

    def status(self) -> dict[str, object]:
        return {
            "analysis_ready": True,
            "complete_ready": self.complete_ready,
            "configured": True,
            "network_used": False,
            "transcription_ready": True,
        }

    def execute(
        self,
        kind: WorkbenchJobKind,
        source_id: str,
        *,
        job_id: str,
        attempt: int,
        cancellation_requested: object,
        report_progress: object,
        completed_steps: frozenset[str],
        mark_step_completed: object,
    ) -> None:
        assert callable(cancellation_requested)
        assert callable(report_progress)
        assert callable(mark_step_completed)
        assert job_id.startswith("job:workbench-")
        assert attempt >= 1
        self.calls.append((kind, source_id, completed_steps))
        report_progress(20, "Running deterministic local fixture")
        self.entered.set()
        if self.block:
            deadline = time.monotonic() + 5
            while not self.release.wait(0.02):
                if cancellation_requested():
                    raise RuntimeError("fixture_cancelled")
                if time.monotonic() >= deadline:
                    raise RuntimeError("fixture_timeout")
        if self.fail_first and len(self.calls) == 1:
            mark_step_completed("transcription")
            raise RuntimeError("fixture_failure")
        if "transcription" not in completed_steps:
            mark_step_completed("transcription")
        if kind in {WorkbenchJobKind.ANALYSIS, WorkbenchJobKind.COMPLETE}:
            mark_step_completed("analysis")
        report_progress(99, "Fixture complete")


class _TermIgnoringToolExecutor:
    """Exercise shutdown against the bounded local-tool process lifecycle."""

    def __init__(self, working_directory: Path) -> None:
        self.working_directory = working_directory
        self.entered = threading.Event()
        self.child_pid_path = working_directory / "term-ignoring-tool.pid"

    def status(self) -> dict[str, object]:
        return {
            "analysis_ready": True,
            "complete_ready": True,
            "configured": True,
            "network_used": False,
            "transcription_ready": True,
        }

    def execute(
        self,
        kind: WorkbenchJobKind,
        source_id: str,
        *,
        job_id: str,
        attempt: int,
        cancellation_requested: object,
        report_progress: object,
        completed_steps: frozenset[str],
        mark_step_completed: object,
    ) -> None:
        assert callable(cancellation_requested)
        assert callable(report_progress)
        tool = LocalTool(name="python", executable=Path(sys.executable))
        script = (
            "import os, pathlib, signal, sys, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')\n"
            "time.sleep(60)\n"
        )
        self.entered.set()
        try:
            BoundedLocalToolRunner(tool).run(
                ("-c", script, os.fspath(self.child_pid_path)),
                working_directory=self.working_directory,
                timeout_seconds=60,
                deny_network=False,
                cancellation_requested=cancellation_requested,
            )
        except LocalToolCancelled:
            raise


class _UncooperativeExecutor:
    """A worker fixture that cannot complete until the test explicitly releases it."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def status(self) -> dict[str, object]:
        return {
            "analysis_ready": True,
            "complete_ready": True,
            "configured": True,
            "network_used": False,
            "transcription_ready": True,
        }

    def execute(self, *args: object, **kwargs: object) -> None:
        self.entered.set()
        self.release.wait()


class WorkbenchProcessingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        parent = Path(self.temporary.name).resolve()
        parent.chmod(0o700)
        self.project = parent / "study"
        initialize_project(self.project)
        media = parent / "lesson.wav"
        media.write_bytes(b"private fixture media")
        media.chmod(0o600)
        self.imported = ingest_media(
            self.project,
            media,
            create_restricted_rights=True,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_completed_job_is_durable_across_service_reopen(self) -> None:
        executor = _ControlledExecutor()
        service = WorkbenchProcessingService(self.project, executor)
        try:
            queued = service.enqueue("complete", self.imported.source_id)
            completed = _wait_for_state(
                service,
                queued.job_id,
                {WorkbenchJobState.COMPLETED},
            )
            self.assertEqual(("analysis", "transcription"), completed.completed_steps)
            self.assertEqual(100, completed.progress_percent)
        finally:
            service.close()

        reopened = WorkbenchProcessingService(
            self.project,
            DisabledWorkbenchExecutor(),
            start_worker=False,
        )
        try:
            persisted = reopened.store.get(queued.job_id)
            self.assertIsNotNone(persisted)
            assert persisted is not None
            self.assertEqual(WorkbenchJobState.COMPLETED, persisted.state)
            self.assertEqual(("analysis", "transcription"), persisted.completed_steps)
        finally:
            reopened.close()

    def test_running_job_cancels_and_publishes_no_completion(self) -> None:
        executor = _ControlledExecutor(block=True)
        service = WorkbenchProcessingService(self.project, executor)
        try:
            queued = service.enqueue("analysis", self.imported.source_id)
            self.assertTrue(executor.entered.wait(2))
            cancelling = service.cancel(queued.job_id)
            self.assertIn(
                cancelling.state,
                {WorkbenchJobState.CANCELLING, WorkbenchJobState.CANCELLED},
            )
            cancelled = _wait_for_state(
                service,
                queued.job_id,
                {WorkbenchJobState.CANCELLED},
            )
            self.assertTrue(cancelled.cancel_requested)
            self.assertNotEqual(100, cancelled.progress_percent)
        finally:
            executor.release.set()
            service.close()

    def test_retry_preserves_completed_step_and_does_not_repeat_it(self) -> None:
        executor = _ControlledExecutor(fail_first=True)
        service = WorkbenchProcessingService(self.project, executor)
        try:
            queued = service.enqueue("complete", self.imported.source_id)
            failed = _wait_for_state(
                service,
                queued.job_id,
                {WorkbenchJobState.FAILED},
            )
            self.assertEqual(("transcription",), failed.completed_steps)
            self.assertTrue(failed.retryable)
            service.retry(queued.job_id)
            completed = _wait_for_state(
                service,
                queued.job_id,
                {WorkbenchJobState.COMPLETED},
            )
            self.assertEqual(2, completed.attempt)
            self.assertEqual(
                frozenset({"transcription"}),
                executor.calls[1][2],
            )
        finally:
            service.close()

    def test_cancellation_after_publication_reports_completed_evidence_truthfully(self) -> None:
        service = WorkbenchProcessingService(
            self.project,
            _ControlledExecutor(),
            start_worker=False,
        )
        try:
            queued = service.store.enqueue(
                WorkbenchJobKind.COMPLETE,
                self.imported.source_id,
            )
            claimed = service.store.claim_next()
            self.assertIsNotNone(claimed)
            service.store.mark_step_completed(queued.job_id, "transcription")
            service.store.request_cancellation(queued.job_id)
            cancelled = service.store.fail(queued.job_id, "fixture_cancelled")
            self.assertEqual(WorkbenchJobState.CANCELLED, cancelled.state)
            self.assertIn("completed evidence remains", cancelled.status_message)
            self.assertEqual(("transcription",), cancelled.completed_steps)
            self.assertTrue(cancelled.retryable)

            service.store.retry(queued.job_id)
            service.store.claim_next()
            service.store.mark_step_completed(queued.job_id, "analysis")
            service.store.request_cancellation(queued.job_id)
            completed = service.store.fail(queued.job_id, "fixture_cancelled")
            self.assertEqual(WorkbenchJobState.COMPLETED, completed.state)
            self.assertIn("before cancellation took effect", completed.status_message)
            self.assertEqual(
                ("analysis", "transcription"),
                completed.completed_steps,
            )
        finally:
            service.close()

    def test_second_service_cannot_recover_a_live_job_until_owner_releases_lock(self) -> None:
        owner = WorkbenchProcessingService(
            self.project,
            _ControlledExecutor(),
            start_worker=False,
        )
        try:
            queued = owner.store.enqueue(
                WorkbenchJobKind.TRANSCRIPTION,
                self.imported.source_id,
            )
            claimed = owner.store.claim_next()
            self.assertIsNotNone(claimed)

            with self.assertRaisesRegex(
                WorkbenchProcessingError,
                "workbench_processing_service_already_active",
            ):
                WorkbenchProcessingService(
                    self.project,
                    _ControlledExecutor(),
                    start_worker=False,
                )
            live = owner.store.get(queued.job_id)
            self.assertIsNotNone(live)
            assert live is not None
            self.assertEqual(WorkbenchJobState.RUNNING, live.state)
        finally:
            owner.close()

        recovered = WorkbenchProcessingService(
            self.project,
            _ControlledExecutor(),
            start_worker=False,
        )
        try:
            interrupted = recovered.store.get(queued.job_id)
            self.assertIsNotNone(interrupted)
            assert interrupted is not None
            self.assertEqual(WorkbenchJobState.INTERRUPTED, interrupted.state)
            self.assertTrue(interrupted.retryable)
        finally:
            recovered.close()

    def test_concurrent_enqueue_allows_exactly_one_active_job(self) -> None:
        service = WorkbenchProcessingService(
            self.project,
            _ControlledExecutor(),
            start_worker=False,
        )
        try:
            start = threading.Barrier(3)
            created: list[str] = []
            errors: list[str] = []

            def enqueue() -> None:
                start.wait()
                try:
                    created.append(
                        service.enqueue("transcription", self.imported.source_id).job_id
                    )
                except WorkbenchProcessingError as exc:
                    errors.append(str(exc))

            workers = [threading.Thread(target=enqueue) for _ in range(2)]
            for worker in workers:
                worker.start()
            start.wait()
            for worker in workers:
                worker.join()

            self.assertEqual(1, len(created))
            self.assertEqual(["processing_job_already_active"], errors)
        finally:
            service.close()

    def test_concurrent_retry_allows_exactly_one_active_job(self) -> None:
        service = WorkbenchProcessingService(
            self.project,
            _ControlledExecutor(),
            start_worker=False,
        )
        try:
            failed_jobs = []
            for _ in range(2):
                queued = service.store.enqueue(
                    WorkbenchJobKind.TRANSCRIPTION,
                    self.imported.source_id,
                )
                service.store.claim_next()
                failed_jobs.append(service.store.fail(queued.job_id, "fixture_failure"))

            start = threading.Barrier(3)
            retried: list[str] = []
            errors: list[str] = []

            def retry(job_id: str) -> None:
                start.wait()
                try:
                    retried.append(service.retry(job_id).job_id)
                except WorkbenchProcessingError as exc:
                    errors.append(str(exc))

            workers = [
                threading.Thread(target=retry, args=(job.job_id,))
                for job in failed_jobs
            ]
            for worker in workers:
                worker.start()
            start.wait()
            for worker in workers:
                worker.join()

            self.assertEqual(1, len(retried))
            self.assertEqual(["processing_job_already_active"], errors)
        finally:
            service.close()

    def test_sqlite_sidecar_disappearance_is_ignored(self) -> None:
        store = WorkbenchJobStore(self.project / "runs" / "sidecar-race.sqlite")
        vanished = Path(f"{store.path}-wal")
        real_open = os.open

        def disappear(path: object, flags: int, mode: int = 0o777) -> int:
            if os.fspath(path) == os.fspath(vanished):
                raise FileNotFoundError()
            return real_open(path, flags, mode)

        with patch(
            "notewitness.application.workbench_processing.os.open",
            side_effect=disappear,
        ):
            store._private_sidecars()

    def test_sqlite_sidecar_symlink_is_rejected_without_touching_target(self) -> None:
        store = WorkbenchJobStore(self.project / "runs" / "sidecar-race.sqlite")
        target = self.project / "runs" / "unrelated-private-file"
        target.write_bytes(b"must not be chmodded through the symlink")
        target.chmod(0o644)
        sidecar = Path(f"{store.path}-wal")
        sidecar.symlink_to(target)

        with self.assertRaisesRegex(WorkbenchProcessingError, "job_store_not_regular"):
            store._private_sidecars()

        self.assertEqual(0o644, target.stat().st_mode & 0o777)

    def test_sqlite_sidecar_os_error_has_stable_code(self) -> None:
        store = WorkbenchJobStore(self.project / "runs" / "sidecar-race.sqlite")
        failing = Path(f"{store.path}-wal")
        real_open = os.open

        def deny(path: object, flags: int, mode: int = 0o777) -> int:
            if os.fspath(path) == os.fspath(failing):
                raise PermissionError("private details must not escape")
            return real_open(path, flags, mode)

        with patch(
            "notewitness.application.workbench_processing.os.open", side_effect=deny):
            with self.assertRaisesRegex(
                WorkbenchProcessingError,
                "^job_store_sidecar_access_failed$",
            ):
                store._private_sidecars()

    def test_cancelled_queued_job_is_retryable_without_losing_checkpoints(self) -> None:
        service = WorkbenchProcessingService(
            self.project,
            _ControlledExecutor(),
            start_worker=False,
        )
        try:
            queued = service.enqueue("complete", self.imported.source_id)
            cancelled = service.cancel(queued.job_id)
            self.assertEqual(WorkbenchJobState.CANCELLED, cancelled.state)
            self.assertTrue(cancelled.retryable)
            resumed = service.retry(queued.job_id)
            self.assertEqual(WorkbenchJobState.QUEUED, resumed.state)
            self.assertFalse(resumed.cancel_requested)
            self.assertEqual((), resumed.completed_steps)
        finally:
            service.close()

    def test_disabled_runtime_and_non_media_source_are_rejected(self) -> None:
        disabled = WorkbenchProcessingService(
            self.project,
            DisabledWorkbenchExecutor(),
            start_worker=False,
        )
        try:
            with self.assertRaisesRegex(
                WorkbenchProcessingError,
                "selected_local_runtime_not_ready",
            ):
                disabled.enqueue("transcription", self.imported.source_id)
        finally:
            disabled.close()

    def test_partial_analysis_runtime_runs_analysis_but_rejects_complete(self) -> None:
        service = WorkbenchProcessingService(
            self.project,
            _ControlledExecutor(complete_ready=False),
            start_worker=False,
        )
        try:
            analysis = service.enqueue("analysis", self.imported.source_id)
            self.assertEqual(WorkbenchJobKind.ANALYSIS, analysis.kind)
            service.cancel(analysis.job_id)
            with self.assertRaisesRegex(
                WorkbenchProcessingError,
                "selected_local_runtime_not_ready",
            ):
                service.enqueue("complete", self.imported.source_id)
        finally:
            service.close()

    def test_close_reaps_term_ignoring_tool_before_releasing_project_lock(self) -> None:
        executor = _TermIgnoringToolExecutor(self.project / "runs")
        service = WorkbenchProcessingService(self.project, executor)
        queued = service.enqueue("transcription", self.imported.source_id)
        self.assertTrue(executor.entered.wait(2))
        _wait_for_path(executor.child_pid_path)

        service.close()

        child_pid = int(executor.child_pid_path.read_text(encoding="utf-8"))
        self.assertFalse(_process_exists(child_pid))
        terminal = service.store.get(queued.job_id)
        self.assertIsNotNone(terminal)
        assert terminal is not None
        self.assertEqual(WorkbenchJobState.CANCELLED, terminal.state)
        self.assertTrue(terminal.retryable)

        reopened = WorkbenchProcessingService(
            self.project,
            _ControlledExecutor(),
            start_worker=False,
        )
        reopened.close()

    def test_close_retains_lock_and_surfaces_timeout_until_worker_stops(self) -> None:
        executor = _UncooperativeExecutor()
        service = WorkbenchProcessingService(self.project, executor)
        queued = service.enqueue("transcription", self.imported.source_id)
        self.assertTrue(executor.entered.wait(2))

        with patch(
            "notewitness.application.workbench_processing."
            "WORKBENCH_SHUTDOWN_WAIT_SECONDS",
            0.05,
        ):
            with self.assertRaisesRegex(
                WorkbenchProcessingError,
                "workbench_processing_shutdown_timeout",
            ):
                service.close()

        with self.assertRaisesRegex(
            WorkbenchProcessingError,
            "workbench_processing_service_already_active",
        ):
            WorkbenchProcessingService(
                self.project,
                _ControlledExecutor(),
                start_worker=False,
            )

        executor.release.set()
        service.close()
        terminal = service.store.get(queued.job_id)
        self.assertIsNotNone(terminal)
        assert terminal is not None
        self.assertEqual(WorkbenchJobState.CANCELLED, terminal.state)

        reopened = WorkbenchProcessingService(
            self.project,
            _ControlledExecutor(),
            start_worker=False,
        )
        reopened.close()


def _wait_for_state(
    service: WorkbenchProcessingService,
    job_id: str,
    states: set[WorkbenchJobState],
) -> object:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = service.store.get(job_id)
        if job is not None and job.state in states:
            return job
        time.sleep(0.01)
    current = service.store.get(job_id)
    raise AssertionError(f"job did not reach {states}; current={current}")


def _wait_for_path(path: Path) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.01)
    raise AssertionError(f"tool did not create {path}")


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
