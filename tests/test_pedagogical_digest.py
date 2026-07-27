from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from notewitness.application.pedagogical_digest import suggest_practice_relations
from notewitness.application.transcript_review_service import add_project_actor
from notewitness.application.workbench import (
    WorkbenchError,
    accept_evidence_suggestion,
    accept_relation_suggestion,
    project_workbench,
    reject_relation_suggestion,
)
from notewitness.evidence import EvidenceGraph
from notewitness.media_ingest import ingest_media
from notewitness.project import initialize_project
from notewitness.project_store import ProjectStore


class PedagogicalDigestTests(unittest.TestCase):
    def test_explicit_instruction_becomes_reviewable_local_assignment_only(self) -> None:
        with TemporaryDirectory() as temporary:
            project = _project_with_instruction(Path(temporary), "Play the phrase lightly.")

            result = suggest_practice_relations(str(project))
            repeated = suggest_practice_relations(str(project))
            graph = ProjectStore(project).load().payload
            relation = next(item for item in graph["relations"] if item["id"] == result.relation_ids[0])

            self.assertEqual(1, len(result.relation_ids))
            self.assertEqual((), repeated.relation_ids)
            self.assertEqual("local:assigned_for_practice", relation["type"])
            self.assertEqual("machine_suggested", relation["review_status"])
            self.assertEqual("normalized_hypothesis", relation["layer"])
            self.assertEqual(["event:instruction", "event:instruction"], [
                argument["ref_id"] for argument in relation["arguments"]
            ])
            self.assertEqual("explicit_instruction_prefix_v1", relation["confidence"]["rule"])
            self.assertEqual("machine", next(item for item in graph["generators"] if item["id"] == relation["generator_id"])["kind"])
            EvidenceGraph(graph).require_valid()

    def test_human_relation_acceptance_requires_transcript_review_then_projects_practice(self) -> None:
        with TemporaryDirectory() as temporary:
            project = _project_with_instruction(Path(temporary), "Practice the release again.")
            relation_id = suggest_practice_relations(str(project)).relation_ids[0]
            before = ProjectStore(project).load()

            with self.assertRaisesRegex(WorkbenchError, "Accept the transcript evidence"):
                accept_relation_suggestion(
                    str(project), relation_id=relation_id, author_id="actor:researcher",
                    reason="Reviewed locally.", expected_sha256=before.sha256,
                )

            accepted_event = accept_evidence_suggestion(
                str(project), event_id="event:instruction", author_id="actor:researcher",
                actor_id="actor:researcher", reason="Auditioned locally.",
                expected_sha256=before.sha256,
            )
            accepted_relation = accept_relation_suggestion(
                str(project), relation_id=relation_id, author_id="actor:researcher",
                reason="Confirmed as the learner's practice assignment.",
                expected_sha256=accepted_event.project_sha256,
            )
            graph = ProjectStore(project).load().payload
            accepted = next(item for item in graph["relations"] if item["id"] == accepted_relation.record_ids[0])
            lesson = project_workbench(str(project))["lesson"]

            self.assertEqual("human_accepted", accepted["review_status"])
            self.assertEqual("accepted_annotation", accepted["layer"])
            self.assertTrue(all(argument["ref_id"] == accepted_event.record_ids[0] for argument in accepted["arguments"]))
            self.assertEqual(1, len(lesson["practice_tasks"]))
            self.assertEqual("Practice the release again.", lesson["practice_tasks"][0]["text"])
            self.assertEqual((), lesson["relation_suggestions"])
            self.assertEqual(2, len(accepted_relation.revision_ids))
            EvidenceGraph(graph).require_valid()

    def test_relation_rejection_is_append_only_and_removes_it_from_review_projection(self) -> None:
        with TemporaryDirectory() as temporary:
            project = _project_with_instruction(Path(temporary), "Try the phrase again.")
            relation_id = suggest_practice_relations(str(project)).relation_ids[0]
            before = ProjectStore(project).load()

            rejected = reject_relation_suggestion(
                str(project), relation_id=relation_id, author_id="actor:researcher",
                reason="This instruction is not a take-home assignment.",
                expected_sha256=before.sha256,
            )
            graph = ProjectStore(project).load().payload
            lesson = project_workbench(str(project))["lesson"]

            self.assertEqual(1, len(rejected.revision_ids))
            self.assertEqual("machine_suggested", next(item for item in graph["relations"] if item["id"] == relation_id)["review_status"])
            self.assertEqual("reject", next(item for item in graph["revisions"] if item["id"] == rejected.revision_ids[0])["operation"])
            self.assertEqual((), lesson["relation_suggestions"])
            self.assertEqual((), lesson["practice_tasks"])
            EvidenceGraph(graph).require_valid()


def _project_with_instruction(parent: Path, text: str) -> Path:
    parent.chmod(0o700)
    project = parent / "study"
    initialize_project(project)
    media = parent / "lesson.wav"
    media.write_bytes(b"synthetic local media")
    media.chmod(0o600)
    source = ingest_media(project, media, create_restricted_rights=True)
    add_project_actor(str(project), actor_id="actor:researcher", role="researcher")
    snapshot = ProjectStore(project).load()

    def append(payload: dict[str, object]) -> None:
        payload["generators"].append({  # type: ignore[union-attr]
            "id": "generator:fixture-speech", "kind": "machine", "name": "Fixture ASR",
            "version": "1", "model": "fixture", "weight_hash_state": "sha256:" + "a" * 64,
        })
        payload["targets"].append({  # type: ignore[union-attr]
            "id": "target:instruction", "source_id": source.source_id,
            "selector": {"stream_id": "audio", "start_us": 0, "duration_us": 500_000},
            "musical_selector": None, "alignment_state": "not_applicable",
        })
        payload["events"].append({  # type: ignore[union-attr]
            "id": "event:instruction", "type": "speech", "scope": "evidence",
            "actor_id": "actor:researcher", "target_ids": ["target:instruction"],
            "body": {"format": "text", "value": text}, "alternatives": [],
            "generator_id": "generator:fixture-speech", "rights_id": source.rights_id,
            "layer": "normalized_hypothesis", "confidence": {"kind": "probability", "value": 0.9},
            "review_status": "machine_suggested",
        })

    ProjectStore(project).mutate(append, expected_sha256=snapshot.sha256)
    return project


if __name__ == "__main__":
    unittest.main()
