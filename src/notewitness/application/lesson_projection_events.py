"""Project graph events into chronological lesson-recall records."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from notewitness.domain.lesson import (
    ActivityKind,
    ActivitySegment,
    ActivityStatistic,
    FullTranscriptEntry,
    PlaybackBookmark,
    TimelineExtent,
    TranscriptTurn,
)
from notewitness.domain.timeline import EvidenceAnchor, MediaSpan


_ACTIVITY_KINDS = {
    "speech": ActivityKind.SPEECH,
    "music": ActivityKind.MUSIC,
    "sung_or_hummed": ActivityKind.SUNG_OR_HUMMED,
    "speech_over_music": ActivityKind.SPEECH_OVER_MUSIC,
    "silence": ActivityKind.SILENCE,
}
_TRANSCRIPT_EVENT_TYPES = frozenset({"speech", "speech_over_music"})
_NOTE_BODY_FORMATS = frozenset(
    {"notewitness.note.v1", "application/vnd.notewitness.note+json"}
)
_PITCH_BODY_FORMATS = frozenset(
    {"notewitness.pitch.v1", "application/vnd.notewitness.pitch+json"}
)


@dataclass(frozen=True, slots=True)
class EventProjection:
    anchors_by_event: Mapping[str, tuple[EvidenceAnchor, ...]]
    activity: tuple[ActivitySegment, ...]
    transcript: tuple[TranscriptTurn, ...]
    full_transcript: tuple[FullTranscriptEntry, ...]
    transcript_suggestions: tuple[FullTranscriptEntry, ...]
    bookmarks: tuple[PlaybackBookmark, ...]
    activity_statistics: tuple[ActivityStatistic, ...]
    timeline_extents: tuple[TimelineExtent, ...]
    timeline_duration_us: int


def project_events(
    events: Mapping[str, Mapping[str, Any]],
    targets: Mapping[str, Mapping[str, Any]],
    actors: Mapping[str, Mapping[str, Any]],
    *,
    speaker_roles_by_event: Mapping[str, str] | None = None,
) -> EventProjection:
    projected_speaker_roles = speaker_roles_by_event or {}
    anchors_by_event = {
        event_id: event_anchors(event, targets)
        for event_id, event in events.items()
    }
    ordered_events = sorted(
        events.values(),
        key=lambda event: event_sort_key(str(event["id"]), anchors_by_event),
    )
    accepted_suggestion_ids = {
        str(body["source_suggestion_id"])
        for event in events.values()
        if is_accepted_record(event)
        and isinstance((body := event.get("body")), Mapping)
        and isinstance(body.get("source_suggestion_id"), str)
    }
    superseded_annotation_ids = {
        str(body["source_annotation_id"])
        for event in events.values()
        if is_accepted_record(event)
        and isinstance((body := event.get("body")), Mapping)
        and isinstance(body.get("source_annotation_id"), str)
    }

    activity: list[ActivitySegment] = []
    transcript: list[TranscriptTurn] = []
    full_transcript: list[FullTranscriptEntry] = []
    transcript_suggestions: list[FullTranscriptEntry] = []
    bookmarks: list[PlaybackBookmark] = []
    for event in ordered_events:
        event_id = str(event["id"])
        if event_id in superseded_annotation_ids:
            continue
        anchors = anchors_by_event[event_id]
        if not anchors:
            continue
        actor_id = str(event["actor_id"])
        actor_role = str(actors.get(actor_id, {}).get("role", "unknown"))
        if actor_role == "unknown" and event_id in projected_speaker_roles:
            actor_role = projected_speaker_roles[event_id]
        event_type = str(event["type"])
        kind = _ACTIVITY_KINDS.get(event_type, ActivityKind.OTHER_SOUND)
        body = event.get("body")
        body_format = (
            str(body.get("format", "unknown"))
            if isinstance(body, Mapping)
            else "unknown"
        )
        body_value = body.get("value") if isinstance(body, Mapping) else None
        confidence = event.get("confidence")
        confidence_value = dict(confidence) if isinstance(confidence, Mapping) else {}
        label = event_display_text(event_type, body)
        if event_type == "local:bookmark":
            if is_accepted_record(event):
                bookmarks.extend(
                    PlaybackBookmark(
                        bookmark_id=f"bookmark:{event_id}:{anchor.target_id}",
                        event_id=event_id,
                        label=label,
                        activity_kind=kind,
                        anchor=anchor,
                    )
                    for anchor in anchors
                )
            continue
        full_entry = FullTranscriptEntry(
            event_id=event_id,
            content_kind=full_transcript_kind(event_type, body_format),
            actor_id=actor_id,
            actor_role=actor_role,
            display_text=label,
            body_format=body_format,
            body_value=body_value,
            alternatives=tuple(event.get("alternatives", [])),
            anchors=anchors,
            review_status=str(event["review_status"]),
            layer=str(event["layer"]),
            generator_id=str(event["generator_id"]),
            rights_id=str(event["rights_id"]),
            confidence=confidence_value,
        )
        if not is_accepted_record(event):
            if event_id not in accepted_suggestion_ids:
                transcript_suggestions.append(full_entry)
            continue
        if event_type in _ACTIVITY_KINDS:
            activity.append(
                ActivitySegment(
                    event_id=event_id,
                    kind=kind,
                    actor_id=actor_id,
                    actor_role=actor_role,
                    anchors=anchors,
                    review_status=str(event["review_status"]),
                    layer=str(event["layer"]),
                    generator_id=str(event["generator_id"]),
                    rights_id=str(event["rights_id"]),
                    confidence=confidence_value,
                )
            )
        full_transcript.append(full_entry)
        if event_type in _TRANSCRIPT_EVENT_TYPES:
            transcript.append(
                TranscriptTurn(
                    event_id=event_id,
                    actor_id=actor_id,
                    speaker_role=actor_role,
                    text=label,
                    anchors=anchors,
                    review_status=str(event["review_status"]),
                    layer=str(event["layer"]),
                    generator_id=str(event["generator_id"]),
                    rights_id=str(event["rights_id"]),
                    confidence=confidence_value,
                )
            )
        bookmarks.extend(
            PlaybackBookmark(
                bookmark_id=f"bookmark:{event_id}:{anchor.target_id}",
                event_id=event_id,
                label=label,
                activity_kind=kind,
                anchor=anchor,
            )
            for anchor in anchors
        )

    extents = timeline_extents(
        anchor_from_target(target) for target in targets.values()
    )
    return EventProjection(
        anchors_by_event=anchors_by_event,
        activity=tuple(activity),
        transcript=tuple(transcript),
        full_transcript=tuple(full_transcript),
        transcript_suggestions=tuple(transcript_suggestions),
        bookmarks=tuple(bookmarks),
        activity_statistics=activity_statistics(activity),
        timeline_extents=extents,
        timeline_duration_us=max((extent.end_us for extent in extents), default=0),
    )


def full_transcript_kind(event_type: str, body_format: str) -> str:
    normalized_format = body_format.casefold()
    if event_type == "local:note" or normalized_format in _NOTE_BODY_FORMATS:
        return "note"
    if event_type == "local:pitch" or normalized_format in _PITCH_BODY_FORMATS:
        return "pitch"
    if event_type == "local:instrument":
        return "instrument"
    if event_type == "local:diarization":
        return "speaker_segment"
    if event_type == "local:score_alignment":
        return "score_alignment"
    return _ACTIVITY_KINDS.get(event_type, ActivityKind.OTHER_SOUND).value


def event_display_text(event_type: str, body: object) -> str:
    if not isinstance(body, Mapping):
        return event_type.replace("_", " ")
    value = body.get("value")
    if isinstance(value, str) and value.strip():
        return value
    if not isinstance(value, Mapping):
        return event_type.replace("_", " ")
    renderer = _EVENT_DISPLAY_RENDERERS.get(event_type)
    display = renderer(value) if renderer is not None else None
    return display or event_type.replace("_", " ")


def _note_display_text(value: Mapping[object, object]) -> str:
    parts = []
    midi = value.get("midi_pitch")
    frequency = value.get("frequency_hz")
    track = value.get("source_track_id")
    if isinstance(midi, (int, float)) and not isinstance(midi, bool):
        parts.append(f"MIDI {float(midi):.2f}")
    if isinstance(frequency, (int, float)) and not isinstance(frequency, bool):
        parts.append(f"{float(frequency):.2f} Hz")
    if isinstance(track, str) and track:
        parts.append(f"track {track}")
    return " · ".join(parts) or "Detected note"


def _pitch_display_text(value: Mapping[object, object]) -> str | None:
    frequency = value.get("frequency_hz")
    if isinstance(frequency, (int, float)) and not isinstance(frequency, bool):
        return f"Pitch {float(frequency):.2f} Hz"
    return None


def _instrument_display_text(value: Mapping[object, object]) -> str | None:
    label = value.get("instrument_label")
    if not isinstance(label, str) or not label:
        return None
    track = value.get("anonymous_instrument_track_id")
    suffix = f" · track {track}" if isinstance(track, str) and track else ""
    return f"Instrument: {label}{suffix}"


def _diarization_display_text(value: Mapping[object, object]) -> str | None:
    cluster = value.get("anonymous_cluster_id")
    return f"Anonymous speaker {cluster}" if isinstance(cluster, str) and cluster else None


def _score_alignment_display_text(value: Mapping[object, object]) -> str | None:
    score_id = value.get("score_id")
    position = value.get("score_position")
    if not isinstance(score_id, str) or not isinstance(position, Mapping):
        return None
    location = ", ".join(f"{key} {position[key]}" for key in sorted(position))
    return f"{score_id}: {location}"


_EVENT_DISPLAY_RENDERERS = {
    "local:note": _note_display_text,
    "local:pitch": _pitch_display_text,
    "local:instrument": _instrument_display_text,
    "local:diarization": _diarization_display_text,
    "local:score_alignment": _score_alignment_display_text,
}


def is_accepted_record(record: Mapping[str, Any]) -> bool:
    return bool(
        record.get("layer") == "accepted_annotation"
        and record.get("review_status") in {"human_created", "human_accepted"}
    )


def activity_statistics(
    activity: Iterable[ActivitySegment],
) -> tuple[ActivityStatistic, ...]:
    counts: Counter[ActivityKind] = Counter()
    durations: Counter[ActivityKind] = Counter()
    for segment in activity:
        counts[segment.kind] += 1
        durations[segment.kind] += sum(
            anchor.span.duration_us for anchor in segment.anchors
        )
    return tuple(
        ActivityStatistic(
            kind=kind,
            event_count=counts[kind],
            duration_us=durations[kind],
        )
        for kind in sorted(counts, key=lambda item: item.value)
    )


def timeline_extents(
    anchors: Iterable[EvidenceAnchor],
) -> tuple[TimelineExtent, ...]:
    bounds: dict[tuple[str, str], tuple[int, int]] = {}
    for anchor in anchors:
        key = (anchor.span.source_id, anchor.span.stream_id)
        current = bounds.get(key)
        start_us = min(current[0], anchor.span.start_us) if current else anchor.span.start_us
        end_us = max(current[1], anchor.span.end_us) if current else anchor.span.end_us
        bounds[key] = (start_us, end_us)
    return tuple(
        TimelineExtent(
            source_id=source_id,
            stream_id=stream_id,
            start_us=start_us,
            duration_us=end_us - start_us,
        )
        for (source_id, stream_id), (start_us, end_us) in sorted(bounds.items())
    )


def event_anchors(
    event: Mapping[str, Any], targets: Mapping[str, Mapping[str, Any]]
) -> tuple[EvidenceAnchor, ...]:
    anchors = tuple(
        anchor_from_target(targets[str(target_id)])
        for target_id in event.get("target_ids", [])
        if str(target_id) in targets
    )
    return tuple(
        sorted(
            anchors,
            key=lambda anchor: (
                anchor.span.start_us,
                anchor.span.duration_us,
                anchor.target_id,
            ),
        )
    )


def anchor_from_target(target: Mapping[str, Any]) -> EvidenceAnchor:
    selector = target["selector"]
    if not isinstance(selector, Mapping):
        raise ValueError(f"Validated target {target.get('id')!r} has no selector.")
    musical_selector = target.get("musical_selector")
    return EvidenceAnchor(
        target_id=str(target["id"]),
        span=MediaSpan(
            source_id=str(target["source_id"]),
            stream_id=str(selector.get("stream_id") or "default"),
            start_us=int(selector["start_us"]),
            duration_us=int(selector["duration_us"]),
        ),
        alignment_state=str(target["alignment_state"]),
        musical_selector=(
            dict(musical_selector) if isinstance(musical_selector, Mapping) else None
        ),
    )


def event_sort_key(
    event_id: str, anchors_by_event: Mapping[str, tuple[EvidenceAnchor, ...]]
) -> tuple[int, str]:
    anchors = anchors_by_event[event_id]
    return (anchors[0].span.start_us if anchors else 2**63 - 1, event_id)


def unique_anchors(anchors: Iterable[EvidenceAnchor]) -> tuple[EvidenceAnchor, ...]:
    by_target = {anchor.target_id: anchor for anchor in anchors}
    return tuple(
        sorted(
            by_target.values(),
            key=lambda anchor: (anchor.span.start_us, anchor.target_id),
        )
    )
