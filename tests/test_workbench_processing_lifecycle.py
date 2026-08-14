from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from notewitness.application.workbench_processing import (
    DisabledWorkbenchExecutor,
    WorkbenchJobState,
    WorkbenchProcessingError,
    WorkbenchProcessingService,
    WorkbenchJobKind,
    WorkbenchJobStore,
)

from tests.support.workbench_processing import (
    ControlledExecutor,
    TermIgnoringToolExecutor,
    UncooperativeExecutor,
    WorkbenchProcessingTestCase,
    process_exists,
    wait_for_path,
)


class WorkbenchProcessingLifecycleSidecarShutdownTests(
    WorkbenchProcessingTestCase,
    unittest.TestCase,
):
    def test_sqlite_sidecar_disappearance_is_ignored(self) -> None:
        store = WorkbenchJobStore(self.project / "runs" / "sidecar-race.sqlite")
        vanished = Path(f"{store.path}-wal")
        real_open = os.open

        def disappear(path: object, flags: int, mode: int = 0o777) -> int:
            if os.fspath(path) == os.fspath(vanished):
                raise FileNotFoundError()
            return real_open(path, flags, mode)

        with patch("notewitness.application._workbench_processing_store.os.open", side_effect=disappear):
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

        with patch("notewitness.application._workbench_processing_store.os.open", side_effect=deny):
            with self.assertRaisesRegex(WorkbenchProcessingError, "^job_store_sidecar_access_failed$"):
                store._private_sidecars()

    def test_cancelled_queued_job_is_retryable_without_losing_checkpoints(self) -> None:
        service = WorkbenchProcessingService(self.project, ControlledExecutor(), start_worker=False)
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
            self.project, DisabledWorkbenchExecutor(), start_worker=False,
        )
        try:
            with self.assertRaisesRegex(WorkbenchProcessingError, "selected_local_runtime_not_ready"):
                disabled.enqueue("transcription", self.imported.source_id)
        finally:
            disabled.close()

    def test_partial_analysis_runtime_runs_analysis_but_rejects_complete(self) -> None:
        service = WorkbenchProcessingService(
            self.project, ControlledExecutor(complete_ready=False), start_worker=False,
        )
        try:
            analysis = service.enqueue("analysis", self.imported.source_id)
            self.assertEqual(WorkbenchJobKind.ANALYSIS, analysis.kind)
            service.cancel(analysis.job_id)
            with self.assertRaisesRegex(WorkbenchProcessingError, "selected_local_runtime_not_ready"):
                service.enqueue("complete", self.imported.source_id)
        finally:
            service.close()

    def test_close_reaps_term_ignoring_tool_before_releasing_project_lock(self) -> None:
        executor = TermIgnoringToolExecutor(self.project / "runs")
        service = WorkbenchProcessingService(self.project, executor)
        queued = service.enqueue("transcription", self.imported.source_id)
        self.assertTrue(executor.entered.wait(2))
        wait_for_path(executor.child_pid_path)

        service.close()

        child_pid = int(executor.child_pid_path.read_text(encoding="utf-8"))
        self.assertFalse(process_exists(child_pid))
        terminal = service.store.get(queued.job_id)
        self.assertIsNotNone(terminal)
        assert terminal is not None
        self.assertEqual(WorkbenchJobState.CANCELLED, terminal.state)
        self.assertTrue(terminal.retryable)

        reopened = WorkbenchProcessingService(self.project, ControlledExecutor(), start_worker=False)
        reopened.close()

    def test_close_retains_lock_and_surfaces_timeout_until_worker_stops(self) -> None:
        executor = UncooperativeExecutor()
        service = WorkbenchProcessingService(self.project, executor)
        queued = service.enqueue("transcription", self.imported.source_id)
        self.assertTrue(executor.entered.wait(2))

        with patch(
            "notewitness.application.workbench_processing.WORKBENCH_SHUTDOWN_WAIT_SECONDS",
            0.05,
        ):
            with self.assertRaisesRegex(WorkbenchProcessingError, "workbench_processing_shutdown_timeout"):
                service.close()

        with self.assertRaisesRegex(WorkbenchProcessingError, "workbench_processing_service_already_active"):
            WorkbenchProcessingService(self.project, ControlledExecutor(), start_worker=False)

        executor.release.set()
        service.close()
        terminal = service.store.get(queued.job_id)
        self.assertIsNotNone(terminal)
        assert terminal is not None
        self.assertEqual(WorkbenchJobState.CANCELLED, terminal.state)

        reopened = WorkbenchProcessingService(self.project, ControlledExecutor(), start_worker=False)
        reopened.close()


if __name__ == "__main__":
    unittest.main()
