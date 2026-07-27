from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import platform
from threading import Event
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from notewitness.adapters.analysis_cli import (
    LocalAnalysisCLIAdapter,
    LocalAnalysisCLISettings,
    LocalAnalysisSource,
)
from notewitness.application.analysis_runtime import (
    LocalAnalysisRunRequest,
    LocalAnalysisRuntime,
    LocalAnalysisRuntimeError,
    LocalAnalysisStep,
)
from notewitness.application.run_integration import (
    RunIntegrationError,
    integrate_completed_run,
)
from notewitness.application.transcript_review_service import add_project_actor
from notewitness.application.workbench import create_exact_time_bookmark
from notewitness.domain.analysis import AnalysisStage
from notewitness.domain.timeline import MediaSpan
from notewitness.local_tools import LocalTool
from notewitness.media_ingest import ingest_media
from notewitness.project import initialize_project
from notewitness.project_store import ProjectStore


@unittest.skipUnless(platform.system() == "Darwin", "macOS isolation contract")
class LocalAnalysisRuntimeTests(unittest.TestCase):
    def test_note_and_score_stages_publish_raw_normalized_reviewable_evidence(self) -> None:
        with TemporaryDirectory() as temporary:
            parent, project, imported, model = _project_fixture(temporary)
            tool = _analysis_tool(parent, malformed_alignment=False)
            steps = _steps(project, imported, model, tool)

            result = LocalAnalysisRuntime().run(
                LocalAnalysisRunRequest(
                    project_root=project,
                    source_id=imported.source_id,
                    spans=(MediaSpan(imported.source_id, "audio", 0, 1_000_000),),
                    steps=steps,
                )
            )

            self.assertEqual(2, len(result.event_ids))
            self.assertTrue(result.manifest_path.is_file())
            self.assertTrue(result.normalized_path.is_file())
            self.assertTrue(
                (result.run_directory / "status.completed.json").is_file()
            )
            raw_files = sorted((result.run_directory / "raw").glob("*.json"))
            self.assertEqual(2, len(raw_files))
            graph = ProjectStore(project).load().payload
            events = [item for item in graph["events"] if item["id"] in result.event_ids]
            self.assertEqual(
                {"local:note", "local:score_alignment"},
                {item["type"] for item in events},
            )
            self.assertTrue(
                all(item["review_status"] == "machine_suggested" for item in events)
            )
            aligned = next(item for item in events if item["type"].endswith("alignment"))
            target = next(
                item for item in graph["targets"] if item["id"] == aligned["target_ids"][0]
            )
            self.assertEqual("aligned", target["alignment_state"])

    def test_bookmark_during_one_shot_analysis_is_preserved_exactly_once(self) -> None:
        with TemporaryDirectory() as temporary:
            parent, project, imported, model = _project_fixture(temporary)
            steps = _steps(
                project,
                imported,
                model,
                _analysis_tool(parent, malformed_alignment=False),
            )
            add_project_actor(
                str(project),
                actor_id="actor:researcher",
                role="researcher",
            )
            entered = Event()
            release = Event()
            original_execute = steps[0].adapter.execute

            def blocked_execute(request: object) -> object:
                entered.set()
                if not release.wait(10):
                    raise RuntimeError("test did not release blocked analysis")
                return original_execute(request)

            request = LocalAnalysisRunRequest(
                project_root=project,
                source_id=imported.source_id,
                spans=(MediaSpan(imported.source_id, "audio", 0, 1_000_000),),
                steps=steps,
            )
            with patch.object(
                steps[0].adapter,
                "execute",
                side_effect=blocked_execute,
            ), ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(LocalAnalysisRuntime().run, request)
                self.assertTrue(entered.wait(10))
                try:
                    before_bookmark = ProjectStore(project).load()
                    bookmark = create_exact_time_bookmark(
                        str(project),
                        source_id=imported.source_id,
                        start_us=250_000,
                        duration_us=100_000,
                        label="Concurrent analysis note",
                        author_id="actor:researcher",
                        expected_sha256=before_bookmark.sha256,
                    )
                finally:
                    release.set()
                result = future.result(timeout=20)

            graph = ProjectStore(project).load().payload
            event_ids = [str(item["id"]) for item in graph["events"]]
            self.assertIn(bookmark.record_ids[-1], event_ids)
            for event_id in result.event_ids:
                self.assertEqual(1, event_ids.count(event_id))
            event_count = len(graph["events"])

            retried = integrate_completed_run(project, result.run_id)

            self.assertTrue(retried.already_integrated)
            self.assertEqual(
                event_count,
                len(ProjectStore(project).load().payload["events"]),
            )

    def test_completed_analysis_artifacts_recover_after_publication_failure(self) -> None:
        with TemporaryDirectory() as temporary:
            parent, project, imported, model = _project_fixture(temporary)
            request = LocalAnalysisRunRequest(
                project_root=project,
                source_id=imported.source_id,
                spans=(MediaSpan(imported.source_id, "audio", 0, 1_000_000),),
                steps=_steps(
                    project,
                    imported,
                    model,
                    _analysis_tool(parent, malformed_alignment=False),
                ),
            )

            with patch(
                "notewitness.application.analysis_runtime.integrate_completed_run",
                side_effect=RunIntegrationError("injected publication failure"),
            ), self.assertRaises(LocalAnalysisRuntimeError):
                LocalAnalysisRuntime().run(request)

            self.assertEqual([], ProjectStore(project).load().payload["events"])
            run_directory = next((project / "runs").glob("analysis-*"))
            self.assertTrue((run_directory / "publication.completed.json").is_file())
            manifest = json.loads(
                (run_directory / "manifest.completed.json").read_text(encoding="utf-8")
            )

            recovered = integrate_completed_run(project, str(manifest["run_id"]))
            retried = integrate_completed_run(project, str(manifest["run_id"]))

            self.assertFalse(recovered.already_integrated)
            self.assertTrue(retried.already_integrated)
            self.assertEqual(2, len(recovered.event_ids))
            self.assertEqual(2, len(ProjectStore(project).load().payload["events"]))

    def test_failed_stage_keeps_raw_recovery_artifact_without_graph_claims(self) -> None:
        with TemporaryDirectory() as temporary:
            parent, project, imported, model = _project_fixture(temporary)
            tool = _analysis_tool(parent, malformed_alignment=True)
            steps = _steps(project, imported, model, tool)

            with self.assertRaises(LocalAnalysisRuntimeError):
                LocalAnalysisRuntime().run(
                    LocalAnalysisRunRequest(
                        project_root=project,
                        source_id=imported.source_id,
                        spans=(
                            MediaSpan(imported.source_id, "audio", 0, 1_000_000),
                        ),
                        steps=steps,
                    )
                )

            self.assertEqual([], ProjectStore(project).load().payload["events"])
            run_directories = tuple((project / "runs").glob("analysis-*"))
            self.assertEqual(1, len(run_directories))
            self.assertTrue((run_directories[0] / "status.failed.json").is_file())
            self.assertEqual(
                2,
                len(tuple((run_directories[0] / "raw").glob("*.json"))),
            )

    def test_duplicate_hypothesis_ids_across_stages_are_not_published(self) -> None:
        with TemporaryDirectory() as temporary:
            parent, project, imported, model = _project_fixture(temporary)
            tool = _analysis_tool(
                parent,
                malformed_alignment=False,
                duplicate_alignment_id=True,
            )

            with self.assertRaises(LocalAnalysisRuntimeError):
                LocalAnalysisRuntime().run(
                    LocalAnalysisRunRequest(
                        project_root=project,
                        source_id=imported.source_id,
                        spans=(
                            MediaSpan(imported.source_id, "audio", 0, 1_000_000),
                        ),
                        steps=_steps(project, imported, model, tool),
                    )
                )

            self.assertEqual([], ProjectStore(project).load().payload["events"])


def _project_fixture(
    temporary: str,
) -> tuple[Path, Path, object, Path]:
    parent = Path(temporary).resolve()
    parent.chmod(0o700)
    project = parent / "study"
    initialize_project(project)
    source = parent / "lesson.wav"
    source.write_bytes(b"synthetic analysis media")
    source.chmod(0o600)
    imported = ingest_media(
        project,
        source,
        create_restricted_rights=True,
    )
    model = parent / "analysis.model"
    model.write_bytes(b"local analysis model")
    model.chmod(0o600)
    return parent, project, imported, model


def _steps(
    project: Path,
    imported: object,
    model: Path,
    tool: Path,
) -> tuple[LocalAnalysisStep, ...]:
    source_id = str(getattr(imported, "source_id"))
    media_path = project / str(getattr(imported, "relative_path"))
    media_raw = media_path.read_bytes()
    model_raw = model.read_bytes()
    score = model.parent / "fixture.score.json"
    score.write_bytes(b'{"score":"fixture"}')
    score.chmod(0o600)
    score_raw = score.read_bytes()
    settings = LocalAnalysisCLISettings(
        working_directory=project,
        media=LocalAnalysisSource(
            source_id,
            media_path,
            hashlib.sha256(media_raw).hexdigest(),
            len(media_raw),
        ),
        model=LocalAnalysisSource(
            "model:analysis-fixture",
            model,
            hashlib.sha256(model_raw).hexdigest(),
            len(model_raw),
        ),
        model_license="LicenseRef-test-model",
        adapter_license="MIT-test-adapter",
        score=LocalAnalysisSource(
            "score:fixture",
            score,
            hashlib.sha256(score_raw).hexdigest(),
            len(score_raw),
        ),
        score_license="CC0-1.0",
        timeout_seconds=30,
    )
    return tuple(
        LocalAnalysisStep(
            LocalAnalysisCLIAdapter(
                LocalTool("analysis-suite", tool),
                stage=stage,
                version="1",
                generator_id="generator:analysis-suite",
                settings=settings,
            ),
            {},
        )
        for stage in (
            AnalysisStage.NOTE_TRANSCRIPTION,
            AnalysisStage.SCORE_ALIGNMENT,
        )
    )


def _analysis_tool(
    parent: Path,
    *,
    malformed_alignment: bool,
    duplicate_alignment_id: bool = False,
) -> Path:
    tool = parent / "analysis-suite"
    note = {
        "state": "ready",
        "hypotheses": [
            {
                "confidence": 0.9,
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
    alignment: object = {
        "state": "ready",
        "hypotheses": [
            {
                "confidence": 0.8,
                "hypothesis_id": (
                    "note:fixture" if duplicate_alignment_id else "alignment:fixture"
                ),
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
    if malformed_alignment:
        alignment = {"unexpected": True}
    tool.write_text(
        "#!/usr/bin/python3\n"
        "import json, pathlib, sys\n"
        "request = json.loads(pathlib.Path(sys.argv[2]).read_text())\n"
        f"note = {note!r}\n"
        f"alignment = {alignment!r}\n"
        "print(json.dumps(note if request['stage'] == 'note_transcription' "
        "else alignment))\n",
        encoding="utf-8",
    )
    tool.chmod(0o700)
    return tool


if __name__ == "__main__":
    unittest.main()
