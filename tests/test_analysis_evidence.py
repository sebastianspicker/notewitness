from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from notewitness.application.analysis_evidence import (
    AnalysisEvidenceContext,
    AnalysisEvidenceError,
    append_analysis_batches,
)
from notewitness.domain.analysis import (
    ActivityHypothesis,
    AlignmentOutcome,
    AnalysisBatch,
    AnalysisResult,
    AnalysisStage,
    AnalysisState,
    InstrumentHypothesis,
    NoteHypothesis,
    PitchPointHypothesis,
    ScoreAlignmentHypothesis,
    SpeakerSegmentHypothesis,
)
from notewitness.domain.lesson import ActivityKind
from notewitness.domain.timeline import MediaSpan
from notewitness.evidence import EvidenceGraph


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "synthetic_lesson" / "project.json"
SOURCE_ID = "source:synthetic-script"
GENERATOR_ID = "generator:analysis-test"


def context() -> AnalysisEvidenceContext:
    return AnalysisEvidenceContext(
        run_token="test-run",
        generator_id=GENERATOR_ID,
        generator_name="Local analysis test adapter",
        generator_version="1",
        model_name="fixture-model",
        weight_hash_state="sha256:" + "a" * 64,
        raw_artifact_id="artifact:analysis-test",
        raw_artifact_sha256="b" * 64,
        raw_artifact_size_bytes=42,
        parameters={"network_isolated": True},
    )


def batch(stage: AnalysisStage, *hypotheses: object) -> AnalysisBatch:
    return AnalysisBatch(
        result=AnalysisResult(
            stage=stage,
            state=(
                AnalysisState.UNCERTAIN
                if any(
                    getattr(item, "state", None) is AnalysisState.UNCERTAIN
                    for item in hypotheses
                )
                else AnalysisState.READY
            ),
            hypothesis_ids=tuple(getattr(item, "hypothesis_id") for item in hypotheses),
            diagnostics=(),
        ),
        hypotheses=hypotheses,  # type: ignore[arg-type]
    )


class AnalysisEvidenceTests(unittest.TestCase):
    def test_all_required_analysis_types_become_reviewable_graph_events(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        span = MediaSpan(SOURCE_ID, "audio", 1_000_000, 500_000)
        note = NoteHypothesis(
            "note:test",
            span,
            AnalysisState.READY,
            60.2,
            264.7,
            0.82,
            GENERATOR_ID,
        )
        pitch = PitchPointHypothesis(
            "pitch:test",
            span,
            AnalysisState.READY,
            264.7,
            0.76,
            GENERATOR_ID,
        )
        batches = (
            batch(
                AnalysisStage.ACTIVITY_SEGMENTATION,
                ActivityHypothesis(
                    "activity:test",
                    span,
                    AnalysisState.READY,
                    ActivityKind.SPEECH_OVER_MUSIC,
                    0.73,
                    GENERATOR_ID,
                ),
            ),
            batch(
                AnalysisStage.ANONYMOUS_DIARIZATION,
                SpeakerSegmentHypothesis(
                    "speaker:one",
                    span,
                    AnalysisState.READY,
                    "SPEAKER_00",
                    None,
                    0.71,
                    GENERATOR_ID,
                ),
                SpeakerSegmentHypothesis(
                    "speaker:two",
                    MediaSpan(SOURCE_ID, "audio", 1_250_000, 500_000),
                    AnalysisState.READY,
                    "SPEAKER_01",
                    None,
                    0.68,
                    GENERATOR_ID,
                ),
            ),
            batch(AnalysisStage.NOTE_TRANSCRIPTION, note),
            batch(AnalysisStage.CONTINUOUS_PITCH, pitch),
            batch(
                AnalysisStage.INSTRUMENT_DETECTION,
                InstrumentHypothesis(
                    "instrument:test",
                    span,
                    AnalysisState.UNCERTAIN,
                    "violin",
                    None,
                    0.58,
                    GENERATOR_ID,
                ),
            ),
            batch(
                AnalysisStage.SCORE_ALIGNMENT,
                ScoreAlignmentHypothesis(
                    "alignment:test",
                    span,
                    AnalysisState.READY,
                    AlignmentOutcome.ALIGNED,
                    "score:test",
                    {"bar": 18, "beat": 2.5},
                    (note.hypothesis_id, pitch.hypothesis_id),
                    0.79,
                    GENERATOR_ID,
                ),
            ),
        )

        records = append_analysis_batches(
            payload,
            source_id=SOURCE_ID,
            batches=batches,
            context=context(),
        )

        EvidenceGraph(payload).require_valid()
        added_events = [
            item for item in payload["events"] if item["id"] in records.event_ids
        ]
        self.assertEqual(7, len(added_events))
        self.assertTrue(
            all(item["review_status"] == "machine_suggested" for item in added_events)
        )
        self.assertTrue(
            all(item["layer"] == "normalized_hypothesis" for item in added_events)
        )
        self.assertIn("speech_over_music", {item["type"] for item in added_events})
        self.assertIn("local:diarization", {item["type"] for item in added_events})
        aligned_event = next(
            item for item in added_events if item["type"] == "local:score_alignment"
        )
        aligned_target = next(
            item
            for item in payload["targets"]
            if item["id"] == aligned_event["target_ids"][0]
        )
        self.assertEqual("aligned", aligned_target["alignment_state"])
        self.assertEqual("score:test", aligned_target["musical_selector"]["score_id"])

    def test_machine_output_cannot_attribute_people_or_unknown_score_evidence(self) -> None:
        original = json.loads(FIXTURE.read_text(encoding="utf-8"))
        span = MediaSpan(SOURCE_ID, "audio", 0, 100_000)
        attributed = InstrumentHypothesis(
            "instrument:attributed",
            span,
            AnalysisState.READY,
            "violin",
            "actor:student",
            0.9,
            GENERATOR_ID,
        )
        payload = deepcopy(original)
        with self.assertRaisesRegex(AnalysisEvidenceError, "attribute"):
            append_analysis_batches(
                payload,
                source_id=SOURCE_ID,
                batches=(batch(AnalysisStage.INSTRUMENT_DETECTION, attributed),),
                context=context(),
            )
        self.assertEqual(original, payload)

        alignment = ScoreAlignmentHypothesis(
            "alignment:unknown",
            span,
            AnalysisState.READY,
            AlignmentOutcome.ALIGNED,
            "score:test",
            {"bar": 1},
            ("note:not-in-this-run",),
            0.8,
            GENERATOR_ID,
        )
        payload = deepcopy(original)
        with self.assertRaisesRegex(AnalysisEvidenceError, "unknown note or pitch"):
            append_analysis_batches(
                payload,
                source_id=SOURCE_ID,
                batches=(batch(AnalysisStage.SCORE_ALIGNMENT, alignment),),
                context=context(),
            )
        self.assertEqual(original, payload)

    def test_instrument_diarization_keeps_same_label_tracks_and_note_links_distinct(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        first_span = MediaSpan(SOURCE_ID, "audio", 1_000_000, 700_000)
        second_span = MediaSpan(SOURCE_ID, "audio", 1_250_000, 700_000)
        instruments = batch(
            AnalysisStage.INSTRUMENT_DIARIZATION,
            InstrumentHypothesis(
                "instrument:piano-one",
                first_span,
                AnalysisState.READY,
                "piano",
                None,
                0.81,
                GENERATOR_ID,
                "instrument-track-01",
            ),
            InstrumentHypothesis(
                "instrument:piano-two",
                second_span,
                AnalysisState.READY,
                "piano",
                None,
                0.77,
                GENERATOR_ID,
                "instrument-track-02",
            ),
        )
        notes = batch(
            AnalysisStage.NOTE_TRANSCRIPTION,
            NoteHypothesis(
                "note:piano-one",
                first_span,
                AnalysisState.READY,
                60.0,
                None,
                None,
                GENERATOR_ID,
                "instrument-track-01",
                0.72,
                None,
                (-0.1, 0.2),
                "semitones",
            ),
            NoteHypothesis(
                "note:piano-two",
                second_span,
                AnalysisState.READY,
                67.0,
                None,
                None,
                GENERATOR_ID,
                "instrument-track-02",
            ),
        )

        records = append_analysis_batches(
            payload,
            source_id=SOURCE_ID,
            batches=(instruments, notes),
            context=context(),
        )

        EvidenceGraph(payload).require_valid()
        projected = {
            item["body"]["hypothesis_id"]: item["body"]["value"]
            for item in payload["events"]
            if item["id"] in records.event_ids
        }
        self.assertEqual(
            "instrument-track-01",
            projected["instrument:piano-one"][
                "anonymous_instrument_track_id"
            ],
        )
        self.assertEqual(
            "instrument-track-02",
            projected["instrument:piano-two"][
                "anonymous_instrument_track_id"
            ],
        )
        self.assertEqual(
            "instrument-track-01",
            projected["note:piano-one"]["source_track_id"],
        )
        self.assertEqual(
            [-0.1, 0.2],
            projected["note:piano-one"]["pitch_bend_values"],
        )

if __name__ == "__main__":
    unittest.main()
