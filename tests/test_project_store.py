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


if __name__ == "__main__":
    unittest.main()
