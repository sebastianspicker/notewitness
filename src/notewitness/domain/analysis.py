"""Typed local-analysis and resumable-job contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import math
from typing import Any, Mapping

from notewitness.domain.lesson import ActivityKind
from notewitness.domain.timeline import MediaSpan


MAX_REQUEST_SPANS = 1_024
MAX_REQUEST_PARAMETERS = 128
MAX_BATCH_HYPOTHESES = 50_000
MAX_DIAGNOSTICS = 64
MAX_DIAGNOSTIC_CHARS = 1_024
MAX_CONTINUATION_TOKEN_CHARS = 4_096


class AnalysisStage(StrEnum):
    MEDIA_PROBE = "media_probe"
    DERIVED_FEATURES = "derived_features"
    ACTIVITY_SEGMENTATION = "activity_segmentation"
    SPEECH_RECOGNITION = "speech_recognition"
    ANONYMOUS_DIARIZATION = "anonymous_diarization"
    NOTE_TRANSCRIPTION = "note_transcription"
    CONTINUOUS_PITCH = "continuous_pitch"
    INSTRUMENT_DETECTION = "instrument_detection"
    INSTRUMENT_DIARIZATION = "instrument_diarization"
    ONSET_BEAT_CHORD = "onset_beat_chord"
    SCORE_ALIGNMENT = "score_alignment"
    PEDAGOGICAL_RELATIONS = "pedagogical_relations"


class AnalysisState(StrEnum):
    READY = "ready"
    UNKNOWN = "unknown"
    NOT_DETECTED = "not_detected"
    NOT_APPLICABLE = "not_applicable"
    NOT_ALIGNABLE = "not_alignable"
    UNSUPPORTED = "unsupported"
    UNCERTAIN = "uncertain"
    INCOMPLETE = "incomplete"
    CANCELLED = "cancelled"
    FAILED = "failed"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class AlignmentOutcome(StrEnum):
    ALIGNED = "aligned"
    UNKNOWN = "unknown"
    NOT_DETECTED = "not_detected"
    NOT_APPLICABLE = "not_applicable"
    NOT_ALIGNABLE = "not_alignable"


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    job_id: str
    source_id: str
    spans: tuple[MediaSpan, ...]
    parameters: Mapping[str, Any] = field(default_factory=dict)
    continuation_token: str | None = None

    def __post_init__(self) -> None:
        if not self.job_id or not self.source_id:
            raise ValueError("Analysis requests require job_id and source_id.")
        if not self.spans:
            raise ValueError("Analysis requests require at least one bounded span.")
        if len(self.spans) > MAX_REQUEST_SPANS:
            raise ValueError(
                f"Analysis requests are limited to {MAX_REQUEST_SPANS} spans."
            )
        if any(span.source_id != self.source_id for span in self.spans):
            raise ValueError("Every analysis span must belong to request source_id.")
        if len(self.parameters) > MAX_REQUEST_PARAMETERS:
            raise ValueError(
                f"Analysis requests are limited to {MAX_REQUEST_PARAMETERS} parameters."
            )
        _validate_continuation_token(self.continuation_token)


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    stage: AnalysisStage
    state: AnalysisState
    hypothesis_ids: tuple[str, ...]
    diagnostics: tuple[str, ...]
    continuation_token: str | None = None

    def __post_init__(self) -> None:
        if self.state is AnalysisState.INCOMPLETE and self.continuation_token is None:
            raise ValueError("An incomplete result requires a continuation token.")
        if self.state is not AnalysisState.INCOMPLETE and self.continuation_token is not None:
            raise ValueError("Only incomplete results may retain a continuation token.")
        _validate_continuation_token(self.continuation_token)
        if len(self.hypothesis_ids) > MAX_BATCH_HYPOTHESES:
            raise ValueError(
                f"Analysis batches are limited to {MAX_BATCH_HYPOTHESES} hypotheses."
            )
        if len(self.hypothesis_ids) != len(set(self.hypothesis_ids)):
            raise ValueError("Analysis result hypothesis IDs must be unique.")
        if len(self.diagnostics) > MAX_DIAGNOSTICS or any(
            not isinstance(item, str) or len(item) > MAX_DIAGNOSTIC_CHARS
            for item in self.diagnostics
        ):
            raise ValueError("Analysis diagnostics exceed their bounded contract.")


@dataclass(frozen=True, slots=True)
class ActivityHypothesis:
    hypothesis_id: str
    span: MediaSpan
    state: AnalysisState
    kind: ActivityKind | None
    confidence: float | None
    generator_id: str

    def __post_init__(self) -> None:
        _validate_hypothesis_identity(self.hypothesis_id, self.generator_id)
        _validate_confidence(self.confidence)
        if self.state is AnalysisState.READY and self.kind is None:
            raise ValueError("Ready activity hypotheses require an activity kind.")


@dataclass(frozen=True, slots=True)
class MediaProbeHypothesis:
    hypothesis_id: str
    span: MediaSpan
    state: AnalysisState
    media_type: str | None
    channel_count: int | None
    sample_rate_hz: int | None
    generator_id: str

    def __post_init__(self) -> None:
        _validate_hypothesis_identity(self.hypothesis_id, self.generator_id)
        if self.state is AnalysisState.READY and not self.media_type:
            raise ValueError("Ready media probes require a media type.")


@dataclass(frozen=True, slots=True)
class DerivedFeatureHypothesis:
    hypothesis_id: str
    span: MediaSpan
    state: AnalysisState
    feature_kind: str | None
    artifact_id: str | None
    generator_id: str

    def __post_init__(self) -> None:
        _validate_hypothesis_identity(self.hypothesis_id, self.generator_id)
        if self.state is AnalysisState.READY and (
            not self.feature_kind or not self.artifact_id
        ):
            raise ValueError("Ready derived features require kind and artifact IDs.")


@dataclass(frozen=True, slots=True)
class WordHypothesis:
    hypothesis_id: str
    span: MediaSpan
    state: AnalysisState
    text: str | None
    language: str | None
    anonymous_speaker_cluster: str | None
    confidence: float | None
    generator_id: str
    alternatives: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_hypothesis_identity(self.hypothesis_id, self.generator_id)
        _validate_confidence(self.confidence)
        if self.state is AnalysisState.READY and not self.text:
            raise ValueError("Ready word hypotheses require text.")


@dataclass(frozen=True, slots=True)
class SpeechSegmentHypothesis:
    """Timed ASR segment retaining ordered word links and segment confidence."""

    hypothesis_id: str
    span: MediaSpan
    state: AnalysisState
    text: str | None
    language: str | None
    word_hypothesis_ids: tuple[str, ...]
    confidence: float | None
    generator_id: str

    def __post_init__(self) -> None:
        _validate_hypothesis_identity(self.hypothesis_id, self.generator_id)
        _validate_confidence(self.confidence)
        if self.state is AnalysisState.READY and not self.text:
            raise ValueError("Ready speech segments require text.")
        if len(self.word_hypothesis_ids) != len(set(self.word_hypothesis_ids)):
            raise ValueError("Speech-segment word IDs must be unique.")


@dataclass(frozen=True, slots=True)
class SpeakerSegmentHypothesis:
    """Project-local diarization; a cluster is not a persistent identity."""

    hypothesis_id: str
    span: MediaSpan
    state: AnalysisState
    anonymous_cluster_id: str | None
    confirmed_actor_id: str | None
    confidence: float | None
    generator_id: str

    def __post_init__(self) -> None:
        _validate_hypothesis_identity(self.hypothesis_id, self.generator_id)
        _validate_confidence(self.confidence)
        if self.state is AnalysisState.READY and not self.anonymous_cluster_id:
            raise ValueError("Ready diarization spans require an anonymous cluster ID.")


@dataclass(frozen=True, slots=True)
class NoteHypothesis:
    """Continuous-time note evidence, separate from quantized notation."""

    hypothesis_id: str
    span: MediaSpan
    state: AnalysisState
    midi_pitch: float | None
    frequency_hz: float | None
    confidence: float | None
    generator_id: str
    source_track_id: str | None = None
    amplitude: float | None = None
    velocity: int | None = None
    pitch_bend_values: tuple[float, ...] = ()
    pitch_bend_unit: str | None = None

    def __post_init__(self) -> None:
        _validate_hypothesis_identity(self.hypothesis_id, self.generator_id)
        if self.state is AnalysisState.READY and (
            self.midi_pitch is None and self.frequency_hz is None
        ):
            raise ValueError("Ready note hypotheses require pitch evidence.")
        if self.midi_pitch is not None and not _finite_number_in_range(
            self.midi_pitch, 0.0, 127.0
        ):
            raise ValueError("midi_pitch must be finite and in the range [0, 127].")
        if self.frequency_hz is not None and not _positive_finite_number(
            self.frequency_hz
        ):
            raise ValueError("frequency_hz must be a positive finite number.")
        _validate_confidence(self.confidence)
        _validate_optional_local_track_id(self.source_track_id, "source_track_id")
        if self.amplitude is not None and not _finite_number_in_range(
            self.amplitude, 0.0, 1.0
        ):
            raise ValueError("amplitude must be finite and in the range [0, 1].")
        if self.velocity is not None and (
            not isinstance(self.velocity, int)
            or isinstance(self.velocity, bool)
            or not 0 <= self.velocity <= 127
        ):
            raise ValueError("velocity must be an integer in the range [0, 127].")
        if (
            not isinstance(self.pitch_bend_values, tuple)
            or len(self.pitch_bend_values) > 50_000
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in self.pitch_bend_values
            )
        ):
            raise ValueError("pitch_bend_values must be bounded finite numbers.")
        if self.pitch_bend_unit is not None:
            _validate_optional_local_track_id(
                self.pitch_bend_unit, "pitch_bend_unit"
            )
        if self.pitch_bend_values and self.pitch_bend_unit is None:
            raise ValueError("Pitch-bend values require an explicit unit.")


@dataclass(frozen=True, slots=True)
class PitchPointHypothesis:
    hypothesis_id: str
    span: MediaSpan
    state: AnalysisState
    frequency_hz: float | None
    confidence: float | None
    generator_id: str

    def __post_init__(self) -> None:
        _validate_hypothesis_identity(self.hypothesis_id, self.generator_id)
        _validate_confidence(self.confidence)
        if self.state is AnalysisState.READY and not _positive_finite_number(
            self.frequency_hz
        ):
            raise ValueError("Ready pitch points require a positive frequency.")
        if self.frequency_hz is not None and not _positive_finite_number(
            self.frequency_hz
        ):
            raise ValueError("frequency_hz must be a positive finite number.")


@dataclass(frozen=True, slots=True)
class InstrumentHypothesis:
    """Reviewable instrument evidence with optional run-local track identity."""

    hypothesis_id: str
    span: MediaSpan
    state: AnalysisState
    instrument_label: str | None
    actor_id: str | None
    confidence: float | None
    generator_id: str
    anonymous_instrument_track_id: str | None = None

    def __post_init__(self) -> None:
        _validate_hypothesis_identity(self.hypothesis_id, self.generator_id)
        if self.state is AnalysisState.READY and not self.instrument_label:
            raise ValueError("Ready instrument hypotheses require an instrument label.")
        _validate_optional_local_track_id(
            self.anonymous_instrument_track_id,
            "anonymous_instrument_track_id",
        )
        _validate_confidence(self.confidence)


@dataclass(frozen=True, slots=True)
class RhythmHarmonyHypothesis:
    hypothesis_id: str
    span: MediaSpan
    state: AnalysisState
    feature_kind: str | None
    value: Mapping[str, Any] | None
    confidence: float | None
    generator_id: str

    def __post_init__(self) -> None:
        _validate_hypothesis_identity(self.hypothesis_id, self.generator_id)
        _validate_confidence(self.confidence)
        if self.state is AnalysisState.READY and (
            self.feature_kind not in {"onset", "beat", "downbeat", "chord"}
            or self.value is None
        ):
            raise ValueError("Ready rhythm/harmony evidence requires a typed value.")


@dataclass(frozen=True, slots=True)
class ScoreAlignmentHypothesis:
    hypothesis_id: str
    span: MediaSpan
    state: AnalysisState
    outcome: AlignmentOutcome
    score_id: str | None
    score_position: Mapping[str, Any] | None
    source_hypothesis_ids: tuple[str, ...]
    confidence: float | None
    generator_id: str

    def __post_init__(self) -> None:
        _validate_hypothesis_identity(self.hypothesis_id, self.generator_id)
        _validate_confidence(self.confidence)
        if self.outcome is AlignmentOutcome.ALIGNED and (
            not self.score_id or self.score_position is None
        ):
            raise ValueError("Aligned hypotheses require a score and score position.")


@dataclass(frozen=True, slots=True)
class PedagogicalRelationHypothesis:
    hypothesis_id: str
    span: MediaSpan
    state: AnalysisState
    relation_type: str | None
    argument_hypothesis_ids: tuple[str, ...]
    confidence: float | None
    generator_id: str

    def __post_init__(self) -> None:
        _validate_hypothesis_identity(self.hypothesis_id, self.generator_id)
        _validate_confidence(self.confidence)
        if self.state is AnalysisState.READY and (
            not self.relation_type or len(self.argument_hypothesis_ids) < 2
        ):
            raise ValueError("Ready relation suggestions require type and arguments.")


AnalysisHypothesis = (
    MediaProbeHypothesis
    | DerivedFeatureHypothesis
    | ActivityHypothesis
    | WordHypothesis
    | SpeechSegmentHypothesis
    | SpeakerSegmentHypothesis
    | NoteHypothesis
    | PitchPointHypothesis
    | InstrumentHypothesis
    | RhythmHarmonyHypothesis
    | ScoreAlignmentHypothesis
    | PedagogicalRelationHypothesis
)


@dataclass(frozen=True, slots=True)
class AnalysisBatch:
    result: AnalysisResult
    hypotheses: tuple[AnalysisHypothesis, ...]

    def __post_init__(self) -> None:
        actual_ids = tuple(item.hypothesis_id for item in self.hypotheses)
        if actual_ids != self.result.hypothesis_ids:
            raise ValueError(
                "Analysis result hypothesis IDs must match the typed batch in order."
            )
        expected_types: dict[AnalysisStage, type[Any] | tuple[type[Any], ...]] = {
            AnalysisStage.MEDIA_PROBE: MediaProbeHypothesis,
            AnalysisStage.DERIVED_FEATURES: DerivedFeatureHypothesis,
            AnalysisStage.ACTIVITY_SEGMENTATION: ActivityHypothesis,
            AnalysisStage.SPEECH_RECOGNITION: (
                WordHypothesis,
                SpeechSegmentHypothesis,
            ),
            AnalysisStage.ANONYMOUS_DIARIZATION: SpeakerSegmentHypothesis,
            AnalysisStage.NOTE_TRANSCRIPTION: NoteHypothesis,
            AnalysisStage.CONTINUOUS_PITCH: PitchPointHypothesis,
            AnalysisStage.INSTRUMENT_DETECTION: InstrumentHypothesis,
            AnalysisStage.INSTRUMENT_DIARIZATION: InstrumentHypothesis,
            AnalysisStage.ONSET_BEAT_CHORD: RhythmHarmonyHypothesis,
            AnalysisStage.SCORE_ALIGNMENT: ScoreAlignmentHypothesis,
            AnalysisStage.PEDAGOGICAL_RELATIONS: PedagogicalRelationHypothesis,
        }
        expected_type = expected_types.get(self.result.stage)
        if expected_type is not None and any(
            not isinstance(item, expected_type) for item in self.hypotheses
        ):
            raise ValueError(
                f"Stage {self.result.stage.value!r} received an incompatible "
                "hypothesis type."
            )
        if self.result.stage is AnalysisStage.INSTRUMENT_DIARIZATION and any(
            isinstance(item, InstrumentHypothesis)
            and item.state is AnalysisState.READY
            and item.anonymous_instrument_track_id is None
            for item in self.hypotheses
        ):
            raise ValueError(
                "Ready instrument-diarization hypotheses require anonymous track IDs."
            )
        if self.result.state is AnalysisState.READY:
            if not self.hypotheses:
                raise ValueError("Ready analysis batches require typed hypotheses.")
            if any(item.state is not AnalysisState.READY for item in self.hypotheses):
                raise ValueError("Ready batches may contain only ready hypotheses.")
        elif self.result.state in {AnalysisState.UNCERTAIN, AnalysisState.INCOMPLETE}:
            if not self.hypotheses:
                raise ValueError(
                    f"{self.result.state.value} batches require typed hypotheses."
                )
            if (
                self.result.state is AnalysisState.UNCERTAIN
                and not any(
                    item.state in {AnalysisState.UNCERTAIN, AnalysisState.UNKNOWN}
                    for item in self.hypotheses
                )
            ):
                raise ValueError(
                    "Uncertain batches require at least one uncertain hypothesis."
                )
        elif self.hypotheses:
            raise ValueError(
                f"{self.result.state.value} batches cannot contain hypotheses."
            )


@dataclass(frozen=True, slots=True)
class JobCheckpoint:
    job_id: str
    stage: AnalysisStage
    state: JobState
    completed_span_count: int
    continuation_token: str | None

    def __post_init__(self) -> None:
        if not self.job_id:
            raise ValueError("job_id must not be empty.")
        if not isinstance(self.stage, AnalysisStage) or not isinstance(
            self.state, JobState
        ):
            raise ValueError("Job checkpoints require typed stage and state values.")
        if (
            not isinstance(self.completed_span_count, int)
            or isinstance(self.completed_span_count, bool)
            or self.completed_span_count < 0
        ):
            raise ValueError("completed_span_count must be a non-negative integer.")
        _validate_continuation_token(self.continuation_token)
        if self.state is JobState.QUEUED and (
            self.completed_span_count or self.continuation_token is not None
        ):
            raise ValueError("Queued checkpoints cannot contain completed work.")
        if self.state is JobState.PAUSED and self.continuation_token is None:
            raise ValueError("Paused checkpoints require a continuation token.")
        if self.state is JobState.COMPLETED and self.continuation_token is not None:
            raise ValueError("Completed checkpoints cannot retain continuation tokens.")


def _validate_hypothesis_identity(hypothesis_id: str, generator_id: str) -> None:
    if not hypothesis_id or not generator_id:
        raise ValueError("Hypotheses require IDs and generator provenance.")


def _validate_confidence(confidence: float | None) -> None:
    if confidence is None:
        return
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence)
        or not 0.0 <= confidence <= 1.0
    ):
        raise ValueError("confidence must be finite and in the closed interval [0, 1].")


def _validate_optional_local_track_id(value: str | None, label: str) -> None:
    if value is None:
        return
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{label} must be a bounded run-local identifier.")


def _validate_continuation_token(token: str | None) -> None:
    if token is not None and (
        not isinstance(token, str)
        or not token
        or len(token) > MAX_CONTINUATION_TOKEN_CHARS
    ):
        raise ValueError("continuation_token exceeds its bounded contract.")


def _positive_finite_number(value: float | None) -> bool:
    return bool(
        value is not None
        and not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value > 0
    )


def _finite_number_in_range(value: float, minimum: float, maximum: float) -> bool:
    return bool(
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and minimum <= value <= maximum
    )
