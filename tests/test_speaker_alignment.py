from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from notewitness.application.lesson_notes import LessonNotesProjector
from notewitness.application.speaker_alignment import (
    _event_spans,
    align_speech_to_anonymous_speakers,
)
from notewitness.evidence import EvidenceGraph
from notewitness.project import initialize_project
from notewitness.project_store import ProjectStore


class SpeakerAlignmentTests(unittest.TestCase):
    def test_event_spans_accepts_only_valid_anchors_in_event_order(self) -> None:
        targets = {
            "target:later": {"source_id": "source:lesson", "selector": {"stream_id": "audio", "start_us": 5, "duration_us": 1}},
            "target:not-mapping": "not a target",
            "target:no-selector": {"source_id": "source:lesson", "selector": None},
            "target:non-string-source": {"source_id": 1, "selector": {"stream_id": "audio", "start_us": 0, "duration_us": 1}},
            "target:non-string-stream": {"source_id": "source:lesson", "selector": {"stream_id": 1, "start_us": 0, "duration_us": 1}},
            "target:bool-start": {"source_id": "source:lesson", "selector": {"stream_id": "audio", "start_us": True, "duration_us": 1}},
            "target:bool-duration": {"source_id": "source:lesson", "selector": {"stream_id": "audio", "start_us": 0, "duration_us": True}},
            "target:negative-start": {"source_id": "source:lesson", "selector": {"stream_id": "audio", "start_us": -1, "duration_us": 1}},
            "target:zero-duration": {"source_id": "source:lesson", "selector": {"stream_id": "audio", "start_us": 0, "duration_us": 0}},
            "target:first": {"source_id": "source:lesson", "selector": {"stream_id": "audio", "start_us": 0, "duration_us": 1}},
        }

        spans = _event_spans(
            {"target_ids": tuple(targets)},
            targets,  # type: ignore[arg-type]
        )

        self.assertEqual(
            (("source:lesson", "audio", 5, 6), ("source:lesson", "audio", 0, 1)),
            spans,
        )

    def test_max_overlap_preserves_ties_and_projects_anonymous_roles(self) -> None:
        with TemporaryDirectory() as temporary:
            project = Path(temporary) / "study"
            initialize_project(project)
            store = ProjectStore(project)
            store.mutate(_add_speech_and_diarization)

            first = align_speech_to_anonymous_speakers(project)
            second = align_speech_to_anonymous_speakers(project)

            self.assertEqual(3, len(first.relation_ids))
            self.assertEqual(first.relation_ids, first.added_relation_ids)
            self.assertEqual((), second.added_relation_ids)
            self.assertEqual(first.project_sha256, second.project_sha256)
            graph = EvidenceGraph(store.load().payload)
            graph.require_valid()
            relations = graph.records("relations")
            self.assertTrue(
                all(item["type"] == "local:speaker_alignment" for item in relations)
            )
            self.assertTrue(
                all(item["review_status"] == "machine_suggested" for item in relations)
            )
            self.assertEqual(
                {"actor:speaker-alignment-unknown"},
                {str(item["annotator_id"]) for item in relations},
            )
            notes = LessonNotesProjector.project(graph)
            speech = {
                item.event_id: item.actor_role
                for item in notes.transcript_suggestions
                if item.content_kind == "speech"
            }
            self.assertEqual("anonymous speaker speaker-01", speech["event:speech-one"])
            self.assertEqual(
                "anonymous speakers speaker-01 / speaker-02",
                speech["event:speech-tie"],
            )

    def test_new_diarization_run_does_not_accumulate_old_speaker_clusters(self) -> None:
        with TemporaryDirectory() as temporary:
            project = Path(temporary) / "study"
            initialize_project(project)
            store = ProjectStore(project)
            store.mutate(_add_speech_and_diarization)
            first = align_speech_to_anonymous_speakers(project)
            store.mutate(_add_diarization_rerun)

            second = align_speech_to_anonymous_speakers(project)
            repeated = align_speech_to_anonymous_speakers(project)

            self.assertEqual(3, len(first.added_relation_ids))
            self.assertEqual(3, len(second.added_relation_ids))
            self.assertEqual(second.relation_ids, repeated.relation_ids)
            self.assertEqual((), repeated.added_relation_ids)
            notes = LessonNotesProjector.project(EvidenceGraph(store.load().payload))
            speech = {
                item.event_id: item.actor_role
                for item in notes.transcript_suggestions
                if item.content_kind == "speech"
            }
            self.assertEqual(
                "anonymous speaker speaker-99",
                speech["event:speech-one"],
            )
            self.assertEqual(
                "anonymous speakers speaker-98 / speaker-99",
                speech["event:speech-tie"],
            )


def _add_speech_and_diarization(payload: dict[str, object]) -> None:
    payload["rights"].append(  # type: ignore[index,union-attr]
        {
            "id": "rights:lesson",
            "access": "restricted",
            "remote_processing": False,
            "model_training": False,
            "retention": "project",
        }
    )
    payload["sources"].append(  # type: ignore[index,union-attr]
        {
            "id": "source:lesson",
            "kind": "audio",
            "uri": "media/lesson.wav",
            "sha256": "a" * 64,
            "rights_id": "rights:lesson",
        }
    )
    payload["actors"].append(  # type: ignore[index,union-attr]
        {"id": "actor:unknown", "role": "unknown", "visibility": "restricted"}
    )
    payload["generators"].append(  # type: ignore[index,union-attr]
        {
            "id": "generator:fixture-model",
            "kind": "machine",
            "name": "fixture",
            "version": "1",
            "model": "fixture",
            "weight_hash_state": "sha256:" + "b" * 64,
        }
    )
    targets = (
        ("target:speech-one", 0, 300_000),
        ("target:speech-tie", 400_000, 400_000),
        ("target:speaker-one", 0, 600_000),
        ("target:speaker-two", 600_000, 400_000),
    )
    for target_id, start, duration in targets:
        payload["targets"].append(  # type: ignore[index,union-attr]
            {
                "id": target_id,
                "source_id": "source:lesson",
                "selector": {
                    "stream_id": "audio",
                    "start_us": start,
                    "duration_us": duration,
                    "spatial": None,
                },
                "musical_selector": None,
                "alignment_state": "not_applicable",
            }
        )
    for event_id, target_id, text in (
        ("event:speech-one", "target:speech-one", "First phrase"),
        ("event:speech-tie", "target:speech-tie", "Overlapping phrase"),
    ):
        payload["events"].append(  # type: ignore[index,union-attr]
            _event(event_id, "speech", target_id, text)
        )
    for event_id, target_id, cluster in (
        ("event:speaker-one", "target:speaker-one", "speaker-01"),
        ("event:speaker-two", "target:speaker-two", "speaker-02"),
    ):
        payload["events"].append(  # type: ignore[index,union-attr]
            _event(
                event_id,
                "local:diarization",
                target_id,
                {"anonymous_cluster_id": cluster},
                analysis_run_id="analysis:one",
            )
        )


def _add_diarization_rerun(payload: dict[str, object]) -> None:
    for target_id, start, duration in (
        ("target:speaker-rerun-one", 0, 600_000),
        ("target:speaker-rerun-two", 600_000, 400_000),
    ):
        payload["targets"].append(  # type: ignore[index,union-attr]
            {
                "id": target_id,
                "source_id": "source:lesson",
                "selector": {
                    "stream_id": "audio",
                    "start_us": start,
                    "duration_us": duration,
                    "spatial": None,
                },
                "musical_selector": None,
                "alignment_state": "not_applicable",
            }
        )
    for event_id, target_id, cluster in (
        ("event:speaker-rerun-one", "target:speaker-rerun-one", "speaker-99"),
        ("event:speaker-rerun-two", "target:speaker-rerun-two", "speaker-98"),
    ):
        payload["events"].append(  # type: ignore[index,union-attr]
            _event(
                event_id,
                "local:diarization",
                target_id,
                {"anonymous_cluster_id": cluster},
                analysis_run_id="analysis:two",
            )
        )


def _event(
    event_id: str,
    event_type: str,
    target_id: str,
    value: object,
    *,
    analysis_run_id: str | None = None,
) -> dict[str, object]:
    return {
        "id": event_id,
        "type": event_type,
        "scope": "evidence",
        "actor_id": "actor:unknown",
        "target_ids": [target_id],
        "body": {
            "format": "text" if isinstance(value, str) else "fixture+json",
            "value": value,
            **(
                {"analysis_run_id": analysis_run_id}
                if analysis_run_id is not None
                else {}
            ),
        },
        "alternatives": [],
        "generator_id": "generator:fixture-model",
        "rights_id": "rights:lesson",
        "layer": "normalized_hypothesis",
        "confidence": {"kind": "adapter_reported"},
        "review_status": "machine_suggested",
    }


if __name__ == "__main__":
    unittest.main()
