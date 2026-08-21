from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from notewitness.project import initialize_project
from notewitness.project_store import (
    ProjectConflictError,
    ProjectStore,
    ProjectStoreError,
)
from notewitness.evidence import MAX_PROJECT_BYTES
from notewitness.domain.analysis import AnalysisStage, JobState
from notewitness.domain.jobs import AnalysisJobSpec
from notewitness.domain.timeline import MediaSpan
from notewitness.infrastructure.sqlite_job_store import JobConflictError, JobStoreError, SQLiteJobStore


class ProjectStoreTests(unittest.TestCase):
    def test_accepts_the_project_document_path_printed_by_init(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "study"
            document = initialize_project(root)

            store = ProjectStore(document)

            self.assertTrue(root.samefile(store.root))
            self.assertEqual("offline", store.load().payload["network"]["mode"])

    def test_mutation_persists_across_store_restart(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "study"
            initialize_project(root)
            store = ProjectStore(root)
            initial = store.load()

            written = store.mutate(
                lambda payload: payload["project"].update({"name": "Rehearsal"}),
                expected_sha256=initial.sha256,
            )

            restarted = ProjectStore(root).load()
            self.assertEqual("Rehearsal", restarted.payload["project"]["name"])
            self.assertEqual(written.sha256, restarted.sha256)

    def test_failed_mutation_does_not_publish_partial_payload(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "study"
            project_file = initialize_project(root)
            original = project_file.read_bytes()

            with self.assertRaises(ProjectStoreError):
                ProjectStore(root).mutate(
                    lambda payload: payload.update({"network": {"mode": "invalid"}})
                )

            self.assertEqual(original, project_file.read_bytes())

    def test_oversized_mutation_retains_the_reloadable_snapshot(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "study"
            project_file = initialize_project(root)
            original = project_file.read_bytes()

            with self.assertRaisesRegex(ProjectStoreError, "exceeds"):
                ProjectStore(root).mutate(
                    lambda payload: payload["project"].update(
                        {"name": "x" * MAX_PROJECT_BYTES}
                    )
                )

            self.assertEqual(original, project_file.read_bytes())
            self.assertEqual("study", ProjectStore(root).load().payload["project"]["name"])

    def test_compare_and_swap_rejects_stale_digest(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "study"
            initialize_project(root)
            store = ProjectStore(root)
            snapshot = store.load()
            store.mutate(lambda payload: payload["project"].update({"name": "new"}))

            with self.assertRaises(ProjectConflictError):
                store.mutate(lambda payload: None, expected_sha256=snapshot.sha256)

    def test_refuses_symlinked_root_or_document(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary)
            real = parent / "real"
            initialize_project(real)
            linked = parent / "linked"
            linked.symlink_to(real, target_is_directory=True)
            with self.assertRaises(ProjectStoreError):
                ProjectStore(linked).load()

            project_file = real / "project.json"
            backup = real / "backup.json"
            project_file.rename(backup)
            project_file.symlink_to(backup)
            with self.assertRaises(ProjectStoreError):
                ProjectStore(real).load()

    def test_refuses_group_readable_project_file(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "study"
            project_file = initialize_project(root)
            project_file.chmod(0o640)
            with self.assertRaisesRegex(ProjectStoreError, "deny group"):
                ProjectStore(root).load()

    def test_runtime_directories_are_allowlisted_private_and_non_symlinked(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "study"
            initialize_project(root)
            runs = root / "runs"
            runs.rmdir()

            created = ProjectStore(root).ensure_private_directory("runs")

            self.assertTrue(runs.samefile(created))
            self.assertEqual(0o700, runs.stat().st_mode & 0o777)
            with self.assertRaises(ProjectStoreError):
                ProjectStore(root).ensure_private_directory("models")

    def test_sqlite_job_store_preserves_private_identity_and_lease_boundaries(self) -> None:
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
                store.enqueue(AnalysisJobSpec(
                    job_id="job-1", source_id="source-1", source_sha256="b" * 64,
                    stages=spec.stages, spans=spec.spans, adapter_fingerprint_sha256=digest,
                    runtime_fingerprint_sha256=digest, settings_fingerprint_sha256=digest,
                ))

            claimed = store.claim("job-1", owner_id="owner-a", lease_seconds=30, source_sha256=digest,
                                  adapter_fingerprint_sha256=digest, runtime_fingerprint_sha256=digest,
                                  settings_fingerprint_sha256=digest, score_sha256=None)
            self.assertIsNotNone(claimed)
            self.assertIsNone(store.claim("job-1", owner_id="owner-b", lease_seconds=30, source_sha256=digest,
                                          adapter_fingerprint_sha256=digest, runtime_fingerprint_sha256=digest,
                                          settings_fingerprint_sha256=digest, score_sha256=None))
            with self.assertRaises(JobConflictError):
                store.checkpoint("job-1", owner_id="owner-b", stage=AnalysisStage.MEDIA_PROBE,
                                 completed_span_count=1, continuation_token="resume", last_artifact_id=None)
            store.checkpoint("job-1", owner_id="owner-a", stage=AnalysisStage.MEDIA_PROBE,
                             completed_span_count=1, continuation_token="resume", last_artifact_id=None)
            with self.assertRaises(JobConflictError):
                store.claim("job-1", owner_id="owner-b", lease_seconds=30, source_sha256=digest,
                            adapter_fingerprint_sha256=digest, runtime_fingerprint_sha256="c" * 64,
                            settings_fingerprint_sha256=digest, score_sha256=None)
            self.assertIsNotNone(store.claim("job-1", owner_id="owner-b", lease_seconds=30, source_sha256=digest,
                                              adapter_fingerprint_sha256=digest, runtime_fingerprint_sha256=digest,
                                              settings_fingerprint_sha256=digest, score_sha256=None))
            self.assertTrue(store.request_cancellation("job-1").cancel_requested)
            with self.assertRaises(JobConflictError):
                store.complete("job-1", owner_id="owner-a")
            self.assertIs(store.complete("job-1", owner_id="owner-b").state, JobState.CANCELLED)

            sidecar = Path(f"{database}-wal")
            sidecar.symlink_to(database)
            with self.assertRaises(JobStoreError):
                store.get("job-1")
            linked = root / "linked.db"
            linked.symlink_to(database)
            with self.assertRaises(JobStoreError):
                SQLiteJobStore(linked)


if __name__ == "__main__":
    unittest.main()
