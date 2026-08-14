from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import platform
from tempfile import TemporaryDirectory
import unittest

from notewitness.cli import main

from tests.support.prototype_cli import fake_ffmpeg, fake_ffprobe, fake_whisper


@unittest.skipUnless(platform.system() == "Darwin", "macOS isolation contract")
class PrototypeCLIRuntimeDoctorTests(unittest.TestCase):
    def test_runtime_doctor_is_read_only_and_requires_explicit_model_contract(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            incomplete_status = main(["runtime-doctor"])
        incomplete = json.loads(output.getvalue())

        self.assertEqual(6, incomplete_status)
        self.assertFalse(incomplete["checks"]["explicit_model_configuration_valid"])
        self.assertFalse(incomplete["prerequisites_ready_for_transcription_attempt"])

        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            model = parent / "model.pt"
            model.write_bytes(b"model fixture")
            output = io.StringIO()
            with redirect_stdout(output):
                ready_status = main(
                    [
                        "runtime-doctor", "--model-checkpoint", str(model),
                        "--model-license", "LicenseRef-test", "--adapter-license", "MIT-test",
                        "--ffmpeg-license", "LGPL-test", "--ffprobe-path", str(fake_ffprobe(parent)),
                        "--whisper-path", str(fake_whisper(parent)), "--ffmpeg-path", str(fake_ffmpeg(parent)),
                    ]
                )
            ready = json.loads(output.getvalue())

            self.assertEqual(0, ready_status)
            self.assertTrue(ready["prerequisites_ready_for_transcription_attempt"])
            self.assertFalse(ready["checkpoint_content_verified"])
            self.assertFalse(ready["end_to_end_transcription_verified"])
            self.assertFalse(ready["full_music_analysis_ready"])
            self.assertEqual("Darwin", ready["host_platform"])
            self.assertTrue(ready["checks"]["local_tool_platform_supported"])
            self.assertNotIn("local_lesson_digest", ready["missing_from_full_profile"])
            self.assertFalse(ready["network_used"])


if __name__ == "__main__":
    unittest.main()
