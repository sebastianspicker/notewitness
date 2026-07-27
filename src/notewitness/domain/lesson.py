"""Lesson-recall records projected from the canonical evidence graph."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Mapping

from notewitness.domain.timeline import EvidenceAnchor


LESSON_NOTES_SCHEMA_VERSION = "0.1.0"


class ActivityKind(StrEnum):
    SPEECH = "speech"
    MUSIC = "music"
    SUNG_OR_HUMMED = "sung_or_hummed"
    SPEECH_OVER_MUSIC = "speech_over_music"
    SILENCE = "silence"
    OTHER_SOUND = "other_sound"


@dataclass(frozen=True, slots=True)
class SourceReference:
    source_id: str
    kind: str
    uri: str
    sha256: str
    rights_id: str


@dataclass(frozen=True, slots=True)
class ActivitySegment:
    event_id: str
    kind: ActivityKind
    actor_id: str
    actor_role: str
    anchors: tuple[EvidenceAnchor, ...]
    review_status: str
    layer: str
    generator_id: str
    rights_id: str
    confidence: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class TranscriptTurn:
    event_id: str
    actor_id: str
    speaker_role: str
    text: str
    anchors: tuple[EvidenceAnchor, ...]
    review_status: str
    layer: str
    generator_id: str
    rights_id: str
    confidence: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class FullTranscriptEntry:
    """One chronological speech, music, note, pitch, silence, or overlap entry."""

    event_id: str
    content_kind: str
    actor_id: str
    actor_role: str
    display_text: str
    body_format: str
    body_value: Any
    alternatives: tuple[Any, ...]
    anchors: tuple[EvidenceAnchor, ...]
    review_status: str
    layer: str
    generator_id: str
    rights_id: str
    confidence: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PlaybackBookmark:
    bookmark_id: str
    event_id: str
    label: str
    activity_kind: ActivityKind
    anchor: EvidenceAnchor


@dataclass(frozen=True, slots=True)
class EvidenceTopic:
    label: str
    target_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceExcerpt:
    event_id: str
    text: str
    actor_role: str
    anchors: tuple[EvidenceAnchor, ...]
    review_status: str
    layer: str
    generator_id: str
    rights_id: str
    confidence: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PedagogicalMoment:
    relation_id: str
    relation_type: str
    label: str
    event_ids: tuple[str, ...]
    target_ids: tuple[str, ...]
    anchors: tuple[EvidenceAnchor, ...]
    review_status: str
    layer: str
    generator_id: str
    annotator_id: str
    rights_id: str
    confidence: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class LessonSummary:
    """Evidence-backed summary; never an untraceable generated narrative."""

    method: str
    overview: str
    topics: tuple[EvidenceTopic, ...]
    feedback: tuple[EvidenceExcerpt, ...]
    key_moments: tuple[PedagogicalMoment, ...]


@dataclass(frozen=True, slots=True)
class PracticeTask:
    task_id: str
    text: str
    source_event_ids: tuple[str, ...]
    source_relation_ids: tuple[str, ...]
    anchors: tuple[EvidenceAnchor, ...]
    review_status: str
    layer: str
    generator_ids: tuple[str, ...]
    rights_ids: tuple[str, ...]
    completed: bool = False


@dataclass(frozen=True, slots=True)
class PracticePlan:
    method: str
    title: str
    tasks: tuple[PracticeTask, ...]
    requires_human_confirmation: bool = True


@dataclass(frozen=True, slots=True)
class NamedCount:
    name: str
    count: int


@dataclass(frozen=True, slots=True)
class ActivityStatistic:
    kind: ActivityKind
    event_count: int
    duration_us: int


@dataclass(frozen=True, slots=True)
class TimelineExtent:
    source_id: str
    stream_id: str
    start_us: int
    duration_us: int

    def __post_init__(self) -> None:
        if not self.source_id or not self.stream_id:
            raise ValueError("Timeline extents require source and stream IDs.")
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in (self.start_us, self.duration_us)
        ):
            raise ValueError("Timeline extents use non-negative integer microseconds.")

    @property
    def end_us(self) -> int:
        return self.start_us + self.duration_us


@dataclass(frozen=True, slots=True)
class LessonStatistics:
    """Descriptive workflow facts only; never a learner or teacher score."""

    timeline_duration_us: int
    transcript_turn_count: int
    bookmark_count: int
    practice_task_count: int
    note_or_pitch_event_count: int
    unknown_actor_event_count: int
    activity: tuple[ActivityStatistic, ...]
    relations: tuple[NamedCount, ...]
    timeline_extents: tuple[TimelineExtent, ...]
    assessment_free: bool = True


@dataclass(frozen=True, slots=True)
class LessonProgress:
    """In-lesson comparison plus local-history hooks for later lessons."""

    revised_attempt_relation_ids: tuple[str, ...]
    previous_lesson_id: str | None = None
    next_lesson_id: str | None = None


@dataclass(frozen=True, slots=True)
class RecordRevisionLink:
    record_id: str
    revision_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceGraphProvenance:
    schema_version: str
    canonical_sha256: str
    generator_ids: tuple[str, ...]
    rights_ids: tuple[str, ...]
    revisions: tuple[RecordRevisionLink, ...]


@dataclass(frozen=True, slots=True)
class LessonNotes:
    """Private local lesson-notes artifact with source-level provenance."""

    project_id: str
    title: str
    network_mode: str
    source_graph: SourceGraphProvenance
    sources: tuple[SourceReference, ...]
    activity: tuple[ActivitySegment, ...]
    transcript: tuple[TranscriptTurn, ...]
    full_transcript: tuple[FullTranscriptEntry, ...]
    transcript_suggestions: tuple[FullTranscriptEntry, ...]
    summary: LessonSummary
    relation_suggestions: tuple[PedagogicalMoment, ...]
    practice_tasks: tuple[PracticeTask, ...]
    practice_plan: PracticePlan
    bookmarks: tuple[PlaybackBookmark, ...]
    progress: LessonProgress
    statistics: LessonStatistics
    limitations: tuple[str, ...]
    schema_version: str = LESSON_NOTES_SCHEMA_VERSION
    artifact_generated_locally: bool = True
    contains_remote_derived_evidence: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible snapshot without dropping provenance."""

        return asdict(self)


def text_body(event: Mapping[str, Any]) -> str:
    body = event.get("body")
    if not isinstance(body, Mapping):
        return ""
    value = body.get("value")
    return value if isinstance(value, str) else ""
