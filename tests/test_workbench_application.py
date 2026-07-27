from __future__ import annotations

from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from notewitness.application.transcript_review_service import add_project_actor
from notewitness.application.workbench import (
    WorkbenchError,
    accept_evidence_suggestion,
    create_exact_time_bookmark,
    project_workbench,
    revise_evidence_annotation,
    set_practice_task_completed,
)
from notewitness.media_ingest import ingest_media
from notewitness.project import initialize_project
from notewitness.project_store import ProjectConflictError, ProjectStore


class WorkbenchApplicationTests(unittest.TestCase):
    def test_machine_or_unknown_actor_cannot_author_human_review(self) -> None:
        with TemporaryDirectory() as temporary:
            project, source_id = _project_fixture(Path(temporary))
            add_project_actor(
                str(project),
                actor_id="actor:unknown-reviewer",
                role="unknown",
            )
            _append_note_suggestion(project, source_id)
            before = ProjectStore(project).load()

            with self.assertRaisesRegex(WorkbenchError, "explicit human"):
                accept_evidence_suggestion(
                    str(project),
                    event_id="event:note-suggestion",
                    author_id="actor:unknown-reviewer",
                    actor_id="actor:researcher",
                    reason="Invalid machine-authored review",
                    expected_sha256=before.sha256,
                )

            self.assertEqual(before.sha256, ProjectStore(project).load().sha256)

    def test_actor_snapshot_and_review_share_human_evidence_eligibility_policy(self) -> None:
        with TemporaryDirectory() as temporary:
            project, source_id = _project_fixture(Path(temporary))
            for role in ("unknown", "machine", "system", "analysis"):
                add_project_actor(project, actor_id=f"actor:{role}", role=role)
            add_project_actor(
                project,
                actor_id="actor:music-analysis-researcher",
                role="music analysis researcher",
            )
            _append_note_suggestion(project, source_id)

            actors = {
                actor["id"]: actor for actor in project_workbench(str(project))["actors"]
            }
            for role in ("unknown", "machine", "system", "analysis"):
                self.assertFalse(actors[f"actor:{role}"]["human_evidence_eligible"])
                before = ProjectStore(project).load()
                with self.assertRaisesRegex(WorkbenchError, "explicit human"):
                    accept_evidence_suggestion(
                        str(project),
                        event_id="event:note-suggestion",
                        author_id=f"actor:{role}",
                        actor_id="actor:researcher",
                        reason="Invalid automated review",
                        expected_sha256=before.sha256,
                    )
                self.assertEqual(before.sha256, ProjectStore(project).load().sha256)

            reviewer = actors["actor:music-analysis-researcher"]
            self.assertTrue(reviewer["human_evidence_eligible"])
            before = ProjectStore(project).load()
            result = accept_evidence_suggestion(
                str(project),
                event_id="event:note-suggestion",
                author_id="actor:music-analysis-researcher",
                actor_id="actor:researcher",
                reason="Reviewed against the local source recording",
                expected_sha256=before.sha256,
            )
            self.assertEqual(1, len(result.record_ids))

    def test_exact_time_bookmark_is_durable_without_cluttering_transcript(self) -> None:
        with TemporaryDirectory() as temporary:
            project, source_id = _project_fixture(Path(temporary))
            before = ProjectStore(project).load()

            result = create_exact_time_bookmark(
                project,
                source_id=source_id,
                start_us=250_000,
                duration_us=100_000,
                label="Check the release",
                author_id="actor:researcher",
                expected_sha256=before.sha256,
            )

            snapshot = project_workbench(str(project))
            self.assertNotEqual(before.sha256, result.project_sha256)
            self.assertEqual(1, len(snapshot["lesson"]["bookmarks"]))
            self.assertEqual("Check the release", snapshot["lesson"]["bookmarks"][0]["label"])
            self.assertEqual((), snapshot["lesson"]["full_transcript"])
            self.assertEqual(1, snapshot["lesson"]["statistics"]["bookmark_count"])

            with self.assertRaises(ProjectConflictError):
                create_exact_time_bookmark(
                    project,
                    source_id=source_id,
                    start_us=500_000,
                    duration_us=100_000,
                    label="Stale write",
                    author_id="actor:researcher",
                    expected_sha256=before.sha256,
                )

    def test_generic_review_accepts_music_evidence_append_only(self) -> None:
        with TemporaryDirectory() as temporary:
            project, source_id = _project_fixture(Path(temporary))
            _append_note_suggestion(project, source_id)
            before = ProjectStore(project).load()

            result = accept_evidence_suggestion(
                str(project),
                event_id="event:note-suggestion",
                author_id="actor:researcher",
                actor_id="actor:researcher",
                reason="Auditioned against the local recording",
                expected_sha256=before.sha256,
            )

            graph = ProjectStore(project).load().payload
            suggestion = next(
                item for item in graph["events"] if item["id"] == "event:note-suggestion"
            )
            accepted = next(
                item for item in graph["events"] if item["id"] == result.record_ids[0]
            )
            self.assertEqual("machine_suggested", suggestion["review_status"])
            self.assertEqual("human_accepted", accepted["review_status"])
            self.assertEqual("actor:researcher", accepted["actor_id"])
            self.assertEqual(
                "event:note-suggestion",
                accepted["body"]["source_suggestion_id"],
            )
            lesson = project_workbench(str(project))["lesson"]
            self.assertEqual(1, len(lesson["full_transcript"]))
            self.assertEqual(0, len(lesson["transcript_suggestions"]))

            with self.assertRaisesRegex(WorkbenchError, "already accepted"):
                accept_evidence_suggestion(
                    str(project),
                    event_id="event:note-suggestion",
                    author_id="actor:researcher",
                    actor_id="actor:researcher",
                    reason="Duplicate",
                    expected_sha256=result.project_sha256,
                )

    def test_text_revision_supersedes_visible_annotation_append_only(self) -> None:
        with TemporaryDirectory() as temporary:
            project, source_id = _project_fixture(Path(temporary))
            _append_speech_suggestion(project, source_id)
            before = ProjectStore(project).load()
            accepted = accept_evidence_suggestion(
                str(project),
                event_id="event:speech-suggestion",
                author_id="actor:researcher",
                actor_id="actor:researcher",
                reason="Auditioned locally",
                expected_sha256=before.sha256,
            )

            revised = revise_evidence_annotation(
                str(project),
                event_id=accepted.record_ids[0],
                author_id="actor:researcher",
                actor_id="actor:researcher",
                reason="Corrected one word",
                replacement_text="Play the phrase more lightly.",
                expected_sha256=accepted.project_sha256,
            )

            graph = ProjectStore(project).load().payload
            self.assertEqual(3, len(graph["events"]))
            revision = next(
                item for item in graph["revisions"] if item["id"] == revised.revision_ids[0]
            )
            self.assertEqual(list(accepted.revision_ids), revision["parent_revision_ids"])
            lesson = project_workbench(str(project))["lesson"]
            self.assertEqual(1, len(lesson["full_transcript"]))
            self.assertEqual(
                "Play the phrase more lightly.",
                lesson["full_transcript"][0]["display_text"],
            )
            self.assertEqual(0, len(lesson["transcript_suggestions"]))

    def test_practice_completion_round_trips_as_append_only_local_state(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            parent.chmod(0o700)
            project = parent / "study"
            fixture = Path(__file__).parents[1] / "fixtures" / "synthetic_lesson"
            shutil.copytree(fixture, project)
            project.chmod(0o700)
            (project / "project.json").chmod(0o600)
            (project / "script.txt").chmod(0o600)
            before = ProjectStore(project).load()
            task = project_workbench(str(project))["lesson"]["practice_tasks"][0]

            completed = set_practice_task_completed(
                str(project),
                task_id=task["task_id"],
                completed=True,
                author_id="actor:student",
                expected_sha256=before.sha256,
            )

            lesson = project_workbench(str(project))["lesson"]
            self.assertTrue(lesson["practice_tasks"][0]["completed"])
            reopened = set_practice_task_completed(
                str(project),
                task_id=task["task_id"],
                completed=False,
                author_id="actor:student",
                expected_sha256=completed.project_sha256,
            )
            self.assertEqual(64, len(reopened.project_sha256))
            self.assertFalse(
                project_workbench(str(project))["lesson"]["practice_tasks"][0][
                    "completed"
                ]
            )


def _project_fixture(parent: Path) -> tuple[Path, str]:
    parent = parent.resolve()
    parent.chmod(0o700)
    project = parent / "study"
    initialize_project(project)
    media = parent / "lesson.wav"
    media.write_bytes(b"synthetic workbench media")
    media.chmod(0o600)
    imported = ingest_media(
        project,
        media,
        create_restricted_rights=True,
    )
    add_project_actor(
        str(project),
        actor_id="actor:researcher",
        role="researcher",
    )
    return project, imported.source_id


def _append_note_suggestion(project: Path, source_id: str) -> None:
    snapshot = ProjectStore(project).load()
    source = next(item for item in snapshot.payload["sources"] if item["id"] == source_id)

    def append(payload: dict[str, object]) -> None:
        payload["generators"].append(  # type: ignore[union-attr]
            {
                "id": "generator:note-model",
                "kind": "machine",
                "model": "fixture-note-model",
                "name": "Fixture note detector",
                "version": "1",
                "weight_hash_state": "sha256:" + "a" * 64,
            }
        )
        payload["targets"].append(  # type: ignore[union-attr]
            {
                "alignment_state": "unknown",
                "id": "target:note-suggestion",
                "selector": {
                    "duration_us": 250_000,
                    "start_us": 0,
                    "stream_id": "audio",
                },
                "source_id": source_id,
            }
        )
        payload["events"].append(  # type: ignore[union-attr]
            {
                "actor_id": "actor:researcher",
                "alternatives": [],
                "body": {
                    "format": "notewitness.note.v1",
                    "value": {"frequency_hz": 440.0, "midi_pitch": 69.0},
                },
                "confidence": {"kind": "probability", "value": 0.9},
                "generator_id": "generator:note-model",
                "id": "event:note-suggestion",
                "layer": "normalized_hypothesis",
                "review_status": "machine_suggested",
                "rights_id": source["rights_id"],
                "scope": "evidence",
                "target_ids": ["target:note-suggestion"],
                "type": "local:note",
            }
        )

    ProjectStore(project).mutate(append, expected_sha256=snapshot.sha256)


def _append_speech_suggestion(project: Path, source_id: str) -> None:
    snapshot = ProjectStore(project).load()
    source = next(
        item for item in snapshot.payload["sources"] if item["id"] == source_id
    )

    def append(payload: dict[str, object]) -> None:
        payload["generators"].append(  # type: ignore[union-attr]
            {
                "id": "generator:speech-model",
                "kind": "machine",
                "model": "fixture-speech-model",
                "name": "Fixture speech recognizer",
                "version": "1",
                "weight_hash_state": "sha256:" + "b" * 64,
            }
        )
        payload["targets"].append(  # type: ignore[union-attr]
            {
                "alignment_state": "not_applicable",
                "id": "target:speech-suggestion",
                "selector": {
                    "duration_us": 500_000,
                    "start_us": 0,
                    "stream_id": "audio",
                },
                "source_id": source_id,
            }
        )
        payload["events"].append(  # type: ignore[union-attr]
            {
                "actor_id": "actor:researcher",
                "alternatives": [],
                "body": {"format": "text", "value": "Play the phrase lightly."},
                "confidence": {"kind": "probability", "value": 0.9},
                "generator_id": "generator:speech-model",
                "id": "event:speech-suggestion",
                "layer": "normalized_hypothesis",
                "review_status": "machine_suggested",
                "rights_id": source["rights_id"],
                "scope": "evidence",
                "target_ids": ["target:speech-suggestion"],
                "type": "speech",
            }
        )

    ProjectStore(project).mutate(append, expected_sha256=snapshot.sha256)


if __name__ == "__main__":
    unittest.main()
