from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from notewitness.application.job_service import DurableJobService, StageExecution
from notewitness.domain.analysis import AnalysisStage, JobState
from notewitness.domain.jobs import AnalysisJobSpec, DurableJob
from notewitness.domain.timeline import MediaSpan
from notewitness.infrastructure.sqlite_job_store import JobConflictError, SQLiteJobStore


SHA = "a" * 64


def spec() -> AnalysisJobSpec:
    return AnalysisJobSpec(
        job_id="job:test",
        source_id="source:test",
        source_sha256=SHA,
        stages=(AnalysisStage.MEDIA_PROBE, AnalysisStage.SPEECH_RECOGNITION),
        spans=(MediaSpan("source:test", "audio:0", 0, 10),),
        adapter_fingerprint_sha256="b" * 64,
        runtime_fingerprint_sha256="c" * 64,
        settings_fingerprint_sha256="d" * 64,
        score_sha256="e" * 64,
    )


class RecordingExecutor:
    def __init__(self, results: list[StageExecution]) -> None:
        self.results = results
        self.calls: list[tuple[AnalysisStage, int, str | None]] = []
        self.cancel_store: SQLiteJobStore | None = None

    def execute(
        self,
        job: DurableJob,
        stage: AnalysisStage,
        spans: tuple[MediaSpan, ...],
        completed_span_count: int,
        continuation_token: str | None,
    ) -> StageExecution:
        self.calls.append((stage, completed_span_count, continuation_token))
        if self.cancel_store is not None:
            self.cancel_store.request_cancellation(job.spec.job_id)
        return self.results.pop(0)


def service(
    store: SQLiteJobStore,
    executor: RecordingExecutor,
    owner: str = "worker:a",
) -> DurableJobService:
    return DurableJobService(
        store,
        executor,
        owner_id=owner,
        lease_seconds=30,
        source_sha256=SHA,
        adapter_fingerprint_sha256="b" * 64,
        runtime_fingerprint_sha256="c" * 64,
        settings_fingerprint_sha256="d" * 64,
        score_sha256="e" * 64,
    )


class DurableJobServiceTests(unittest.TestCase):
    def test_reopens_after_stale_lease_without_repeating_completed_stage(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "jobs.sqlite"
            first = SQLiteJobStore(path)
            first.enqueue(spec())
            first.claim(
                "job:test", owner_id="crashed", lease_seconds=0.01, source_sha256=SHA,
                adapter_fingerprint_sha256="b" * 64, runtime_fingerprint_sha256="c" * 64,
                settings_fingerprint_sha256="d" * 64, score_sha256="e" * 64,
            )
            first.checkpoint(
                "job:test", owner_id="crashed", stage=AnalysisStage.MEDIA_PROBE,
                completed_span_count=1, continuation_token=None, last_artifact_id="artifact:probe",
                pause=False,
            )
            time.sleep(0.02)
            reopened = SQLiteJobStore(path)
            self.assertEqual(1, reopened.recover_stale_leases())
            executor = RecordingExecutor(
                [StageExecution(1, True, last_artifact_id="artifact:asr")]
            )
            finished = service(reopened, executor, "worker:resume").run("job:test")
            self.assertEqual(JobState.COMPLETED, finished.state if finished else None)
            self.assertEqual([(AnalysisStage.SPEECH_RECOGNITION, 0, None)], executor.calls)

    def test_incomplete_stage_uses_continuation_before_later_stage(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SQLiteJobStore(Path(temporary) / "jobs.sqlite")
            store.enqueue(spec())
            executor = RecordingExecutor(
                [
                    StageExecution(1, False, "next"),
                    StageExecution(1, True, last_artifact_id="artifact:probe"),
                    StageExecution(1, True, last_artifact_id="artifact:asr"),
                ]
            )
            finished = service(store, executor).run("job:test")
            self.assertEqual(JobState.COMPLETED, finished.state if finished else None)
            self.assertEqual(
                [
                    (AnalysisStage.MEDIA_PROBE, 0, None),
                    (AnalysisStage.MEDIA_PROBE, 1, "next"),
                    (AnalysisStage.SPEECH_RECOGNITION, 0, None),
                ],
                executor.calls,
            )

    def test_cancellation_between_stages_stops_before_next_execution(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SQLiteJobStore(Path(temporary) / "jobs.sqlite")
            store.enqueue(spec())
            executor = RecordingExecutor([StageExecution(1, True)])
            executor.cancel_store = store
            finished = service(store, executor).run("job:test")
            self.assertEqual(JobState.CANCELLED, finished.state if finished else None)
            self.assertEqual([(AnalysisStage.MEDIA_PROBE, 0, None)], executor.calls)

    def test_executor_failure_is_sanitized(self) -> None:
        class FailingExecutor(RecordingExecutor):
            def execute(self, *args: object, **kwargs: object) -> StageExecution:
                raise RuntimeError("source path /private/media must not persist")

        with TemporaryDirectory() as temporary:
            store = SQLiteJobStore(Path(temporary) / "jobs.sqlite")
            store.enqueue(spec())
            finished = service(store, FailingExecutor([])).run("job:test")
            self.assertEqual(JobState.FAILED, finished.state if finished else None)
            self.assertEqual(
                "stage_execution_failed",
                finished.failure_reason if finished else None,
            )

    def test_immutable_identity_mismatch_does_not_execute(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SQLiteJobStore(Path(temporary) / "jobs.sqlite")
            store.enqueue(spec())
            for adapter, score in (("f" * 64, "e" * 64), ("b" * 64, "f" * 64)):
                with self.subTest(adapter=adapter, score=score):
                    executor = RecordingExecutor([])
                    worker = DurableJobService(
                        store, executor, owner_id="worker:a", lease_seconds=30,
                        source_sha256=SHA, adapter_fingerprint_sha256=adapter,
                        runtime_fingerprint_sha256="c" * 64,
                        settings_fingerprint_sha256="d" * 64, score_sha256=score,
                    )
                    with self.assertRaises(JobConflictError):
                        worker.run("job:test")
                    self.assertEqual([], executor.calls)

    def test_stage_execution_validates_continuation_contract(self) -> None:
        with self.assertRaises(ValueError):
            StageExecution(1, False)
        with self.assertRaises(ValueError):
            StageExecution(1, True, "not-allowed")
