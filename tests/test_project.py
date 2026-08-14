from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from notewitness.evidence import EvidenceGraph
from notewitness.project import ProjectInitializationError, initialize_project


class ProjectInitializationTests(unittest.TestCase):
    def test_initialize_creates_valid_offline_project(self) -> None:
        with TemporaryDirectory() as temporary:
            temporary_path = Path(temporary).resolve()
            project_path = initialize_project(
                temporary_path / "lesson-study", name="Lesson study"
            )

            payload = json.loads(project_path.read_text(encoding="utf-8"))
            self.assertEqual("offline", payload["network"]["mode"])
            self.assertEqual("Lesson study", payload["project"]["name"])
            self.assertEqual((), EvidenceGraph(payload).validate())
            self.assertTrue((project_path.parent / "media" / "README.txt").exists())

    def test_initialize_creates_private_directories_and_files(self) -> None:
        with TemporaryDirectory() as temporary:
            previous_umask = os.umask(0o777)
            try:
                project_path = initialize_project(
                    Path(temporary).resolve() / "lesson-study"
                )
            finally:
                os.umask(previous_umask)
            media_directory = project_path.parent / "media"

            self.assertEqual(0o700, stat.S_IMODE(project_path.parent.stat().st_mode))
            self.assertEqual(0o700, stat.S_IMODE(media_directory.stat().st_mode))
            self.assertEqual(
                0o700,
                stat.S_IMODE((project_path.parent / "runs").stat().st_mode),
            )
            self.assertEqual(
                0o700,
                stat.S_IMODE((project_path.parent / "exports").stat().st_mode),
            )
            self.assertEqual(0o600, stat.S_IMODE(project_path.stat().st_mode))
            self.assertEqual(
                0o600, stat.S_IMODE((media_directory / "README.txt").stat().st_mode)
            )

    def test_initialize_refuses_non_empty_directory(self) -> None:
        with TemporaryDirectory() as temporary:
            target = Path(temporary).resolve() / "existing"
            target.mkdir()
            (target / "keep.txt").write_text("keep", encoding="utf-8")

            with self.assertRaises(ProjectInitializationError):
                initialize_project(target)

    def test_initialize_restricts_an_empty_existing_directory(self) -> None:
        with TemporaryDirectory() as temporary:
            target = Path(temporary).resolve() / "existing"
            target.mkdir(mode=0o755)
            target.chmod(0o755)
            self.assertEqual(0o755, stat.S_IMODE(target.stat().st_mode))

            initialize_project(target)

            self.assertEqual(0o700, stat.S_IMODE(target.stat().st_mode))

    def test_initialize_refuses_symlink_target(self) -> None:
        with TemporaryDirectory() as temporary:
            temporary_path = Path(temporary).resolve()
            target = temporary_path / "target"
            target.mkdir()
            symlink = temporary_path / "lesson-study"
            symlink.symlink_to(target, target_is_directory=True)

            with self.assertRaises(ProjectInitializationError):
                initialize_project(symlink)

    def test_initialize_refuses_symlink_component(self) -> None:
        with TemporaryDirectory() as temporary:
            temporary_path = Path(temporary).resolve()
            real_parent = temporary_path / "real-parent"
            real_parent.mkdir()
            symlink_parent = temporary_path / "linked-parent"
            symlink_parent.symlink_to(real_parent, target_is_directory=True)

            with self.assertRaises(ProjectInitializationError):
                initialize_project(symlink_parent / "lesson-study")
            self.assertFalse((real_parent / "lesson-study").exists())

    def test_initialize_refuses_symlink_ancestor_before_existing_subdirectory(self) -> None:
        with TemporaryDirectory() as temporary:
            temporary_path = Path(temporary).resolve()
            real_parent = temporary_path / "real-parent"
            nested = real_parent / "nested"
            nested.mkdir(parents=True)
            symlink_parent = temporary_path / "linked-parent"
            symlink_parent.symlink_to(real_parent, target_is_directory=True)

            with self.assertRaises(ProjectInitializationError):
                initialize_project(symlink_parent / "nested" / "lesson-study")
            self.assertFalse((nested / "lesson-study").exists())

    def test_initialize_refuses_file_target(self) -> None:
        with TemporaryDirectory() as temporary:
            target = Path(temporary).resolve() / "not-a-directory"
            target.write_text("keep", encoding="utf-8")

            with self.assertRaises(ProjectInitializationError):
                initialize_project(target)

    def test_initialize_refuses_filesystem_root(self) -> None:
        with self.assertRaises(ProjectInitializationError):
            initialize_project(Path(os.path.sep))


if __name__ == "__main__":
    unittest.main()
