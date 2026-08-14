from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import platform
from tempfile import TemporaryDirectory
import unittest

from notewitness.application.workbench import project_workbench
from notewitness.cli import main
from notewitness.media_ingest import ingest_media
from notewitness.project import initialize_project
from notewitness.project_store import ProjectStore

from tests.support.prototype_cli import fake_analysis_suite


@unittest.skipUnless(platform.system() == "Darwin", "macOS isolation contract")
class PrototypeCLIAnalysisTests(unittest.TestCase):
    def test_explicit_analysis_suite_publishes_note_and_score_alignment(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            parent.chmod(0o700)
            project = parent / "study"
            initialize_project(project)
            media = parent / "lesson.wav"
            media.write_bytes(b"synthetic analysis media")
            media.chmod(0o600)
            imported = ingest_media(project, media, create_restricted_rights=True)
            model = parent / "analysis.model"
            model.write_bytes(b"analysis model")
            model.chmod(0o600)
            score = parent / "study.musicxml"
            score.write_bytes(b"<score-partwise/>")
            score.chmod(0o600)

            output = io.StringIO()
            with redirect_stdout(output):
                status = main(
                    [
                        "analyze-local", str(project), imported.source_id,
                        "--analysis-path", str(fake_analysis_suite(parent)),
                        "--adapter-version", "test-v1", "--adapter-license", "MIT-test-adapter",
                        "--model-path", str(model), "--model-license", "LicenseRef-test-model",
                        "--stage", "note_transcription", "--stage", "score_alignment",
                        "--duration-us", "1000000", "--score-path", str(score),
                        "--score-id", "score:fixture", "--score-license", "CC0-1.0",
                    ]
                )

            result = json.loads(output.getvalue())
            self.assertEqual(0, status)
            self.assertFalse(result["network_used"])
            self.assertEqual(2, len(result["event_ids"]))
            self.assertEqual(["note_transcription", "score_alignment"], result["stages"])
            self.assertEqual("completed", result["state"])
            manifest_path = project / result["artifacts"]["identity_manifest"]
            self.assertTrue(manifest_path.is_file())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(64, len(manifest["score_sha256"]))
            raw_files = sorted((project / result["artifacts"]["run_directory"]).glob("*.raw.json"))
            self.assertEqual(2, len(raw_files))
            graph = ProjectStore(project).load().payload
            events = [item for item in graph["events"] if item["id"] in result["event_ids"]]
            self.assertEqual({"local:note", "local:score_alignment"}, {item["type"] for item in events})

            status_output = io.StringIO()
            with redirect_stdout(status_output):
                status_status = main(["analysis-job", str(project), result["job_id"]])
            self.assertEqual(0, status_status)
            self.assertEqual("completed", json.loads(status_output.getvalue())["state"])

    def test_full_analysis_profile_preserves_overlap_and_music_evidence(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            parent.chmod(0o700)
            project = parent / "study"
            initialize_project(project)
            media = parent / "lesson.wav"
            media.write_bytes(b"complete automatic analysis fixture")
            media.chmod(0o600)
            imported = ingest_media(project, media, create_restricted_rights=True)
            model = parent / "analysis.model"
            model.write_bytes(b"complete analysis model")
            model.chmod(0o600)
            score = parent / "study.musicxml"
            score.write_bytes(b"<score-partwise/>")
            score.chmod(0o600)

            output = io.StringIO()
            with redirect_stdout(output):
                status = main(
                    [
                        "analyze-local", str(project), imported.source_id,
                        "--analysis-path", str(fake_analysis_suite(parent)),
                        "--adapter-version", "test-v1", "--adapter-license", "MIT-test-adapter",
                        "--model-path", str(model), "--model-license", "LicenseRef-test-model",
                        "--duration-us", "1000000", "--detect-overlap",
                        "--score-path", str(score), "--score-id", "score:fixture", "--score-license", "CC0-1.0",
                    ]
                )

            result = json.loads(output.getvalue())
            self.assertEqual(0, status)
            self.assertEqual("completed", result["state"])
            self.assertEqual(7, len(result["event_ids"]))
            graph = ProjectStore(project).load().payload
            events = [item for item in graph["events"] if item["id"] in result["event_ids"]]
            self.assertEqual(
                {
                    "local:diarization", "local:instrument", "local:note", "local:pitch",
                    "local:score_alignment", "speech_over_music",
                },
                {item["type"] for item in events},
            )
            diarization_targets = [
                target
                for event in events if event["type"] == "local:diarization"
                for target in graph["targets"] if target["id"] in event["target_ids"]
            ]
            first, second = sorted(diarization_targets, key=lambda item: item["selector"]["start_us"])
            self.assertLess(
                second["selector"]["start_us"],
                first["selector"]["start_us"] + first["selector"]["duration_us"],
            )
            suggestions = project_workbench(str(project))["lesson"]["transcript_suggestions"]
            self.assertIn("Instrument: piano", {item["display_text"] for item in suggestions})
            self.assertTrue(
                {
                    "instrument", "note", "pitch", "score_alignment", "speaker_segment", "speech_over_music",
                }.issubset({item["content_kind"] for item in suggestions})
            )


if __name__ == "__main__":
    unittest.main()
