from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import platform
from tempfile import TemporaryDirectory
import unittest

from notewitness.cli import main
from notewitness.application.workbench import project_workbench
from notewitness.media_ingest import ingest_media
from notewitness.project import initialize_project
from notewitness.project_store import ProjectStore


@unittest.skipUnless(platform.system() == "Darwin", "macOS isolation contract")
class PrototypeCLITests(unittest.TestCase):
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
            ffprobe = _fake_ffprobe(parent)

            ingest_output = io.StringIO()
            with redirect_stdout(ingest_output):
                ingest_status = main(
                    [
                        "ingest-media",
                        str(project),
                        str(media),
                        "--create-restricted-rights",
                        "--ffprobe-path",
                        str(ffprobe),
                    ]
                )
            imported = json.loads(ingest_output.getvalue())
            self.assertEqual(0, ingest_status)
            self.assertFalse(imported["network_used"])
            self.assertEqual("audio", imported["metadata"]["kind"])

            model = parent / "model.pt"
            model.write_bytes(b"model fixture")
            whisper = _fake_whisper(parent)
            ffmpeg = _fake_ffmpeg(parent)
            transcription_output = io.StringIO()
            with redirect_stdout(transcription_output):
                transcription_status = main(
                    [
                        "transcribe-local",
                        str(project),
                        imported["source_id"],
                        "--model-checkpoint",
                        str(model),
                        "--model-license",
                        "LicenseRef-test-model",
                        "--adapter-license",
                        "MIT-test-adapter",
                        "--ffmpeg-license",
                        "LGPL-test-ffmpeg",
                        "--ffprobe-path",
                        str(ffprobe),
                        "--whisper-path",
                        str(whisper),
                        "--ffmpeg-path",
                        str(ffmpeg),
                        "--language",
                        "de",
                        "--pause-ms",
                        "2000",
                        "--visible-timestamps",
                        "--timestamp-interval-ms",
                        "3000",
                        "--format",
                        "html",
                        "--authorize-local-export",
                        "--acknowledge-export-losses",
                    ]
                )
            transcription = json.loads(transcription_output.getvalue())
            self.assertEqual(0, transcription_status)
            self.assertFalse(transcription["network_used"])
            self.assertEqual(1, transcription["segment_count"])
            self.assertTrue(transcription["artifacts"]["manifest"].startswith("runs/"))
            manifest = json.loads(
                (project / transcription["artifacts"]["manifest"]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(1, len(manifest["runtime_artifacts"]))
            self.assertEqual(
                "launcher+ffmpeg",
                manifest["effective_settings"]["runtime_artifact_scope"],
            )
            self.assertEqual("include", manifest["job"]["disfluency_policy"])
            self.assertEqual(2_000, manifest["job"]["pause_threshold_ms"])
            self.assertTrue(manifest["job"]["visible_timestamps"])
            self.assertEqual(3_000, manifest["job"]["timestamp_interval_ms"])
            export_path = project / transcription["artifacts"]["export"]
            self.assertIn("<time datetime=", export_path.read_text(encoding="utf-8"))

            with redirect_stdout(io.StringIO()):
                actor_status = main(
                    [
                        "add-actor",
                        str(project),
                        "--actor-id",
                        "actor:researcher",
                        "--role",
                        "researcher",
                    ]
                )
            review_output = io.StringIO()
            with redirect_stdout(review_output):
                review_status = main(
                    [
                        "review-accept",
                        str(project),
                        "--event",
                        transcription["event_ids"][0],
                        "--author",
                        "actor:researcher",
                        "--speaker",
                        "actor:researcher",
                        "--reason",
                        "Verified against the recording",
                        "--replacement-text",
                        "Noch einmal, bitte",
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
            self.assertEqual(
                64, len(events[0]["body"]["normalized_artifact_sha256"])
            )

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
                        "runtime-doctor",
                        "--model-checkpoint",
                        str(model),
                        "--model-license",
                        "LicenseRef-test",
                        "--adapter-license",
                        "MIT-test",
                        "--ffmpeg-license",
                        "LGPL-test",
                        "--ffprobe-path",
                        str(_fake_ffprobe(parent)),
                        "--whisper-path",
                        str(_fake_whisper(parent)),
                        "--ffmpeg-path",
                        str(_fake_ffmpeg(parent)),
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

    def test_explicit_analysis_suite_publishes_note_and_score_alignment(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            parent.chmod(0o700)
            project = parent / "study"
            initialize_project(project)
            media = parent / "lesson.wav"
            media.write_bytes(b"synthetic analysis media")
            media.chmod(0o600)
            imported = ingest_media(
                project,
                media,
                create_restricted_rights=True,
            )
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
                        "analyze-local",
                        str(project),
                        imported.source_id,
                        "--analysis-path",
                        str(_fake_analysis_suite(parent)),
                        "--adapter-version",
                        "test-v1",
                        "--adapter-license",
                        "MIT-test-adapter",
                        "--model-path",
                        str(model),
                        "--model-license",
                        "LicenseRef-test-model",
                        "--stage",
                        "note_transcription",
                        "--stage",
                        "score_alignment",
                        "--duration-us",
                        "1000000",
                        "--score-path",
                        str(score),
                        "--score-id",
                        "score:fixture",
                        "--score-license",
                        "CC0-1.0",
                    ]
                )

            result = json.loads(output.getvalue())
            self.assertEqual(0, status)
            self.assertFalse(result["network_used"])
            self.assertEqual(2, len(result["event_ids"]))
            self.assertEqual(
                ["note_transcription", "score_alignment"],
                result["stages"],
            )
            self.assertEqual("completed", result["state"])
            manifest_path = project / result["artifacts"]["identity_manifest"]
            self.assertTrue(manifest_path.is_file())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(64, len(manifest["score_sha256"]))
            raw_files = sorted(
                (project / result["artifacts"]["run_directory"]).glob("*.raw.json")
            )
            self.assertEqual(2, len(raw_files))
            graph = ProjectStore(project).load().payload
            events = [
                item for item in graph["events"] if item["id"] in result["event_ids"]
            ]
            self.assertEqual(
                {"local:note", "local:score_alignment"},
                {item["type"] for item in events},
            )

            status_output = io.StringIO()
            with redirect_stdout(status_output):
                status_status = main(
                    ["analysis-job", str(project), result["job_id"]]
                )
            self.assertEqual(0, status_status)
            self.assertEqual(
                "completed",
                json.loads(status_output.getvalue())["state"],
            )

    def test_full_analysis_profile_preserves_overlap_and_music_evidence(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            parent.chmod(0o700)
            project = parent / "study"
            initialize_project(project)
            media = parent / "lesson.wav"
            media.write_bytes(b"complete automatic analysis fixture")
            media.chmod(0o600)
            imported = ingest_media(
                project,
                media,
                create_restricted_rights=True,
            )
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
                        "analyze-local",
                        str(project),
                        imported.source_id,
                        "--analysis-path",
                        str(_fake_analysis_suite(parent)),
                        "--adapter-version",
                        "test-v1",
                        "--adapter-license",
                        "MIT-test-adapter",
                        "--model-path",
                        str(model),
                        "--model-license",
                        "LicenseRef-test-model",
                        "--duration-us",
                        "1000000",
                        "--detect-overlap",
                        "--score-path",
                        str(score),
                        "--score-id",
                        "score:fixture",
                        "--score-license",
                        "CC0-1.0",
                    ]
                )

            result = json.loads(output.getvalue())
            self.assertEqual(0, status)
            self.assertEqual("completed", result["state"])
            self.assertEqual(7, len(result["event_ids"]))
            graph = ProjectStore(project).load().payload
            events = [
                item for item in graph["events"] if item["id"] in result["event_ids"]
            ]
            self.assertEqual(
                {
                    "local:diarization",
                    "local:instrument",
                    "local:note",
                    "local:pitch",
                    "local:score_alignment",
                    "speech_over_music",
                },
                {item["type"] for item in events},
            )
            diarization_targets = [
                target
                for event in events
                if event["type"] == "local:diarization"
                for target in graph["targets"]
                if target["id"] in event["target_ids"]
            ]
            first, second = sorted(
                diarization_targets,
                key=lambda item: item["selector"]["start_us"],
            )
            self.assertLess(
                second["selector"]["start_us"],
                first["selector"]["start_us"]
                + first["selector"]["duration_us"],
            )
            suggestions = project_workbench(str(project))["lesson"][
                "transcript_suggestions"
            ]
            self.assertIn(
                "Instrument: piano",
                {item["display_text"] for item in suggestions},
            )
            self.assertTrue(
                {
                    "instrument",
                    "note",
                    "pitch",
                    "score_alignment",
                    "speaker_segment",
                    "speech_over_music",
                }.issubset({item["content_kind"] for item in suggestions})
            )

    def test_invalid_probe_rolls_media_back_without_publishing_source(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            project = parent / "study"
            initialize_project(project)
            media = parent / "lesson.wav"
            media.write_bytes(b"synthetic media")
            ffprobe = _fake_ffprobe(parent, has_audio=False)
            error = io.StringIO()

            with redirect_stderr(error):
                status = main(
                    [
                        "ingest-media",
                        str(project),
                        str(media),
                        "--create-restricted-rights",
                        "--ffprobe-path",
                        str(ffprobe),
                    ]
                )

            self.assertEqual(2, status)
            self.assertIn("audio stream", error.getvalue())
            self.assertEqual([], ProjectStore(project).load().payload["sources"])
            media_files = {
                path.name for path in (project / "media").iterdir() if path.is_file()
            }
            self.assertEqual({"README.txt"}, media_files)


def _fake_ffprobe(parent: Path, *, has_audio: bool = True) -> Path:
    path = parent / ("ffprobe-good" if has_audio else "ffprobe-no-audio")
    stream = "audio" if has_audio else "video"
    path.write_text(
        "#!/usr/bin/python3\n"
        "print('{\"streams\":[{\"codec_type\":\""
        f"{stream}"
        "\",\"codec_name\":\"fixture\",\"duration\":\"5.0\","
        "\"sample_rate\":\"16000\",\"channels\":1}],"
        "\"format\":{\"duration\":\"5.0\",\"format_name\":\"wav\"}}')\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def _fake_analysis_suite(parent: Path) -> Path:
    path = parent / "analysis-suite"
    note = {
        "state": "ready",
        "hypotheses": [
            {
                "confidence": 0.91,
                "frequency_hz": 440.0,
                "hypothesis_id": "note:fixture",
                "midi_pitch": 69.0,
                "span": {
                    "duration_us": 500_000,
                    "start_us": 0,
                    "stream_id": "audio",
                },
                "state": "ready",
            }
        ],
        "diagnostics": [],
        "continuation_token": None,
    }
    alignment = {
        "state": "ready",
        "hypotheses": [
            {
                "confidence": 0.83,
                "hypothesis_id": "alignment:fixture",
                "outcome": "aligned",
                "score_id": "score:fixture",
                "score_position": {"bar": 1, "beat": 1.0},
                "source_hypothesis_ids": ["note:fixture"],
                "span": {
                    "duration_us": 500_000,
                    "start_us": 0,
                    "stream_id": "audio",
                },
                "state": "ready",
            }
        ],
        "diagnostics": [],
        "continuation_token": None,
    }
    activity = {
        "state": "ready",
        "hypotheses": [
            {
                "confidence": 0.88,
                "hypothesis_id": "activity:overlap",
                "kind": "speech_over_music",
                "span": {
                    "duration_us": 900_000,
                    "start_us": 0,
                    "stream_id": "audio",
                },
                "state": "ready",
            }
        ],
        "diagnostics": [],
        "continuation_token": None,
    }
    diarization = {
        "state": "ready",
        "hypotheses": [
            {
                "anonymous_cluster_id": "SPEAKER_00",
                "confidence": 0.86,
                "hypothesis_id": "speaker:one",
                "span": {
                    "duration_us": 600_000,
                    "start_us": 0,
                    "stream_id": "audio",
                },
                "state": "ready",
            },
            {
                "anonymous_cluster_id": "SPEAKER_01",
                "confidence": 0.79,
                "hypothesis_id": "speaker:two",
                "span": {
                    "duration_us": 500_000,
                    "start_us": 400_000,
                    "stream_id": "audio",
                },
                "state": "ready",
            },
        ],
        "diagnostics": [],
        "continuation_token": None,
    }
    pitch = {
        "state": "ready",
        "hypotheses": [
            {
                "confidence": 0.84,
                "frequency_hz": 442.0,
                "hypothesis_id": "pitch:fixture",
                "span": {
                    "duration_us": 100_000,
                    "start_us": 200_000,
                    "stream_id": "audio",
                },
                "state": "ready",
            }
        ],
        "diagnostics": [],
        "continuation_token": None,
    }
    instrument = {
        "state": "ready",
        "hypotheses": [
            {
                "confidence": 0.92,
                "hypothesis_id": "instrument:fixture",
                "instrument_label": "piano",
                "span": {
                    "duration_us": 800_000,
                    "start_us": 100_000,
                    "stream_id": "audio",
                },
                "state": "ready",
            }
        ],
        "diagnostics": [],
        "continuation_token": None,
    }
    path.write_text(
        "#!/usr/bin/python3\n"
        "import json, pathlib, sys\n"
        "request = json.loads(pathlib.Path(sys.argv[2]).read_text())\n"
        f"note = {note!r}\n"
        f"alignment = {alignment!r}\n"
        f"activity = {activity!r}\n"
        f"diarization = {diarization!r}\n"
        f"pitch = {pitch!r}\n"
        f"instrument = {instrument!r}\n"
        "payloads = {'activity_segmentation': activity, "
        "'anonymous_diarization': diarization, "
        "'note_transcription': note, 'continuous_pitch': pitch, "
        "'instrument_detection': instrument, 'score_alignment': alignment}\n"
        "payload = payloads[request['stage']]\n"
        "print(json.dumps(payload))\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def _fake_whisper(parent: Path) -> Path:
    path = parent / "whisper-good"
    payload = {
        "language": "de",
        "segments": [
            {
                "start": 1.0,
                "end": 2.0,
                "text": " Noch einmal",
                "avg_logprob": -0.2,
                "words": [
                    {
                        "word": " Noch",
                        "start": 1.0,
                        "end": 1.4,
                        "probability": 0.9,
                    },
                    {
                        "word": " einmal",
                        "start": 1.4,
                        "end": 1.9,
                        "probability": 0.8,
                    },
                ],
            }
        ],
    }
    path.write_text(
        "#!/usr/bin/python3\n"
        "import json, pathlib, sys\n"
        "if '--help' in sys.argv:\n print('fixture whisper')\n raise SystemExit(0)\n"
        "audio = pathlib.Path(sys.argv[1])\n"
        "output = pathlib.Path(sys.argv[sys.argv.index('--output_dir') + 1])\n"
        f"payload = {payload!r}\n"
        "(output / (audio.stem + '.json')).write_text("
        "json.dumps(payload), encoding='utf-8')\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def _fake_ffmpeg(parent: Path) -> Path:
    path = parent / "ffmpeg"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path


if __name__ == "__main__":
    unittest.main()
