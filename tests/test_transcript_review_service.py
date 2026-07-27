from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from notewitness.application.transcript_review_service import (
    MAX_REPLACEMENT_TEXT_CHARS,
    MAX_REVIEW_REASON_CHARS,
    TranscriptReviewDecision,
    TranscriptReviewError,
    accept_transcript_events,
    add_project_actor,
)
from notewitness.media_ingest import ingest_media
from notewitness.project import initialize_project
from notewitness.project_store import ProjectStore


class TranscriptReviewServiceTests(unittest.TestCase):
    def test_accepts_correction_as_new_human_event_and_adjudication(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            root = parent / "study"
            initialize_project(root)
            source_path = parent / "lesson.wav"
            source_path.write_bytes(b"synthetic media")
            imported = ingest_media(root, source_path, create_restricted_rights=True)
            _append_machine_event(root, imported.source_id, imported.rights_id)
            add_project_actor(root, actor_id="actor:researcher", role="researcher")
            add_project_actor(root, actor_id="actor:teacher", role="teacher")

            result = accept_transcript_events(
                root,
                decisions=(
                    TranscriptReviewDecision(
                        "event:machine-speech",
                        replacement_text="Noch einmal, bitte.",
                        actor_id="actor:teacher",
                    ),
                ),
                author_id="actor:researcher",
                reason="Compared with the local recording",
            )

            project = ProjectStore(root).load().payload
            original = next(
                event for event in project["events"] if event["id"] == "event:machine-speech"
            )
            accepted = next(
                event for event in project["events"] if event["id"] in result.accepted_event_ids
            )
            self.assertEqual("machine_suggested", original["review_status"])
            self.assertEqual("human_accepted", accepted["review_status"])
            self.assertEqual("actor:teacher", accepted["actor_id"])
            self.assertEqual("Noch einmal, bitte.", accepted["body"]["value"])
            self.assertEqual("event:machine-speech", accepted["body"]["source_suggestion_id"])
            self.assertEqual("adjudicate", project["revisions"][0]["operation"])

            with self.assertRaisesRegex(TranscriptReviewError, "already accepted"):
                accept_transcript_events(
                    root,
                    decisions=(TranscriptReviewDecision("event:machine-speech"),),
                    author_id="actor:researcher",
                    reason="duplicate",
                )

    def test_actor_and_machine_source_requirements_fail_atomically(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "study"
            initialize_project(root)
            add_project_actor(root, actor_id="actor:researcher", role="researcher")
            with self.assertRaisesRegex(TranscriptReviewError, "not found"):
                accept_transcript_events(
                    root,
                    decisions=(TranscriptReviewDecision("event:missing"),),
                    author_id="actor:researcher",
                    reason="review",
                )
            self.assertEqual([], ProjectStore(root).load().payload["events"])
            with self.assertRaises(TranscriptReviewError):
                add_project_actor(root, actor_id="actor:researcher", role="duplicate")

    def test_only_eligible_humans_can_accept_transcript_evidence(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            root = parent / "study"
            initialize_project(root)
            source_path = parent / "lesson.wav"
            source_path.write_bytes(b"synthetic media")
            imported = ingest_media(root, source_path, create_restricted_rights=True)
            _append_machine_event(root, imported.source_id, imported.rights_id)
            for role in ("machine", "system", "analysis"):
                add_project_actor(root, actor_id=f"actor:{role}", role=role)
            add_project_actor(
                root,
                actor_id="actor:music-analysis-researcher",
                role="music analysis researcher",
            )

            for role in ("unknown", "machine", "system", "analysis"):
                before = ProjectStore(root).load()
                with self.assertRaisesRegex(
                    TranscriptReviewError, "explicit human project actor"
                ):
                    accept_transcript_events(
                        root,
                        decisions=(TranscriptReviewDecision("event:machine-speech"),),
                        author_id=f"actor:{role}",
                        reason="Invalid automated review",
                    )
                self.assertEqual(before.sha256, ProjectStore(root).load().sha256)

            accepted = accept_transcript_events(
                root,
                decisions=(TranscriptReviewDecision("event:machine-speech"),),
                author_id="actor:music-analysis-researcher",
                reason="Reviewed against the local source recording",
            )
            self.assertEqual(1, len(accepted.accepted_event_ids))

    def test_oversized_review_input_fails_before_mutating_the_project(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            root = parent / "study"
            initialize_project(root)
            source_path = parent / "lesson.wav"
            source_path.write_bytes(b"synthetic media")
            imported = ingest_media(root, source_path, create_restricted_rights=True)
            _append_machine_event(root, imported.source_id, imported.rights_id)
            add_project_actor(root, actor_id="actor:researcher", role="researcher")
            original = (root / "project.json").read_bytes()

            with self.assertRaisesRegex(TranscriptReviewError, "exceeds"):
                accept_transcript_events(
                    root,
                    decisions=(TranscriptReviewDecision("event:machine-speech"),),
                    author_id="actor:researcher",
                    reason="x" * (MAX_REVIEW_REASON_CHARS + 1),
                )
            with self.assertRaisesRegex(ValueError, "exceeds"):
                TranscriptReviewDecision(
                    "event:machine-speech",
                    replacement_text="x" * (MAX_REPLACEMENT_TEXT_CHARS + 1),
                )

            self.assertEqual(original, (root / "project.json").read_bytes())


def _append_machine_event(root: Path, source_id: str, rights_id: str) -> None:
    def append(payload: dict[str, object]) -> None:
        payload["actors"].append(
            {"id": "actor:unknown", "role": "unknown", "visibility": "restricted"}
        )
        payload["generators"].append(
            {
                "id": "generator:machine",
                "kind": "machine",
                "name": "fixture",
                "version": "1",
                "model": "fixture",
                "weight_hash_state": "sha256:" + "a" * 64,
            }
        )
        payload["targets"].append(
            {
                "id": "target:machine-speech",
                "source_id": source_id,
                "selector": {
                    "stream_id": "audio",
                    "start_us": 0,
                    "duration_us": 1_000_000,
                    "spatial": None,
                },
                "musical_selector": None,
                "alignment_state": "not_applicable",
            }
        )
        payload["events"].append(
            {
                "id": "event:machine-speech",
                "type": "speech",
                "scope": "evidence",
                "actor_id": "actor:unknown",
                "target_ids": ["target:machine-speech"],
                "body": {"format": "text", "value": "Noch einmal"},
                "alternatives": [],
                "generator_id": "generator:machine",
                "rights_id": rights_id,
                "layer": "normalized_hypothesis",
                "confidence": {"kind": "model_probability", "value": 0.8},
                "review_status": "machine_suggested",
            }
        )

    ProjectStore(root).mutate(append)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
