from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from notewitness.application.workbench_local_executor import (
    LocalWorkbenchExecutor,
    WorkbenchRuntimeConfigurationError,
)
from notewitness.application.workbench_processing import WorkbenchJobKind
from notewitness.project import initialize_project
from notewitness.project_store import ProjectStore


class WorkbenchLocalExecutorConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.parent = Path(self.temporary.name).resolve()
        self.parent.chmod(0o700)
        self.project = self.parent / "study"
        initialize_project(self.project)
        self.whisper = _executable(self.parent / "whisper")
        self.ffprobe = _executable(self.parent / "ffprobe")
        self.ffmpeg = _executable(self.parent / "ffmpeg")
        self.analysis = _executable(self.parent / "analysis-suite")
        self.checkpoint = _private_file(self.parent / "whisper-model.bin")
        self.analysis_model = _private_file(self.parent / "analysis-model.bin")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_private_config_approves_both_local_runtimes_without_paths_in_status(self) -> None:
        path = self.parent / "runtime.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "transcription": {
                        "adapter_license": "MIT",
                        "ffmpeg_license": "LGPL-2.1-or-later",
                        "ffmpeg_path": str(self.ffmpeg),
                        "ffprobe_path": str(self.ffprobe),
                        "model_checkpoint": str(self.checkpoint),
                        "model_license": "model-card-license",
                        "whisper_path": str(self.whisper),
                    },
                    "analysis": {
                        "adapter_license": "MIT",
                        "adapter_version": "fixture-1",
                        "analysis_path": str(self.analysis),
                        "ffprobe_path": str(self.ffprobe),
                        "model_license": "model-card-license",
                        "model_path": str(self.analysis_model),
                        "stages": [
                            "activity_segmentation",
                            "anonymous_diarization",
                            "note_transcription",
                            "continuous_pitch",
                            "instrument_detection",
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)

        executor = LocalWorkbenchExecutor.from_private_config(self.project, path)
        status = executor.status()

        self.assertTrue(status["transcription_ready"])
        self.assertTrue(status["analysis_ready"])
        self.assertFalse(status["complete_ready"])
        self.assertEqual(
            ["instrument_diarization"],
            status["missing_complete_modalities"],
        )
        self.assertEqual(
            {
                "speech_transcription": True,
                "activity_segmentation": True,
                "anonymous_diarization": True,
                "note_transcription": True,
                "instrument_detection": True,
                "instrument_diarization": False,
            },
            status["modalities"],
        )
        self.assertFalse(status["network_used"])
        self.assertNotIn(str(self.parent), json.dumps(status))
        self.assertIsNotNone(executor.ingest_probe())

    def test_config_rejects_open_permissions_and_unknown_keys(self) -> None:
        path = self.parent / "runtime.json"
        path.write_text(json.dumps({"version": 1, "unexpected": True}), encoding="utf-8")
        path.chmod(0o644)
        with self.assertRaisesRegex(
            WorkbenchRuntimeConfigurationError,
            "owner_private",
        ):
            LocalWorkbenchExecutor.from_private_config(self.project, path)
        path.chmod(0o600)
        with self.assertRaisesRegex(
            WorkbenchRuntimeConfigurationError,
            "keys_invalid",
        ):
            LocalWorkbenchExecutor.from_private_config(self.project, path)

    def test_v2_config_uses_distinct_provider_and_model_per_stage(self) -> None:
        diarization = _executable(self.parent / "pyannote-bridge")
        notes = _executable(self.parent / "basic-pitch-bridge")
        diarization_model = _private_file(self.parent / "pyannote-config.yaml")
        note_model = _private_file(self.parent / "basic-pitch.tflite")
        note_model.write_bytes(b"distinct basic pitch model bytes")
        note_model.chmod(0o600)
        path = self.parent / "runtime-v2.json"
        path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "analysis": {
                        "ffprobe_path": str(self.ffprobe),
                        "detect_overlap": True,
                        "providers": [
                            {
                                "adapter_license": "MIT",
                                "adapter_version": "pyannote-4",
                                "analysis_path": str(diarization),
                                "model_license": "CC-BY-4.0",
                                "model_path": str(diarization_model),
                                "stage": "anonymous_diarization",
                            },
                            {
                                "adapter_license": "Apache-2.0",
                                "adapter_version": "basic-pitch-0.4",
                                "analysis_path": str(notes),
                                "model_license": "Apache-2.0",
                                "model_path": str(note_model),
                                "stage": "note_transcription",
                            },
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)

        executor = LocalWorkbenchExecutor.from_private_config(self.project, path)

        self.assertEqual(
            ["anonymous_diarization", "note_transcription"],
            executor.status()["analysis_stages"],
        )
        assert executor.analysis is not None
        self.assertNotEqual(
            executor.analysis.providers[0].tool.executable,
            executor.analysis.providers[1].tool.executable,
        )
        self.assertNotEqual(
            executor.analysis.providers[0].model.sha256,
            executor.analysis.providers[1].model.sha256,
        )
        self.assertNotIn(str(self.parent), json.dumps(executor.status()))

    def test_diarization_mode_off_is_unavailable_and_blocks_complete_pass(self) -> None:
        path = self.parent / "runtime-v2-diarization-off.json"
        path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "transcription": {
                        "adapter_license": "MIT",
                        "ffmpeg_license": "LGPL-2.1-or-later",
                        "ffmpeg_path": str(self.ffmpeg),
                        "ffprobe_path": str(self.ffprobe),
                        "model_checkpoint": str(self.checkpoint),
                        "model_license": "model-card-license",
                        "whisper_path": str(self.whisper),
                    },
                    "analysis": {
                        "ffprobe_path": str(self.ffprobe),
                        "diarization_mode": "off",
                        "providers": [
                            self._provider("activity_segmentation"),
                            self._provider("anonymous_diarization"),
                            self._provider("note_transcription"),
                            self._provider("instrument_diarization"),
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)

        status = LocalWorkbenchExecutor.from_private_config(self.project, path).status()

        self.assertFalse(status["modalities"]["anonymous_diarization"])
        self.assertFalse(status["complete_ready"])
        self.assertEqual(["anonymous_diarization"], status["missing_complete_modalities"])

    def _provider(self, stage: str) -> dict[str, str]:
        return {
            "adapter_license": "MIT",
            "adapter_version": "fixture-1",
            "analysis_path": str(self.analysis),
            "model_license": "fixture-model",
            "model_path": str(self.analysis_model),
            "stage": stage,
        }

    def test_v2_config_rejects_repeated_provider_stage(self) -> None:
        path = self.parent / "runtime-v2-repeated.json"
        provider = {
            "adapter_license": "MIT",
            "adapter_version": "fixture-1",
            "analysis_path": str(self.analysis),
            "model_license": "fixture-model",
            "model_path": str(self.analysis_model),
            "stage": "note_transcription",
        }
        path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "analysis": {
                        "ffprobe_path": str(self.ffprobe),
                        "providers": [provider, dict(provider)],
                    },
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)

        with self.assertRaisesRegex(
            WorkbenchRuntimeConfigurationError, "analysis_stages_repeated"
        ):
            LocalWorkbenchExecutor.from_private_config(self.project, path)

    def test_startup_bound_checkpoint_rejects_replacement_before_execution(self) -> None:
        executor = self._transcription_executor()
        self.checkpoint.write_bytes(b"replacement checkpoint bytes")
        self.checkpoint.chmod(0o600)

        with self.assertRaisesRegex(
            WorkbenchRuntimeConfigurationError,
            "configured_transcription_checkpoint_changed",
        ):
            executor._transcribe(
                "source:unused",
                lambda: False,
                lambda _percent, _message: None,
                run_token="0" * 32,
            )

    def test_retry_recovers_published_attempt_without_rerunning_model(self) -> None:
        executor = self._transcription_executor()
        runs = ProjectStore(self.project).ensure_private_directory("runs")
        invocations: list[str] = []
        completed: list[str] = []
        job_id = "job:workbench-1234567890abcdef"

        def publish_marker(
            _source_id: str,
            _cancel: object,
            _progress: object,
            *,
            run_token: str,
        ) -> None:
            invocations.append(run_token)
            directory = runs / run_token
            directory.mkdir(mode=0o700)
            marker = directory / "publication.completed.json"
            marker.write_text("{}", encoding="utf-8")
            marker.chmod(0o600)

        def crash_after_publication(_step: str) -> None:
            raise RuntimeError("fault after integration before GUI checkpoint")

        with patch.object(executor, "_transcribe", side_effect=publish_marker):
            with self.assertRaisesRegex(RuntimeError, "before GUI checkpoint"):
                executor.execute(
                    WorkbenchJobKind.TRANSCRIPTION,
                    "source:fixture",
                    job_id=job_id,
                    attempt=1,
                    cancellation_requested=lambda: False,
                    report_progress=lambda _percent, _message: None,
                    completed_steps=frozenset(),
                    mark_step_completed=crash_after_publication,
                )

            with patch(
                "notewitness.application.workbench_local_executor.integrate_completed_run"
            ) as integrate:
                executor.execute(
                    WorkbenchJobKind.TRANSCRIPTION,
                    "source:fixture",
                    job_id=job_id,
                    attempt=2,
                    cancellation_requested=lambda: False,
                    report_progress=lambda _percent, _message: None,
                    completed_steps=frozenset(),
                    mark_step_completed=completed.append,
                )

        self.assertEqual(1, len(invocations))
        self.assertEqual(["transcription"], completed)
        integrate.assert_called_once_with(
            self.project,
            f"run:{invocations[0]}",
        )

    def _transcription_executor(self) -> LocalWorkbenchExecutor:
        path = self.parent / "transcription-runtime.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "transcription": {
                        "adapter_license": "MIT",
                        "ffmpeg_license": "LGPL-2.1-or-later",
                        "ffmpeg_path": str(self.ffmpeg),
                        "ffprobe_path": str(self.ffprobe),
                        "model_checkpoint": str(self.checkpoint),
                        "model_license": "model-card-license",
                        "whisper_path": str(self.whisper),
                    },
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)
        return LocalWorkbenchExecutor.from_private_config(self.project, path)


def _executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path


def _private_file(path: Path) -> Path:
    path.write_bytes(b"fixture model bytes")
    path.chmod(0o600)
    return path
