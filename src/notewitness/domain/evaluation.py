"""Benchmark plans that retain failures and condition strata."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MetricFamily(StrEnum):
    ACTIVITY = "activity"
    SPEECH = "speech"
    MUSIC = "music"
    ALIGNMENT = "alignment"
    RELATIONS = "relations"
    WORKFLOW = "workflow"
    INTERCHANGE = "interchange"
    PRIVACY_RUNTIME = "privacy_runtime"


class BenchmarkOutcome(StrEnum):
    MEASURED = "measured"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    NOT_ALIGNABLE = "not_alignable"


@dataclass(frozen=True, slots=True)
class EvaluationStratum:
    instrument_or_voice: str
    language: str
    room_condition: str
    recording_setup: str
    activity_type: str
    overlap_present: bool


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    case_id: str
    participant_partition_id: str
    source_ids: tuple[str, ...]
    stratum: EvaluationStratum
    consent_cleared: bool


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    metric_id: str
    family: MetricFamily
    unit: str
    decision_rule: str
    failure_included: bool = True


@dataclass(frozen=True, slots=True)
class BenchmarkObservation:
    case_id: str
    metric_id: str
    outcome: BenchmarkOutcome
    value: float | None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if self.outcome is BenchmarkOutcome.MEASURED and self.value is None:
            raise ValueError("Measured observations require a value.")
        if self.outcome is not BenchmarkOutcome.MEASURED and not self.failure_reason:
            raise ValueError("Non-measured observations require a failure reason.")

