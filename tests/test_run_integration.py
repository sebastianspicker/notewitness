from __future__ import annotations

from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest

from notewitness.application.run_integration import (
    MAX_PUBLICATION_BYTES,
    PUBLICATION_FILENAME,
    RunIntegrationError,
    RunIntegrationResult,
    capture_source_identity,
    completed_artifact_sha256s,
    integrate_completed_run,
    select_publication_records,
    write_completed_publication,
)
from notewitness.project import initialize_project
from notewitness.project_store import ProjectStore


class RunIntegrationFacadeTests(unittest.TestCase):
    def test_public_contract_remains_available(self) -> None:
        self.assertEqual("publication.completed.json", PUBLICATION_FILENAME)
        self.assertEqual(16 * 1024 * 1024, MAX_PUBLICATION_BYTES)
        self.assertTrue(issubclass(RunIntegrationError, RuntimeError))
        self.assertEqual(
            {
                "kind",
                "run_id",
                "event_ids",
                "target_ids",
                "project_sha256",
                "already_integrated",
            },
            set(RunIntegrationResult.__dataclass_fields__),
        )
        self.assertTrue(callable(capture_source_identity))
        self.assertTrue(callable(completed_artifact_sha256s))
        self.assertTrue(callable(select_publication_records))
        self.assertTrue(callable(write_completed_publication))

    def test_invalid_publication_leaves_project_unchanged(self) -> None:
        with TemporaryDirectory() as temporary:
            project = Path(temporary) / "study"
            initialize_project(project)
            run_id = "run:" + "a" * 32
            run_directory = project / "runs" / ("a" * 32)
            run_directory.parent.chmod(stat.S_IRWXU)
            run_directory.mkdir(mode=0o700)
            publication = run_directory / PUBLICATION_FILENAME
            publication.write_bytes(b"{")
            publication.chmod(stat.S_IRUSR | stat.S_IWUSR)
            before = ProjectStore(project).load().sha256

            with self.assertRaisesRegex(RunIntegrationError, "invalid JSON"):
                integrate_completed_run(project, run_id)

            self.assertEqual(before, ProjectStore(project).load().sha256)
