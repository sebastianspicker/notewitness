from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import platform
import stat
from threading import Event
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from notewitness.adapters.ffprobe import FFprobeMediaProbe
from notewitness.adapters.whisper_cli import WhisperCLIAdapter, WhisperCLISettings
from notewitness.application.transcription_runtime import (
    LocalTranscriptionRequest,
    LocalTranscriptionRuntime,
    LocalTranscriptionRuntimeError,
)
from notewitness.application.run_integration import (
    RunIntegrationError,
    integrate_completed_run,
)
from notewitness.application.transcript_review_service import add_project_actor
from notewitness.application.workbench import create_exact_time_bookmark
from notewitness.cli import main
from notewitness.domain.transcription import TranscriptExportFormat
from notewitness.domain.transcription import DisfluencyPolicy
from notewitness.local_tools import LocalTool
from notewitness.media_ingest import ingest_media
from notewitness.project import initialize_project
from notewitness.project_store import ProjectStore


@unittest.skipUnless(platform.system() == "Darwin", "macOS isolation contract")
class LocalTranscriptionRuntimeTests(unittest.TestCase):
    def test_ingested_media_runs_to_reviewable_graph_and_private_export(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            root = parent / "study"
            initialize_project(root)
            source_path = parent / "lesson.wav"
            source_path.write_bytes(b"synthetic media")
            imported = ingest_media(root, source_path, create_restricted_rights=True)
            runtime = _runtime(parent, transcript=True)

            result = runtime.run(
                LocalTranscriptionRequest(
                    project_root=root,
                    source_id=imported.source_id,
                    export_format=TranscriptExportFormat.HTML,
                    authorize_local_export=True,
                    acknowledge_export_losses=True,
                )
            )

            project = ProjectStore(root).load().payload
            self.assertEqual(1, len(project["events"]))
            self.assertEqual("machine_suggested", project["events"][0]["review_status"])
            self.assertEqual("normalized_hypothesis", project["events"][0]["layer"])
            self.assertEqual("Noch einmal", project["events"][0]["body"]["value"])
            self.assertTrue(result.manifest_path.exists())
            self.assertTrue(result.canonical_evidence_path.exists())
            self.assertIsNotNone(result.export_path)
            assert result.export_path is not None
            self.assertIn("Noch einmal", result.export_path.read_text(encoding="utf-8"))
            self.assertEqual(0o600, stat.S_IMODE(result.export_path.stat().st_mode))
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual("completed", manifest["state"])
            self.assertTrue(manifest["effective_settings"]["network_isolated"])
            self.assertEqual([], manifest["detected_languages"])
            self.assertEqual("include", manifest["job"]["disfluency_policy"])
            self.assertIsNone(manifest["job"]["pause_threshold_ms"])
            self.assertFalse(manifest["job"]["visible_timestamps"])
            self.assertEqual(60_000, manifest["job"]["timestamp_interval_ms"])
            canonical = json.loads(
                result.canonical_evidence_path.read_text(encoding="utf-8")
            )
            normalized_bytes = result.normalized_transcript_path.read_bytes()
            self.assertEqual(
                hashlib.sha256(normalized_bytes).hexdigest(),
                canonical["normalized_transcript_sha256"],
            )
            raw_path = next((result.run_directory / "raw").glob("*.json"))
            self.assertEqual(
                hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                canonical["raw_response_sha256"],
            )

    def test_bookmark_during_asr_is_preserved_with_exactly_once_publication(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            root = parent / "study"
            initialize_project(root)
            source_path = parent / "lesson.wav"
            source_path.write_bytes(b"synthetic media")
            imported = ingest_media(root, source_path, create_restricted_rights=True)
            add_project_actor(
                str(root),
                actor_id="actor:researcher",
                role="researcher",
            )
            runtime = _runtime(parent, transcript=True)
            entered = Event()
            release = Event()
            original_transcribe = runtime._asr.transcribe

            def blocked_transcribe(**kwargs: object) -> object:
                entered.set()
                if not release.wait(10):
                    raise RuntimeError("test did not release blocked ASR")
                return original_transcribe(**kwargs)

            with patch.object(
                runtime._asr,
                "transcribe",
                side_effect=blocked_transcribe,
            ), ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    runtime.run,
                    LocalTranscriptionRequest(root, imported.source_id),
                )
                self.assertTrue(entered.wait(10))
                try:
                    before_bookmark = ProjectStore(root).load()
                    bookmark = create_exact_time_bookmark(
                        str(root),
                        source_id=imported.source_id,
                        start_us=500_000,
                        duration_us=100_000,
                        label="Concurrent observation",
                        author_id="actor:researcher",
                        expected_sha256=before_bookmark.sha256,
                    )
                finally:
                    release.set()
                result = future.result(timeout=20)

            project = ProjectStore(root).load().payload
            event_ids = [str(item["id"]) for item in project["events"]]
            self.assertIn(bookmark.record_ids[-1], event_ids)
            self.assertEqual(1, sum(item in event_ids for item in result.event_ids))
            event_count = len(project["events"])

            retried = integrate_completed_run(root, result.run_id)

            self.assertTrue(retried.already_integrated)
            self.assertEqual(
                event_count,
                len(ProjectStore(root).load().payload["events"]),
            )

            def revise_rights(payload: dict[str, object]) -> None:
                rights = payload["rights"]
                assert isinstance(rights, list)
                rights[0]["retention"] = "changed-after-run"

            ProjectStore(root).mutate(revise_rights)
            with self.assertRaisesRegex(RunIntegrationError, "source or rights"):
                integrate_completed_run(root, result.run_id)

    def test_failed_asr_keeps_private_recovery_status_without_graph_claims(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            root = parent / "study"
            initialize_project(root)
            source_path = parent / "lesson.wav"
            source_path.write_bytes(b"synthetic media")
            imported = ingest_media(root, source_path, create_restricted_rights=True)

            with self.assertRaises(LocalTranscriptionRuntimeError):
                _runtime(parent, transcript=False).run(
                    LocalTranscriptionRequest(root, imported.source_id)
                )

            run_directories = tuple((root / "runs").iterdir())
            self.assertEqual(1, len(run_directories))
            failure = json.loads(
                (run_directories[0] / "status.failed.json").read_text(encoding="utf-8")
            )
            self.assertEqual("failed", failure["state"])
            self.assertEqual([], ProjectStore(root).load().payload["events"])

    def test_export_failure_is_integration_failure_without_graph_commit(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            root = parent / "study"
            initialize_project(root)
            source_path = parent / "lesson.wav"
            source_path.write_bytes(b"synthetic media")
            imported = ingest_media(root, source_path, create_restricted_rights=True)

            with patch(
                "notewitness.application.transcription_runtime._publish_export",
                side_effect=OSError("injected export failure"),
            ), self.assertRaises(LocalTranscriptionRuntimeError):
                _runtime(parent, transcript=True, language=None).run(
                    LocalTranscriptionRequest(
                        root,
                        imported.source_id,
                        export_format=TranscriptExportFormat.HTML,
                        authorize_local_export=True,
                        acknowledge_export_losses=True,
                    )
                )

            run_directory = next((root / "runs").iterdir())
            manifest = json.loads(
                (run_directory / "manifest.completed.json").read_text(
                    encoding="utf-8"
                )
            )
            integration = json.loads(
                (run_directory / "status.integration-failed.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("completed", manifest["state"])
            self.assertIsNone(manifest["detected_languages"][0]["probability"])
            self.assertEqual("integration_failed", integration["state"])
            self.assertFalse((run_directory / "status.failed.json").exists())
            self.assertTrue((run_directory / "publication.completed.json").exists())
            self.assertEqual([], ProjectStore(root).load().payload["events"])

            output = io.StringIO()
            with redirect_stdout(output):
                recovery_status = main(
                    ["integrate-run", str(root), str(integration["run_id"])]
                )
            recovered = json.loads(output.getvalue())

            self.assertEqual(0, recovery_status)
            self.assertFalse(recovered["already_integrated"])
            self.assertEqual(1, len(recovered["event_ids"]))
            self.assertEqual(1, len(ProjectStore(root).load().payload["events"]))

            retry_output = io.StringIO()
            with redirect_stdout(retry_output):
                retry_status = main(
                    ["integrate-run", str(root), str(integration["run_id"])]
                )
            self.assertEqual(0, retry_status)
            self.assertTrue(json.loads(retry_output.getvalue())["already_integrated"])
            self.assertEqual(1, len(ProjectStore(root).load().payload["events"]))

    def test_export_requires_authorization_before_any_runtime_work(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "study"
            initialize_project(root)
            with self.assertRaisesRegex(ValueError, "authorization"):
                LocalTranscriptionRequest(
                    root,
                    "source:lesson",
                    export_format=TranscriptExportFormat.TEXT,
                )


class LocalTranscriptionRequestTests(unittest.TestCase):
    def test_rejects_unsupported_disfluency_suppression(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not support disfluency suppression"):
            LocalTranscriptionRequest(
                Path("/private/tmp/study"),
                "source:lesson",
                disfluency_policy=DisfluencyPolicy.SUPPRESS,
            )

    def test_validates_canonical_export_options(self) -> None:
        root = Path("/private/tmp/study")
        for options in (
            {"pause_threshold_ms": 500},
            {"visible_timestamps": 1},
            {"timestamp_interval_ms": 0},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                LocalTranscriptionRequest(root, "source:lesson", **options)


def _runtime(
    parent: Path,
    *,
    transcript: bool,
    language: str | None = "de",
) -> LocalTranscriptionRuntime:
    ffprobe_script = parent / "ffprobe-fixture"
    ffprobe_script.write_text(
        "#!/usr/bin/python3\n"
        "print('{\"streams\":[{\"codec_type\":\"audio\","
        "\"codec_name\":\"pcm_s16le\",\"duration\":\"5.0\","
        "\"sample_rate\":\"16000\",\"channels\":1}],"
        "\"format\":{\"duration\":\"5.0\",\"format_name\":\"wav\"}}')\n",
        encoding="utf-8",
    )
    ffprobe_script.chmod(0o700)
    whisper_script = parent / ("whisper-good" if transcript else "whisper-failed")
    script = (
        "#!/usr/bin/python3\n"
        "import json, pathlib, sys\n"
        "audio = pathlib.Path(sys.argv[1])\n"
        "output = pathlib.Path(sys.argv[sys.argv.index('--output_dir') + 1])\n"
    )
    if transcript:
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
        script += (
            f"payload = {payload!r}\n"
            "(output / (audio.stem + '.json')).write_text("
            "json.dumps(payload), encoding='utf-8')\n"
        )
    whisper_script.write_text(script, encoding="utf-8")
    whisper_script.chmod(0o700)
    model = parent / "model.pt"
    model.write_bytes(b"model fixture")
    return LocalTranscriptionRuntime(
        media_probe=FFprobeMediaProbe(LocalTool("ffprobe", ffprobe_script)),
        asr=WhisperCLIAdapter(
            LocalTool("whisper", whisper_script),
            WhisperCLISettings(
                model,
                "LicenseRef-test-model",
                "MIT-test-adapter",
                language=language,
                threads=1,
                timeout_seconds=30,
            ),
        ),
    )


if __name__ == "__main__":
    unittest.main()
