from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from notewitness.cli import main


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "fixtures" / "synthetic_lesson" / "project.json"


class CLITests(unittest.TestCase):
    def test_version_reports_package_version(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit) as raised:
            main(["--version"])

        self.assertEqual(0, raised.exception.code)
        self.assertEqual("notewitness 0.1.0a0\n", stdout.getvalue())

    def test_runtime_doctor_reports_non_macos_processing_as_unsupported(self) -> None:
        stdout = io.StringIO()
        with (
            patch(
                "notewitness.prototype_commands.platform.system",
                return_value="Linux",
            ),
            patch(
                "notewitness.prototype_commands._discover_tool",
                return_value=None,
            ),
            redirect_stdout(stdout),
        ):
            status = main(["runtime-doctor"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(6, status)
        self.assertEqual("Linux", payload["host_platform"])
        self.assertFalse(payload["checks"]["local_tool_platform_supported"])
        self.assertFalse(payload["checks"]["network_isolation_available"])

    def test_validate_and_inspect_are_local_and_non_sensitive(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            validate_code = main(["validate", str(FIXTURE_PATH)])
            inspect_code = main(["inspect", str(FIXTURE_PATH)])

        self.assertEqual(0, validate_code)
        self.assertEqual(0, inspect_code)
        self.assertIn('"events": 7', stdout.getvalue())
        self.assertNotIn("release the C♯", stdout.getvalue())

    def test_remote_command_is_denied_for_offline_fixture(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main(
                [
                    "suggest-relations",
                    str(FIXTURE_PATH),
                    "--event",
                    "event:instruction",
                    "--event",
                    "event:demonstration",
                    "--allow-remote",
                ]
            )

        self.assertEqual(3, code)
        self.assertIn("remote_explicit", stderr.getvalue())

    def test_remote_preview_is_local_and_hides_text_unless_requested(self) -> None:
        hidden_stdout = io.StringIO()
        with redirect_stdout(hidden_stdout):
            hidden_code = main(
                [
                    "preview-relations",
                    str(FIXTURE_PATH),
                    "--event",
                    "event:instruction",
                ]
            )
        shown_stdout = io.StringIO()
        with redirect_stdout(shown_stdout):
            shown_code = main(
                [
                    "preview-relations",
                    str(FIXTURE_PATH),
                    "--event",
                    "event:instruction",
                    "--include-text",
                ]
            )

        self.assertEqual(0, hidden_code)
        self.assertEqual(0, shown_code)
        self.assertNotIn("release the C♯", hidden_stdout.getvalue())
        self.assertIn("release the C♯", shown_stdout.getvalue())
        self.assertIn('"rights_allow_remote": false', hidden_stdout.getvalue())

    def test_init_creates_a_project(self) -> None:
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "new-project"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["init", str(target), "--name", "New project"])

            self.assertEqual(0, code)
            self.assertEqual(target / "project.json", Path(stdout.getvalue().strip()))

    def test_lesson_notes_writes_private_local_artifact_without_replacing(self) -> None:
        with TemporaryDirectory() as temporary:
            output = Path(temporary).resolve() / "lesson-notes.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "lesson-notes",
                        str(FIXTURE_PATH),
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(0, code)
            self.assertEqual(output, Path(stdout.getvalue().strip()))
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(payload["artifact_generated_locally"])
            self.assertFalse(payload["contains_remote_derived_evidence"])
            self.assertEqual(7, len(payload["full_transcript"]))
            self.assertEqual(7, len(payload["bookmarks"]))
            self.assertTrue(payload["statistics"]["assessment_free"])
            self.assertEqual(0o600, stat.S_IMODE(output.stat().st_mode))

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                duplicate_code = main(
                    [
                        "lesson-notes",
                        str(FIXTURE_PATH),
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(2, duplicate_code)
            self.assertIn("Refusing to replace", stderr.getvalue())

    def test_capabilities_and_doctor_are_truthful(self) -> None:
        capabilities_stdout = io.StringIO()
        with redirect_stdout(capabilities_stdout):
            capabilities_code = main(["capabilities"])
        doctor_stdout = io.StringIO()
        with redirect_stdout(doctor_stdout):
            doctor_code = main(["doctor", "--profile", "tonic-local"])

        self.assertEqual(0, capabilities_code)
        self.assertEqual(6, doctor_code)
        self.assertIn('"lesson_notes_artifact"', capabilities_stdout.getvalue())
        self.assertIn('"instrument_detection"', capabilities_stdout.getvalue())
        self.assertIn('"ready": false', doctor_stdout.getvalue())

    def test_offline_tuner_and_metronome_commands(self) -> None:
        tuner_stdout = io.StringIO()
        with redirect_stdout(tuner_stdout):
            tuner_code = main(["tuner-reading", "440"])
        metronome_stdout = io.StringIO()
        with redirect_stdout(metronome_stdout):
            metronome_code = main(
                ["metronome-plan", "--bpm", "120", "--bars", "1"]
            )

        self.assertEqual(0, tuner_code)
        self.assertEqual(0, metronome_code)
        self.assertIn('"note_name": "A"', tuner_stdout.getvalue())
        self.assertEqual(4, len(json.loads(metronome_stdout.getvalue())["ticks"]))

    def test_transcription_plan_exposes_noscribe_options_without_inference(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(
                [
                    "transcription-plan",
                    "--job-id",
                    "job:test",
                    "--source-id",
                    "source:test",
                    "--duration-us",
                    "1000000",
                    "--model-profile",
                    "profile:precise",
                    "--language-mode",
                    "fixed",
                    "--language",
                    "de",
                    "--diarization",
                    "exact",
                    "--speakers",
                    "2",
                    "--detect-overlap",
                    "--pause-ms",
                    "1000",
                    "--visible-timestamps",
                    "--format",
                    "webvtt",
                    "--beam-size",
                    "5",
                    "--vad-threshold",
                    "0.35",
                    "--compute-type",
                    "int8",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(0, code)
        self.assertFalse(payload["executes_model"])
        self.assertFalse(payload["network_used"])
        self.assertEqual(5, payload["job"]["adapter_settings"]["beam_size"])
        self.assertEqual(
            0.35, payload["job"]["adapter_settings"]["vad_threshold"]
        )
        self.assertEqual(3, len(payload["export_losses"]))


if __name__ == "__main__":
    unittest.main()
