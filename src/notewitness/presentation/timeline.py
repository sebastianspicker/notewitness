"""Accessible synchronized-timeline view models derived from lesson notes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from notewitness.domain.lesson import LessonNotes, TimelineExtent
from notewitness.domain.timeline import EvidenceAnchor


class TimelineLaneKind(StrEnum):
    SOURCE = "source"
    ACTIVITY = "activity"
    TRANSCRIPT = "transcript"
    PERFORMANCE = "performance"
    SCORE = "score"
    EPISODES = "episodes"
    RESEARCH = "research"


@dataclass(frozen=True, slots=True)
class PlaybackIntent:
    source_id: str
    stream_id: str
    start_us: int
    duration_us: int

    @classmethod
    def from_anchor(cls, anchor: EvidenceAnchor) -> "PlaybackIntent":
        return cls(
            source_id=anchor.span.source_id,
            stream_id=anchor.span.stream_id,
            start_us=anchor.span.start_us,
            duration_us=anchor.span.duration_us,
        )


@dataclass(frozen=True, slots=True)
class TimelineItem:
    item_id: str
    label: str
    accessibility_label: str
    review_status: str
    playback: PlaybackIntent | None

    def __post_init__(self) -> None:
        if not self.accessibility_label.strip():
            raise ValueError("Timeline items require a non-empty accessibility label.")


@dataclass(frozen=True, slots=True)
class TimelineLane:
    lane_id: str
    kind: TimelineLaneKind
    label: str
    keyboard_shortcut: str
    items: tuple[TimelineItem, ...]


@dataclass(frozen=True, slots=True)
class TimelineViewModel:
    project_id: str
    title: str
    lanes: tuple[TimelineLane, ...]

    @classmethod
    def from_lesson_notes(cls, notes: LessonNotes) -> "TimelineViewModel":
        source_items = _source_timeline_items(notes)
        activity_items = tuple(
            TimelineItem(
                item_id=f"activity:{segment.event_id}:{anchor.target_id}",
                label=segment.kind.value.replace("_", " "),
                accessibility_label=(
                    f"{segment.kind.value.replace('_', ' ')}, "
                    f"{segment.actor_role}, {segment.review_status}"
                ),
                review_status=segment.review_status,
                playback=PlaybackIntent.from_anchor(anchor),
            )
            for segment in notes.activity
            for anchor in segment.anchors
        )
        transcript_items = tuple(
            TimelineItem(
                item_id=f"full-transcript:{entry.event_id}:{anchor.target_id}",
                label=entry.display_text,
                accessibility_label=(
                    f"{entry.actor_role}, {entry.content_kind}: "
                    f"{entry.display_text}; {entry.review_status}"
                ),
                review_status=entry.review_status,
                playback=PlaybackIntent.from_anchor(anchor),
            )
            for entry in notes.full_transcript
            for anchor in entry.anchors
        )
        performance_items = tuple(
            TimelineItem(
                item_id=f"performance:{entry.event_id}:{anchor.target_id}",
                label=entry.display_text,
                accessibility_label=(
                    f"Performance evidence, {entry.actor_role}, "
                    f"{entry.content_kind.replace('_', ' ')}, {entry.review_status}"
                ),
                review_status=entry.review_status,
                playback=PlaybackIntent.from_anchor(anchor),
            )
            for entry in notes.full_transcript
            if entry.content_kind
            in {
                "instrument",
                "music",
                "note",
                "pitch",
                "sung_or_hummed",
                "speech_over_music",
            }
            for anchor in entry.anchors
        )
        score_items = tuple(
            TimelineItem(
                item_id=f"score:{entry.event_id}:{anchor.target_id}",
                label=entry.display_text,
                accessibility_label=(
                    f"Score-linked evidence, {entry.actor_role}, "
                    f"{entry.display_text}; {entry.review_status}"
                ),
                review_status=entry.review_status,
                playback=PlaybackIntent.from_anchor(anchor),
            )
            for entry in notes.full_transcript
            for anchor in entry.anchors
            if anchor.musical_selector is not None
        )
        episode_items = tuple(
            TimelineItem(
                item_id=f"episode:{moment.relation_id}:{anchor.target_id}",
                label=moment.label,
                accessibility_label=(
                    f"{moment.relation_type.replace('_', ' ')} relation; "
                    f"{moment.review_status}; {moment.label}"
                ),
                review_status=moment.review_status,
                playback=PlaybackIntent.from_anchor(anchor),
            )
            for moment in notes.summary.key_moments
            for anchor in moment.anchors
        )
        research_items = tuple(
            [
                TimelineItem(
                    item_id=f"review-event:{entry.event_id}:{anchor.target_id}",
                    label=entry.display_text,
                    accessibility_label=(
                        f"Review {entry.content_kind}, {entry.actor_role}, "
                        f"{entry.display_text}; {entry.review_status}"
                    ),
                    review_status=entry.review_status,
                    playback=PlaybackIntent.from_anchor(anchor),
                )
                for entry in notes.transcript_suggestions
                for anchor in entry.anchors
            ]
            + [
                TimelineItem(
                    item_id=f"review-relation:{moment.relation_id}:{anchor.target_id}",
                    label=moment.label,
                    accessibility_label=(
                        f"Review {moment.relation_type.replace('_', ' ')} relation; "
                        f"{moment.review_status}; {moment.label}"
                    ),
                    review_status=moment.review_status,
                    playback=PlaybackIntent.from_anchor(anchor),
                )
                for moment in notes.relation_suggestions
                for anchor in moment.anchors
            ]
        )
        lanes = (
            TimelineLane(
                lane_id="lane:source",
                kind=TimelineLaneKind.SOURCE,
                label="Source and playback",
                keyboard_shortcut="1",
                items=source_items,
            ),
            TimelineLane(
                lane_id="lane:activity",
                kind=TimelineLaneKind.ACTIVITY,
                label="Speech and music activity",
                keyboard_shortcut="2",
                items=activity_items,
            ),
            TimelineLane(
                lane_id="lane:transcript",
                kind=TimelineLaneKind.TRANSCRIPT,
                label="Full speech and music transcript",
                keyboard_shortcut="3",
                items=transcript_items,
            ),
            TimelineLane(
                lane_id="lane:performance",
                kind=TimelineLaneKind.PERFORMANCE,
                label="Pitch, notes, beats, and performance evidence",
                keyboard_shortcut="4",
                items=performance_items,
            ),
            TimelineLane(
                lane_id="lane:score",
                kind=TimelineLaneKind.SCORE,
                label="Score and musical time",
                keyboard_shortcut="5",
                items=score_items,
            ),
            TimelineLane(
                lane_id="lane:episodes",
                kind=TimelineLaneKind.EPISODES,
                label="Pedagogical episodes and relations",
                keyboard_shortcut="6",
                items=episode_items,
            ),
            TimelineLane(
                lane_id="lane:research",
                kind=TimelineLaneKind.RESEARCH,
                label="Codes, memos, and interpretations",
                keyboard_shortcut="7",
                items=research_items,
            ),
        )
        return cls(project_id=notes.project_id, title=notes.title, lanes=lanes)


def _source_timeline_items(notes: LessonNotes) -> tuple[TimelineItem, ...]:
    extents_by_source: dict[str, list[TimelineExtent]] = {}
    for extent in notes.statistics.timeline_extents:
        extents_by_source.setdefault(extent.source_id, []).append(extent)
    items: list[TimelineItem] = []
    for source in sorted(notes.sources, key=lambda item: item.source_id):
        extents = sorted(
            extents_by_source.get(source.source_id, []),
            key=lambda item: (item.stream_id, item.start_us),
        )
        if not extents:
            items.append(
                TimelineItem(
                    item_id=f"source:{source.source_id}:unannotated",
                    label=f"{source.kind}: {source.source_id}",
                    accessibility_label=(
                        f"Source {source.source_id}; no targeted evidence extent"
                    ),
                    review_status="unannotated",
                    playback=None,
                )
            )
            continue
        items.extend(
            TimelineItem(
                item_id=f"source:{extent.source_id}:{extent.stream_id}",
                label=f"{source.kind}: {extent.source_id}",
                accessibility_label=(
                    f"Source {extent.source_id}, stream {extent.stream_id}, "
                    f"{extent.duration_us} microseconds"
                ),
                review_status="source",
                playback=PlaybackIntent(
                    source_id=extent.source_id,
                    stream_id=extent.stream_id,
                    start_us=extent.start_us,
                    duration_us=extent.duration_us,
                ),
            )
            for extent in extents
        )
    return tuple(items)
