from __future__ import annotations

import copy
from pathlib import Path
import unittest

from notewitness.application.lesson_notes import LessonNotesProjector
from notewitness.application.lesson_projection_events import full_transcript_kind
from notewitness.evidence import EvidenceGraph
from notewitness.presentation.timeline import TimelineLaneKind, TimelineViewModel


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "fixtures" / "synthetic_lesson" / "project.json"


class LessonNotesProjectionTests(unittest.TestCase):
    def test_brand_namespace_does_not_turn_every_event_into_a_note(self) -> None:
        self.assertEqual(
            "speech_over_music",
            full_transcript_kind(
                "speech_over_music",
                "application/vnd.notewitness.activity+json",
            ),
        )
        self.assertEqual(
            "note",
            full_transcript_kind(
                "local:extension",
                "application/vnd.notewitness.note+json",
            ),
        )

    @classmethod
    def setUpClass(cls) -> None:
        cls.graph = EvidenceGraph.load(FIXTURE_PATH)
        cls.notes = LessonNotesProjector.project(cls.graph)

    def test_projection_delivers_local_evidence_linked_lesson_recall(self) -> None:
        notes = self.notes

        self.assertTrue(notes.artifact_generated_locally)
        self.assertFalse(notes.contains_remote_derived_evidence)
        self.assertEqual("offline", notes.network_mode)
        self.assertEqual(64, len(notes.source_graph.canonical_sha256))
        self.assertEqual("0.1.0", notes.source_graph.schema_version)
        self.assertEqual(7, len(notes.activity))
        self.assertEqual(3, len(notes.transcript))
        self.assertEqual(7, len(notes.full_transcript))
        self.assertEqual(7, len(notes.bookmarks))
        self.assertEqual(8, len(notes.summary.key_moments))
        self.assertEqual(1, len(notes.summary.feedback))
        self.assertEqual(1, len(notes.practice_tasks))
        self.assertEqual(notes.practice_tasks, notes.practice_plan.tasks)
        self.assertEqual(
            "Again from bar 18, leichter; release the C♯.",
            notes.practice_tasks[0].text,
        )
        self.assertEqual(("event:instruction",), notes.practice_tasks[0].source_event_ids)
        self.assertEqual(
            ("relation:assigned-for-practice",),
            notes.practice_tasks[0].source_relation_ids,
        )
        self.assertEqual("human_created", notes.practice_tasks[0].review_status)
        self.assertEqual(
            ("generator:fixture-human",), notes.practice_tasks[0].generator_ids
        )
        self.assertEqual(0, notes.bookmarks[0].anchor.span.start_us)

    def test_summary_and_stats_retain_provenance_without_scoring(self) -> None:
        feedback = self.notes.summary.feedback[0]
        revision = next(
            moment
            for moment in self.notes.summary.key_moments
            if moment.relation_type == "revises"
        )

        self.assertEqual("event:feedback", feedback.event_id)
        self.assertEqual("human_created", feedback.review_status)
        self.assertEqual("relation:revises", revision.relation_id)
        self.assertEqual(
            ("event:attempt-2", "event:attempt-1"), revision.event_ids
        )
        self.assertEqual("accepted_annotation", revision.layer)
        self.assertTrue(self.notes.statistics.assessment_free)
        self.assertEqual(30_000_000, self.notes.statistics.timeline_duration_us)
        self.assertEqual(0, self.notes.statistics.note_or_pitch_event_count)

    def test_no_score_project_does_not_fabricate_score_topics(self) -> None:
        payload = copy.deepcopy(self.graph.payload)
        for target in payload["targets"]:
            target["musical_selector"] = None
            target["alignment_state"] = "not_alignable"
        no_score_notes = LessonNotesProjector.project(EvidenceGraph(payload))

        self.assertEqual((), no_score_notes.summary.topics)
        self.assertEqual(7, len(no_score_notes.activity))

    def test_structured_note_entry_is_preserved_in_full_transcript(self) -> None:
        payload = copy.deepcopy(self.graph.payload)
        payload["generators"].append(
            {
                "id": "generator:note-model",
                "kind": "machine",
                "name": "Synthetic note adapter",
                "version": "1",
                "model": "fixture",
                "weight_hash_state": "verified:fixture",
            }
        )
        payload["events"].append(
            {
                "id": "event:note-c4",
                "type": "local:note",
                "scope": "evidence",
                "actor_id": "actor:student",
                "target_ids": ["target:attempt-1"],
                "body": {
                    "format": "notewitness.note.v1",
                    "value": {"midi_pitch": 60.2, "frequency_hz": 264.7},
                },
                "alternatives": [{"midi_pitch": 61.0}],
                "generator_id": "generator:note-model",
                "rights_id": "rights:fixture-public-local-only",
                "layer": "normalized_hypothesis",
                "confidence": {"kind": "probability", "value": 0.7},
                "review_status": "machine_suggested",
            }
        )

        notes = LessonNotesProjector.project(EvidenceGraph(payload))
        entry = next(
            item
            for item in notes.transcript_suggestions
            if item.event_id == "event:note-c4"
        )

        self.assertEqual("note", entry.content_kind)
        self.assertEqual(60.2, entry.body_value["midi_pitch"])
        self.assertEqual(({"midi_pitch": 61.0},), entry.alternatives)
        self.assertEqual("generator:note-model", entry.generator_id)
        self.assertEqual("machine_suggested", entry.review_status)
        self.assertEqual(0, notes.statistics.note_or_pitch_event_count)
        timeline = TimelineViewModel.from_lesson_notes(notes)
        performance = next(
            lane
            for lane in timeline.lanes
            if lane.kind is TimelineLaneKind.PERFORMANCE
        )
        research = next(
            lane
            for lane in timeline.lanes
            if lane.kind is TimelineLaneKind.RESEARCH
        )
        self.assertFalse(
            any("event:note-c4" in item.item_id for item in performance.items)
        )
        self.assertTrue(
            any("event:note-c4" in item.item_id for item in research.items)
        )

    def test_accepted_note_keeps_every_anchor_in_performance_lane(self) -> None:
        payload = copy.deepcopy(self.graph.payload)
        payload["events"].append(
            {
                "id": "event:accepted-note",
                "type": "local:note",
                "scope": "evidence",
                "actor_id": "actor:student",
                "target_ids": ["target:attempt-1", "target:attempt-2"],
                "body": {
                    "format": "notewitness.note.v1",
                    "value": {"midi_pitch": 60.0, "frequency_hz": 261.626},
                },
                "alternatives": [],
                "generator_id": "generator:fixture-human",
                "rights_id": "rights:fixture-public-local-only",
                "layer": "accepted_annotation",
                "confidence": {"kind": "not_applicable"},
                "review_status": "human_created",
            }
        )

        notes = LessonNotesProjector.project(EvidenceGraph(payload))
        timeline = TimelineViewModel.from_lesson_notes(notes)
        performance = next(
            lane
            for lane in timeline.lanes
            if lane.kind is TimelineLaneKind.PERFORMANCE
        )
        accepted_items = tuple(
            item for item in performance.items if "event:accepted-note" in item.item_id
        )

        self.assertEqual(2, len(accepted_items))
        self.assertTrue(all(item.playback is not None for item in accepted_items))
        self.assertEqual(
            {9_000_000, 18_000_000},
            {
                item.playback.start_us
                for item in accepted_items
                if item.playback is not None
            },
        )
        self.assertEqual(1, notes.statistics.note_or_pitch_event_count)

    def test_contested_assignment_is_review_only_not_homework(self) -> None:
        payload = copy.deepcopy(self.graph.payload)
        assignment = next(
            relation
            for relation in payload["relations"]
            if relation["id"] == "relation:assigned-for-practice"
        )
        assignment["layer"] = "normalized_hypothesis"
        assignment["review_status"] = "contested"

        notes = LessonNotesProjector.project(EvidenceGraph(payload))

        self.assertEqual((), notes.practice_tasks)
        self.assertEqual(1, len(notes.relation_suggestions))
        self.assertEqual(
            "relation:assigned-for-practice",
            notes.relation_suggestions[0].relation_id,
        )

    def test_local_artifact_does_not_hide_unknown_remote_derivation(self) -> None:
        payload = copy.deepcopy(self.graph.payload)
        payload["network"]["mode"] = "remote_explicit"

        notes = LessonNotesProjector.project(EvidenceGraph(payload))

        self.assertTrue(notes.artifact_generated_locally)
        self.assertIsNone(notes.contains_remote_derived_evidence)

    def test_timeline_items_are_keyboard_grouped_and_playable(self) -> None:
        timeline = TimelineViewModel.from_lesson_notes(self.notes)

        self.assertEqual(7, len(timeline.lanes))
        self.assertEqual(set(TimelineLaneKind), {lane.kind for lane in timeline.lanes})
        self.assertEqual(
            len(timeline.lanes),
            len({lane.keyboard_shortcut for lane in timeline.lanes}),
        )
        source_lane = next(
            lane for lane in timeline.lanes if lane.kind is TimelineLaneKind.SOURCE
        )
        self.assertEqual(1, len(source_lane.items))
        self.assertIsNotNone(source_lane.items[0].playback)
        self.assertEqual(30_000_000, source_lane.items[0].playback.duration_us)
        transcript_lane = next(
            lane for lane in timeline.lanes if lane.kind is TimelineLaneKind.TRANSCRIPT
        )
        self.assertEqual(7, len(transcript_lane.items))
        all_item_ids = [item.item_id for lane in timeline.lanes for item in lane.items]
        self.assertEqual(len(all_item_ids), len(set(all_item_ids)))
        for lane in timeline.lanes:
            self.assertTrue(lane.keyboard_shortcut)
            for item in lane.items:
                self.assertTrue(item.accessibility_label)
                if item.playback is not None:
                    self.assertGreaterEqual(item.playback.start_us, 0)

    def test_late_review_evidence_extends_the_source_timeline(self) -> None:
        payload = copy.deepcopy(self.graph.payload)
        payload["generators"].append(
            {
                "id": "generator:late-suggestion",
                "kind": "machine",
                "name": "Synthetic review adapter",
                "version": "1",
                "model": "fixture",
                "weight_hash_state": "verified:fixture",
            }
        )
        payload["targets"].append(
            {
                "id": "target:late-review",
                "source_id": "source:synthetic-script",
                "selector": {
                    "stream_id": "timeline",
                    "start_us": 60_000_000,
                    "duration_us": 1_000_000,
                    "spatial": None,
                },
                "musical_selector": None,
                "alignment_state": "unknown",
            }
        )
        payload["events"].append(
            {
                "id": "event:late-review",
                "type": "speech",
                "scope": "evidence",
                "actor_id": "actor:teacher",
                "target_ids": ["target:late-review"],
                "body": {"format": "text", "value": "Unreviewed late phrase"},
                "alternatives": [],
                "generator_id": "generator:late-suggestion",
                "rights_id": "rights:fixture-public-local-only",
                "layer": "normalized_hypothesis",
                "confidence": {"kind": "probability", "value": 0.5},
                "review_status": "machine_suggested",
            }
        )

        notes = LessonNotesProjector.project(EvidenceGraph(payload))
        timeline = TimelineViewModel.from_lesson_notes(notes)
        extent = notes.statistics.timeline_extents[0]
        research = next(
            lane
            for lane in timeline.lanes
            if lane.kind is TimelineLaneKind.RESEARCH
        )
        late = next(
            item for item in research.items if "event:late-review" in item.item_id
        )

        self.assertEqual(61_000_000, extent.end_us)
        self.assertEqual(61_000_000, notes.statistics.timeline_duration_us)
        self.assertIsNotNone(late.playback)
        self.assertLessEqual(
            late.playback.start_us + late.playback.duration_us,
            extent.end_us,
        )

    def test_source_lane_includes_unannotated_sources_without_fake_duration(self) -> None:
        payload = copy.deepcopy(self.graph.payload)
        second_source = copy.deepcopy(payload["sources"][0])
        second_source.update(
            {
                "id": "source:unannotated",
                "kind": "audio",
                "uri": "media/unannotated.wav",
                "sha256": "f" * 64,
            }
        )
        payload["sources"].append(second_source)

        notes = LessonNotesProjector.project(EvidenceGraph(payload))
        timeline = TimelineViewModel.from_lesson_notes(notes)
        source_lane = next(
            lane for lane in timeline.lanes if lane.kind is TimelineLaneKind.SOURCE
        )
        unannotated = next(
            item
            for item in source_lane.items
            if item.item_id == "source:source:unannotated:unannotated"
        )

        self.assertEqual(2, len(notes.sources))
        self.assertEqual(2, len(source_lane.items))
        self.assertEqual("unannotated", unannotated.review_status)
        self.assertIsNone(unannotated.playback)


if __name__ == "__main__":
    unittest.main()
