from __future__ import annotations

from dataclasses import dataclass
import unittest

from notewitness.application.pipeline import (
    CapabilityUnavailable,
    InvalidAdapterResult,
    LocalAnalysisPipeline,
    PipelineStep,
)
from notewitness.domain.analysis import (
    AnalysisBatch,
    AnalysisRequest,
    AnalysisResult,
    AnalysisStage,
    AnalysisState,
    InstrumentHypothesis,
    JobCheckpoint,
    JobState,
    NoteHypothesis,
    MAX_DIAGNOSTIC_CHARS,
    SpeechSegmentHypothesis,
    WordHypothesis,
)
from notewitness.domain.timeline import MediaSpan


@dataclass
class FakeAdapter:
    stage: AnalysisStage
    result: AnalysisResult
    hypotheses: tuple[object, ...] = ()
    name: str = "deterministic-test-adapter"
    version: str = "1"
    generator_id: str = "adapter:test"

    def analyze(self, request: AnalysisRequest) -> AnalysisBatch:
        return AnalysisBatch(
            result=self.result,
            hypotheses=self.hypotheses,  # type: ignore[arg-type]
        )


def request() -> AnalysisRequest:
    return AnalysisRequest(
        job_id="job:test",
        source_id="source:test",
        spans=(MediaSpan("source:test", "audio", 0, 1_000_000),),
    )


class PipelineContractTests(unittest.TestCase):
    def test_missing_local_capability_fails_loudly(self) -> None:
        pipeline = LocalAnalysisPipeline()

        with self.assertRaisesRegex(CapabilityUnavailable, "speech_recognition"):
            pipeline.run((PipelineStep(AnalysisStage.SPEECH_RECOGNITION, request()),))

    def test_non_ready_result_stops_dependent_stages(self) -> None:
        first = FakeAdapter(
            AnalysisStage.ACTIVITY_SEGMENTATION,
            AnalysisResult(
                stage=AnalysisStage.ACTIVITY_SEGMENTATION,
                state=AnalysisState.UNSUPPORTED,
                hypothesis_ids=(),
                diagnostics=("unsupported media profile",),
            ),
        )
        second = FakeAdapter(
            AnalysisStage.SPEECH_RECOGNITION,
            AnalysisResult(
                stage=AnalysisStage.SPEECH_RECOGNITION,
                state=AnalysisState.NOT_DETECTED,
                hypothesis_ids=(),
                diagnostics=(),
            ),
        )
        run = LocalAnalysisPipeline((first, second)).run(
            (
                PipelineStep(first.stage, request()),
                PipelineStep(second.stage, request()),
            )
        )

        self.assertFalse(run.completed)
        self.assertEqual(1, len(run.results))
        self.assertEqual(AnalysisState.UNSUPPORTED, run.results[0].state)

    def test_adapter_cannot_return_a_different_stage(self) -> None:
        adapter = FakeAdapter(
            AnalysisStage.SPEECH_RECOGNITION,
            AnalysisResult(
                stage=AnalysisStage.NOTE_TRANSCRIPTION,
                state=AnalysisState.NOT_DETECTED,
                hypothesis_ids=(),
                diagnostics=(),
            ),
        )

        with self.assertRaises(InvalidAdapterResult):
            LocalAnalysisPipeline((adapter,)).run(
                (PipelineStep(adapter.stage, request()),)
            )

    def test_note_and_instrument_evidence_remain_uncertain_and_reviewable(self) -> None:
        span = MediaSpan("source:test", "audio", 0, 100_000)
        note = NoteHypothesis(
            hypothesis_id="note:1",
            span=span,
            state=AnalysisState.READY,
            midi_pitch=60.2,
            frequency_hz=None,
            confidence=0.7,
            generator_id="generator:test",
        )
        instrument = InstrumentHypothesis(
            hypothesis_id="instrument:1",
            span=span,
            state=AnalysisState.UNCERTAIN,
            instrument_label="violin",
            actor_id=None,
            confidence=0.52,
            generator_id="generator:test",
        )
        unsupported = NoteHypothesis(
            hypothesis_id="note:unsupported",
            span=span,
            state=AnalysisState.UNSUPPORTED,
            midi_pitch=None,
            frequency_hz=None,
            confidence=None,
            generator_id="generator:test",
        )

        self.assertEqual(60.2, note.midi_pitch)
        self.assertIsNone(instrument.actor_id)
        self.assertEqual(AnalysisState.UNSUPPORTED, unsupported.state)
        with self.assertRaises(ValueError):
            NoteHypothesis(
                hypothesis_id="note:invalid",
                span=span,
                state=AnalysisState.READY,
                midi_pitch=None,
                frequency_hz=None,
                confidence=None,
                generator_id="generator:test",
            )

    def test_adapter_diagnostics_and_continuations_are_bounded(self) -> None:
        with self.assertRaises(ValueError):
            AnalysisResult(
                stage=AnalysisStage.SPEECH_RECOGNITION,
                state=AnalysisState.FAILED,
                hypothesis_ids=(),
                diagnostics=("x" * (MAX_DIAGNOSTIC_CHARS + 1),),
            )
        with self.assertRaises(ValueError):
            AnalysisResult(
                stage=AnalysisStage.SPEECH_RECOGNITION,
                state=AnalysisState.READY,
                hypothesis_ids=(),
                diagnostics=(),
                continuation_token="unexpected",
            )

    def test_ready_batch_is_typed_bounded_and_source_scoped(self) -> None:
        word = WordHypothesis(
            hypothesis_id="word:1",
            span=MediaSpan("source:test", "audio", 10, 100),
            state=AnalysisState.READY,
            text="legato",
            language="en",
            anonymous_speaker_cluster="SPEAKER_01",
            confidence=0.8,
            generator_id="adapter:test",
        )
        result = AnalysisResult(
            stage=AnalysisStage.SPEECH_RECOGNITION,
            state=AnalysisState.READY,
            hypothesis_ids=(word.hypothesis_id,),
            diagnostics=(),
        )
        adapter = FakeAdapter(
            AnalysisStage.SPEECH_RECOGNITION, result, hypotheses=(word,)
        )

        run = LocalAnalysisPipeline((adapter,)).run(
            (PipelineStep(adapter.stage, request()),)
        )

        self.assertTrue(run.completed)

        wrong_source = WordHypothesis(
            hypothesis_id="word:other",
            span=MediaSpan("source:other", "audio", 10, 100),
            state=AnalysisState.READY,
            text="legato",
            language="en",
            anonymous_speaker_cluster="SPEAKER_01",
            confidence=0.8,
            generator_id="adapter:test",
        )
        wrong_result = AnalysisResult(
            stage=AnalysisStage.SPEECH_RECOGNITION,
            state=AnalysisState.READY,
            hypothesis_ids=(wrong_source.hypothesis_id,),
            diagnostics=(),
        )
        wrong_adapter = FakeAdapter(
            AnalysisStage.SPEECH_RECOGNITION,
            wrong_result,
            hypotheses=(wrong_source,),
        )
        with self.assertRaisesRegex(InvalidAdapterResult, "different source"):
            LocalAnalysisPipeline((wrong_adapter,)).run(
                (PipelineStep(wrong_adapter.stage, request()),)
            )

    def test_asr_batch_can_preserve_segment_and_word_timing(self) -> None:
        span = MediaSpan("source:test", "audio", 10, 100)
        word = WordHypothesis(
            hypothesis_id="word:one",
            span=span,
            state=AnalysisState.READY,
            text="legato",
            language="en",
            anonymous_speaker_cluster="SPEAKER_01",
            confidence=0.8,
            generator_id="adapter:test",
        )
        segment = SpeechSegmentHypothesis(
            hypothesis_id="segment:one",
            span=span,
            state=AnalysisState.READY,
            text="legato",
            language="en",
            word_hypothesis_ids=(word.hypothesis_id,),
            confidence=0.75,
            generator_id="adapter:test",
        )
        result = AnalysisResult(
            stage=AnalysisStage.SPEECH_RECOGNITION,
            state=AnalysisState.READY,
            hypothesis_ids=(segment.hypothesis_id, word.hypothesis_id),
            diagnostics=(),
        )

        batch = AnalysisBatch(result=result, hypotheses=(segment, word))

        self.assertEqual(("word:one",), segment.word_hypothesis_ids)
        self.assertEqual(2, len(batch.hypotheses))

    def test_job_checkpoint_state_controls_continuation_tokens(self) -> None:
        paused = JobCheckpoint(
            job_id="job:test",
            stage=AnalysisStage.SPEECH_RECOGNITION,
            state=JobState.PAUSED,
            completed_span_count=1,
            continuation_token="resume:one",
        )

        self.assertEqual("resume:one", paused.continuation_token)
        with self.assertRaisesRegex(ValueError, "Paused"):
            JobCheckpoint(
                job_id="job:test",
                stage=AnalysisStage.SPEECH_RECOGNITION,
                state=JobState.PAUSED,
                completed_span_count=1,
                continuation_token=None,
            )
        with self.assertRaisesRegex(ValueError, "Completed"):
            JobCheckpoint(
                job_id="job:test",
                stage=AnalysisStage.SPEECH_RECOGNITION,
                state=JobState.COMPLETED,
                completed_span_count=1,
                continuation_token="stale",
            )


if __name__ == "__main__":
    unittest.main()
