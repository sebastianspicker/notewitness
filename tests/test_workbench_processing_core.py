from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from notewitness.application.workbench_processing import (
    DisabledWorkbenchExecutor,
    WorkbenchJobKind,
    WorkbenchJobState,
    WorkbenchProcessingError,
    WorkbenchProcessingService,
)

from tests.support.workbench_processing import (
    ControlledExecutor,
    WorkbenchProcessingTestCase,
    wait_for_state,
)


class WorkbenchProcessingCoreTests(WorkbenchProcessingTestCase, unittest.TestCase):
    def test_completed_job_is_durable_across_service_reopen(self) -> None:
        executor = ControlledExecutor()
        service = WorkbenchProcessingService(self.project, executor)
        try:
            queued = service.enqueue("complete", self.imported.source_id)
            completed = wait_for_state(service, queued.job_id, {WorkbenchJobState.COMPLETED})
            self.assertEqual(("analysis", "transcription"), completed.completed_steps)
            self.assertEqual(100, completed.progress_percent)
        finally:
            service.close()

        reopened = WorkbenchProcessingService(
            self.project, DisabledWorkbenchExecutor(), start_worker=False,
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
        executor = ControlledExecutor(block=True)
        service = WorkbenchProcessingService(self.project, executor)
        try:
            queued = service.enqueue("analysis", self.imported.source_id)
            self.assertTrue(executor.entered.wait(2))
            cancelling = service.cancel(queued.job_id)
            self.assertIn(cancelling.state, {WorkbenchJobState.CANCELLING, WorkbenchJobState.CANCELLED})
            cancelled = wait_for_state(service, queued.job_id, {WorkbenchJobState.CANCELLED})
            self.assertTrue(cancelled.cancel_requested)
            self.assertNotEqual(100, cancelled.progress_percent)
        finally:
            executor.release.set()
            service.close()

    def test_retry_preserves_completed_step_and_does_not_repeat_it(self) -> None:
        executor = ControlledExecutor(fail_first=True)
        service = WorkbenchProcessingService(self.project, executor)
        try:
            queued = service.enqueue("complete", self.imported.source_id)
            failed = wait_for_state(service, queued.job_id, {WorkbenchJobState.FAILED})
            self.assertEqual(("transcription",), failed.completed_steps)
            self.assertTrue(failed.retryable)
            service.retry(queued.job_id)
            completed = wait_for_state(service, queued.job_id, {WorkbenchJobState.COMPLETED})
            self.assertEqual(2, completed.attempt)
            self.assertEqual(frozenset({"transcription"}), executor.calls[1][2])
        finally:
            service.close()

    def test_cancellation_after_publication_reports_completed_evidence_truthfully(self) -> None:
        service = WorkbenchProcessingService(self.project, ControlledExecutor(), start_worker=False)
        try:
            queued = service.store.enqueue(WorkbenchJobKind.COMPLETE, self.imported.source_id)
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
            self.assertEqual(("analysis", "transcription"), completed.completed_steps)
        finally:
            service.close()

    def test_second_service_cannot_recover_a_live_job_until_owner_releases_lock(self) -> None:
        owner = WorkbenchProcessingService(self.project, ControlledExecutor(), start_worker=False)
        try:
            queued = owner.store.enqueue(WorkbenchJobKind.TRANSCRIPTION, self.imported.source_id)
            claimed = owner.store.claim_next()
            self.assertIsNotNone(claimed)

            with self.assertRaisesRegex(WorkbenchProcessingError, "workbench_processing_service_already_active"):
                WorkbenchProcessingService(self.project, ControlledExecutor(), start_worker=False)
            live = owner.store.get(queued.job_id)
            self.assertIsNotNone(live)
            assert live is not None
            self.assertEqual(WorkbenchJobState.RUNNING, live.state)
        finally:
            owner.close()

        recovered = WorkbenchProcessingService(self.project, ControlledExecutor(), start_worker=False)
        try:
            interrupted = recovered.store.get(queued.job_id)
            self.assertIsNotNone(interrupted)
            assert interrupted is not None
            self.assertEqual(WorkbenchJobState.INTERRUPTED, interrupted.state)
            self.assertTrue(interrupted.retryable)
        finally:
            recovered.close()

    def test_concurrent_enqueue_allows_exactly_one_active_job(self) -> None:
        service = WorkbenchProcessingService(self.project, ControlledExecutor(), start_worker=False)
        try:
            start = threading.Barrier(3)
            created: list[str] = []
            errors: list[str] = []

            def enqueue() -> None:
                start.wait()
                try:
                    created.append(service.enqueue("transcription", self.imported.source_id).job_id)
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
        service = WorkbenchProcessingService(self.project, ControlledExecutor(), start_worker=False)
        try:
            failed_jobs = []
            for _ in range(2):
                queued = service.store.enqueue(WorkbenchJobKind.TRANSCRIPTION, self.imported.source_id)
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

            workers = [threading.Thread(target=retry, args=(job.job_id,)) for job in failed_jobs]
            for worker in workers:
                worker.start()
            start.wait()
            for worker in workers:
                worker.join()

            self.assertEqual(1, len(retried))
            self.assertEqual(["processing_job_already_active"], errors)
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
