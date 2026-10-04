from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.verify_public_hygiene import filesystem_violations, path_violations, text_violations


class PublicHygieneTests(unittest.TestCase):
    def test_rejects_private_and_generated_files(self) -> None:
        cases = (
            "RELEASE_STATUS.md",
            "projects/private/project.json",
            "docs/screenshots/generated/raw.json",
            "fixtures/private-lesson.wav",
            "fixtures/local-test.sqlite3",
        )

        for value in cases:
            with self.subTest(path=value):
                self.assertTrue(path_violations(Path(value)))

    def test_rejects_sensitive_text_shapes_without_printing_values(self) -> None:
        cases = (
            ("path", "saved under /" + "Users/local-person/private/project.json"),
            ("windows_path", "C:\\Users\\local-person\\private\\project.json"),
            ("private_key", "-----BEGIN " + "PRIVATE KEY-----"),
            ("token", "sk-" + "proj-abcdefghijklmnopqrstuvwxyz012345"),
            ("email", "maintainer" + "@" + "private.example"),
        )

        for label, value in cases:
            with self.subTest(case=label):
                self.assertTrue(text_violations(value))

        self.assertEqual((), text_violations("https://api.openai.com/v1/responses"))

    def test_accepts_intentional_public_files(self) -> None:
        cases = (
            "docs/RELEASING.md",
            "examples/synthetic-lesson/project.json",
        )

        for value in cases:
            with self.subTest(path=value):
                self.assertEqual((), path_violations(Path(value)))

    def test_rejects_file_and_directory_symbolic_links(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_text("public-looking contents", encoding="utf-8")
            (root / "file-link").symlink_to(target)
            directory = root / "directory"
            directory.mkdir()
            (root / "directory-link").symlink_to(directory, target_is_directory=True)

            self.assertEqual(
                ("symbolic link component",),
                filesystem_violations(Path("file-link"), root=root),
            )
            self.assertEqual(
                ("symbolic link component",),
                filesystem_violations(Path("directory-link"), root=root),
            )
            child = directory / "child"
            child.write_text("safe", encoding="utf-8")
            self.assertEqual(
                ("symbolic link component",),
                filesystem_violations(Path("directory-link/child"), root=root),
            )


if __name__ == "__main__":
    unittest.main()
