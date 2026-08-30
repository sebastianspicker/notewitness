from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import argparse
import io
import unittest

from notewitness import __version__
from notewitness.interfaces.cli.main import build_parser, main


EXPECTED_COMMANDS = {
    "add-actor",
    "analysis-job",
    "analyze-local",
    "capabilities",
    "doctor",
    "export-music",
    "ingest-media",
    "init",
    "inspect",
    "integrate-run",
    "lesson-notes",
    "metronome-plan",
    "preview-relations",
    "review-accept",
    "runtime-doctor",
    "suggest-relations",
    "transcribe-local",
    "transcription-plan",
    "tuner-reading",
    "validate",
    "workbench",
}


class CliContractTests(unittest.TestCase):
    def test_command_names_are_stable(self) -> None:
        subparsers = next(
            action
            for action in build_parser()._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertEqual(EXPECTED_COMMANDS, set(subparsers.choices))

    def test_version_output_is_stable(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            main(["--version"])
        self.assertEqual(0, raised.exception.code)
        self.assertEqual(f"notewitness {__version__}\n", output.getvalue())

    def test_missing_or_unknown_command_is_usage_error(self) -> None:
        for arguments in ([], ["unknown-command"]):
            with self.subTest(arguments=arguments):
                error = io.StringIO()
                with redirect_stderr(error), self.assertRaises(SystemExit) as raised:
                    main(arguments)
                self.assertEqual(2, raised.exception.code)
                self.assertIn("usage: notewitness", error.getvalue())


if __name__ == "__main__":
    unittest.main()
