from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import platform
from tempfile import TemporaryDirectory
import unittest

from notewitness.cli import main
from notewitness.media_ingest import ingest_media
from notewitness.project import initialize_project
from notewitness.project_store import ProjectStore

from tests.support.prototype_cli import fake_ffmpeg, fake_ffprobe, fake_whisper


@unittest.skipUnless(platform.system() == "Darwin", "macOS isolation contract")
class PrototypeCLITranscriptionTests(unittest.TestCase):
    def test_unsupported_disfluency_mode_fails_before_tool_discovery(self) -> None:
        with TemporaryDirectory() as temporary:
            project = Path(temporary).resolve() / "study"
            initialize_project(project)
            error = io.StringIO()

            with redirect_stderr(error):
                status = main(
                    [
                        "transcribe-local",
                        str(project),
                        "source:lesson",
                        "--model-checkpoint",
                        str(project / "missing-model"),
                        "--model-license",
                        "LicenseRef-test-model",
                        "--adapter-license",
                        "MIT-test-adapter",
                        "--ffmpeg-license",
                        "LGPL-test-ffmpeg",
                        "--disfluencies",
                        "suppress",
                    ]
                )

            self.assertEqual(2, status)
            self.assertIn("does not support disfluency suppression", error.getvalue())

    def test_ingest_transcribe_and_review_are_one_durable_local_flow(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            project = parent / "study"
            initialize_project(project)
            media = parent / "lesson.wav"
            media.write_bytes(b"synthetic media")
            ffprobe = fake_ffprobe(parent)

            ingest_output = io.StringIO()
            with redirect_stdout(ingest_output):
                ingest_status = main(
                    [
                        "ingest-media", str(project), str(media),
                        "--create-restricted-rights", "--ffprobe-path", str(ffprobe),
                    ]
                )
            imported = json.loads(ingest_output.getvalue())
            self.assertEqual(0, ingest_status)
            self.assertFalse(imported["network_used"])
            self.assertEqual("audio", imported["metadata"]["kind"])

            model = parent / "model.pt"
            model.write_bytes(b"model fixture")
            whisper = fake_whisper(parent)
            ffmpeg = fake_ffmpeg(parent)
            transcription_output = io.StringIO()
            with redirect_stdout(transcription_output):
                transcription_status = main(
                    [
                        "transcribe-local", str(project), imported["source_id"],
                        "--model-checkpoint", str(model),
                        "--model-license", "LicenseRef-test-model",
                        "--adapter-license", "MIT-test-adapter",
                        "--ffmpeg-license", "LGPL-test-ffmpeg",
                        "--ffprobe-path", str(ffprobe), "--whisper-path", str(whisper),
                        "--ffmpeg-path", str(ffmpeg), "--language", "de",
                        "--pause-ms", "2000", "--visible-timestamps",
                        "--timestamp-interval-ms", "3000", "--format", "html",
                        "--authorize-local-export", "--acknowledge-export-losses",
                    ]
                )
            transcription = json.loads(transcription_output.getvalue())
            self.assertEqual(0, transcription_status)
            self.assertFalse(transcription["network_used"])
            self.assertEqual(1, transcription["segment_count"])
            self.assertTrue(transcription["artifacts"]["manifest"].startswith("runs/"))
            manifest = json.loads(
                (project / transcription["artifacts"]["manifest"]).read_text(encoding="utf-8")
            )
            self.assertEqual(1, len(manifest["runtime_artifacts"]))
            self.assertEqual("launcher+ffmpeg", manifest["effective_settings"]["runtime_artifact_scope"])
            self.assertEqual("include", manifest["job"]["disfluency_policy"])
            self.assertEqual(2_000, manifest["job"]["pause_threshold_ms"])
            self.assertTrue(manifest["job"]["visible_timestamps"])
            self.assertEqual(3_000, manifest["job"]["timestamp_interval_ms"])
            export_path = project / transcription["artifacts"]["export"]
            self.assertIn("<time datetime=", export_path.read_text(encoding="utf-8"))

            with redirect_stdout(io.StringIO()):
                actor_status = main(
                    ["add-actor", str(project), "--actor-id", "actor:researcher", "--role", "researcher"]
                )
            review_output = io.StringIO()
            with redirect_stdout(review_output):
                review_status = main(
                    [
                        "review-accept", str(project), "--event", transcription["event_ids"][0],
                        "--author", "actor:researcher", "--speaker", "actor:researcher",
                        "--reason", "Verified against the recording", "--replacement-text", "Noch einmal, bitte",
                    ]
                )

            self.assertEqual(0, actor_status)
            self.assertEqual(0, review_status)
            review = json.loads(review_output.getvalue())
            self.assertEqual(1, len(review["accepted_event_ids"]))
            events = ProjectStore(project).load().payload["events"]
            self.assertEqual(2, len(events))
            self.assertEqual("machine_suggested", events[0]["review_status"])
            self.assertEqual("human_accepted", events[1]["review_status"])
            self.assertEqual("Noch einmal, bitte", events[1]["body"]["value"])
            self.assertEqual(64, len(events[0]["body"]["raw_artifact_sha256"]))
            self.assertEqual(64, len(events[0]["body"]["normalized_artifact_sha256"]))

    def test_invalid_probe_rolls_media_back_without_publishing_source(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            project = parent / "study"
            initialize_project(project)
            media = parent / "lesson.wav"
            media.write_bytes(b"synthetic media")
            ffprobe = fake_ffprobe(parent, has_audio=False)
            error = io.StringIO()

            with redirect_stderr(error):
                status = main(
                    [
                        "ingest-media", str(project), str(media),
                        "--create-restricted-rights", "--ffprobe-path", str(ffprobe),
                    ]
                )

            self.assertEqual(2, status)
            self.assertIn("audio stream", error.getvalue())
            self.assertEqual([], ProjectStore(project).load().payload["sources"])
            media_files = {path.name for path in (project / "media").iterdir() if path.is_file()}
            self.assertEqual({"README.txt"}, media_files)


if __name__ == "__main__":
    unittest.main()
