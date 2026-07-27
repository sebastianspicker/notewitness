from __future__ import annotations

from pathlib import Path
import unittest

from scripts.verify_public_hygiene import path_violations, text_violations


class PublicHygieneTests(unittest.TestCase):
    def test_rejects_private_and_generated_files(self) -> None:
        cases = (
            "docs/archive/old.md",
            "projects/private/project.json",
            "docs/screenshots/generated/raw.png",
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

    def test_accepts_intentional_public_alpha_files(self) -> None:
        cases = (
            "RELEASE_STATUS.md",
            "docs/RELEASING.md",
            "docs/screenshots/workbench-overview.png",
            "fixtures/synthetic_lesson/project.json",
        )

        for value in cases:
            with self.subTest(path=value):
                self.assertEqual((), path_violations(Path(value)))


if __name__ == "__main__":
    unittest.main()
