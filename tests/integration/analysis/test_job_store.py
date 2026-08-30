from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from notewitness.analysis.runs.sqlite_job_store import (
    JobConflictError,
    JobStoreError,
    SQLiteJobStore,
)
from notewitness.core.analysis.analysis import AnalysisStage, JobState
from notewitness.core.analysis.jobs import AnalysisJobSpec
from notewitness.core.time import MediaSpan


class AnalysisJobStoreTests(unittest.TestCase):
    def test_preserves_private_identity_and_lease_boundaries(self) -> None:
        digest = "a" * 64
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"
            root.mkdir(mode=0o700)
            database = root / "jobs.db"
            store = SQLiteJobStore(database)
            self.assertEqual(0o600, database.stat().st_mode & 0o777)
            for sidecar in (Path(f"{database}-wal"), Path(f"{database}-shm")):
                if sidecar.exists():
                    self.assertFalse(sidecar.is_symlink())
                    self.assertEqual(0o600, sidecar.stat().st_mode & 0o777)

            spec = AnalysisJobSpec(
                job_id="job-1",
                source_id="source-1",
                source_sha256=digest,
                stages=(AnalysisStage.MEDIA_PROBE,),
                spans=(MediaSpan("source-1", "audio", 0, 1),),
                adapter_fingerprint_sha256=digest,
                runtime_fingerprint_sha256=digest,
                settings_fingerprint_sha256=digest,
            )
            self.assertEqual(spec, store.enqueue(spec).spec)
            with self.assertRaises(JobConflictError):
                store.enqueue(
                    AnalysisJobSpec(
                        job_id="job-1",
                        source_id="source-1",
                        source_sha256="b" * 64,
                        stages=spec.stages,
                        spans=spec.spans,
                        adapter_fingerprint_sha256=digest,
                        runtime_fingerprint_sha256=digest,
                        settings_fingerprint_sha256=digest,
                    )
                )

            claimed = store.claim(
                "job-1",
                owner_id="owner-a",
                lease_seconds=30,
                source_sha256=digest,
                adapter_fingerprint_sha256=digest,
                runtime_fingerprint_sha256=digest,
                settings_fingerprint_sha256=digest,
                score_sha256=None,
            )
            self.assertIsNotNone(claimed)
            self.assertIsNone(
                store.claim(
                    "job-1",
                    owner_id="owner-b",
                    lease_seconds=30,
                    source_sha256=digest,
                    adapter_fingerprint_sha256=digest,
                    runtime_fingerprint_sha256=digest,
                    settings_fingerprint_sha256=digest,
                    score_sha256=None,
                )
            )
            with self.assertRaises(JobConflictError):
                store.checkpoint(
                    "job-1",
                    owner_id="owner-b",
                    stage=AnalysisStage.MEDIA_PROBE,
                    completed_span_count=1,
                    continuation_token="resume",
                    last_artifact_id=None,
                )
            store.checkpoint(
                "job-1",
                owner_id="owner-a",
                stage=AnalysisStage.MEDIA_PROBE,
                completed_span_count=1,
                continuation_token="resume",
                last_artifact_id=None,
            )
            with self.assertRaises(JobConflictError):
                store.claim(
                    "job-1",
                    owner_id="owner-b",
                    lease_seconds=30,
                    source_sha256=digest,
                    adapter_fingerprint_sha256=digest,
                    runtime_fingerprint_sha256="c" * 64,
                    settings_fingerprint_sha256=digest,
                    score_sha256=None,
                )
            self.assertIsNotNone(
                store.claim(
                    "job-1",
                    owner_id="owner-b",
                    lease_seconds=30,
                    source_sha256=digest,
                    adapter_fingerprint_sha256=digest,
                    runtime_fingerprint_sha256=digest,
                    settings_fingerprint_sha256=digest,
                    score_sha256=None,
                )
            )
            self.assertTrue(store.request_cancellation("job-1").cancel_requested)
            with self.assertRaises(JobConflictError):
                store.complete("job-1", owner_id="owner-a")
            self.assertIs(
                store.complete("job-1", owner_id="owner-b").state,
                JobState.CANCELLED,
            )

            sidecar = Path(f"{database}-wal")
            sidecar.symlink_to(database)
            with self.assertRaises(JobStoreError):
                store.get("job-1")
            linked = root / "linked.db"
            linked.symlink_to(database)
            with self.assertRaises(JobStoreError):
                SQLiteJobStore(linked)

    def test_rejects_parent_replacement(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary)
            analysis_parent = parent / "analysis-runs"
            analysis_parent.mkdir(mode=0o700)
            store = SQLiteJobStore(analysis_parent / "jobs.sqlite")

            analysis_parent.rename(parent / "analysis-runs-displaced")
            analysis_parent.mkdir(mode=0o700)

            with self.assertRaises(JobStoreError):
                store.list()


if __name__ == "__main__":
    unittest.main()
