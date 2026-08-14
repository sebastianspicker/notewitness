"""Deterministically link ASR segments to anonymous diarization evidence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

from notewitness.project_store import ProjectStore


class SpeakerAlignmentError(RuntimeError):
    """Speech and diarization evidence cannot be linked without ambiguity loss."""


@dataclass(frozen=True, slots=True)
class SpeakerAlignmentResult:
    relation_ids: tuple[str, ...]
    added_relation_ids: tuple[str, ...]
    project_sha256: str


_GENERATOR = {
    "id": "generator:speaker-alignment-v1",
    "kind": "machine",
    "name": "Deterministic anonymous speaker overlap alignment",
    "version": "1",
    "model": "temporal-overlap",
    "weight_hash_state": "not_applicable:deterministic-v1",
    "parameters": {
        "assignment": "maximum_positive_temporal_overlap",
        "ties": "preserve_all",
        "persistent_identity": False,
        "processing_location": "local",
    },
}
_ANNOTATOR = {
    "id": "actor:speaker-alignment-unknown",
    "role": "unknown",
    "visibility": "restricted",
}


def align_speech_to_anonymous_speakers(
    project_root: str | Path,
) -> SpeakerAlignmentResult:
    """Append idempotent machine-suggested overlap relations.

    Ties are intentionally retained as multiple relations.  The operation
    never writes a human actor identity, voiceprint, or replacement ASR text.
    """

    store = ProjectStore(project_root)
    snapshot = store.load()
    planned = _planned_relations(snapshot.payload)
    if not planned:
        return SpeakerAlignmentResult((), (), snapshot.sha256)
    existing = {
        str(item.get("id")): item
        for item in snapshot.payload["relations"]
        if isinstance(item, Mapping)
    }
    for relation in planned:
        current = existing.get(relation["id"])
        if current is not None and current != relation:
            raise SpeakerAlignmentError(
                f"Existing speaker alignment {relation['id']!r} conflicts."
            )
    missing = tuple(
        relation for relation in planned if relation["id"] not in existing
    )
    if not missing:
        return SpeakerAlignmentResult(
            tuple(relation["id"] for relation in planned),
            (),
            snapshot.sha256,
        )

    def append(payload: dict[str, Any]) -> None:
        _append_or_require_equal(payload["actors"], _ANNOTATOR)
        _append_or_require_equal(payload["generators"], _GENERATOR)
        for relation in planned:
            _append_or_require_equal(payload["relations"], relation)

    updated = store.mutate(append, expected_sha256=snapshot.sha256)
    return SpeakerAlignmentResult(
        tuple(relation["id"] for relation in planned),
        tuple(relation["id"] for relation in missing),
        updated.sha256,
    )


def _planned_relations(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    event_records = tuple(
        item
        for item in payload.get("events", ())
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    )
    events = {
        str(item["id"]): item
        for item in event_records
    }
    event_positions = {
        str(item["id"]): index for index, item in enumerate(event_records)
    }
    targets = {
        str(item["id"]): item
        for item in payload.get("targets", ())
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    speech = tuple(
        event
        for event in events.values()
        if event.get("type") in {"speech", "speech_over_music"}
        and event.get("layer") == "normalized_hypothesis"
        and event.get("review_status") == "machine_suggested"
    )
    diarization = tuple(
        event
        for event in events.values()
        if event.get("type") == "local:diarization"
        and event.get("layer") == "normalized_hypothesis"
        and event.get("review_status") == "machine_suggested"
        and _anonymous_cluster(event) is not None
    )
    planned: list[dict[str, Any]] = []
    for speech_event in sorted(speech, key=lambda item: str(item["id"])):
        speech_spans = _event_spans(speech_event, targets)
        speech_duration = sum(end - start for _, _, start, end in speech_spans)
        if speech_duration <= 0:
            continue
        scored: list[tuple[int, Mapping[str, Any], str, int]] = []
        for speaker_event in diarization:
            overlap = _total_overlap(
                speech_spans,
                _event_spans(speaker_event, targets),
            )
            if overlap > 0:
                scored.append(
                    (
                        overlap,
                        speaker_event,
                        diarization_run_id(speaker_event),
                        event_positions[str(speaker_event["id"])],
                    )
                )
        if not scored:
            continue
        latest_run = max(scored, key=lambda item: item[3])[2]
        current_run = tuple(item for item in scored if item[2] == latest_run)
        best = max(overlap for overlap, _event, _run, _position in current_run)
        for overlap, speaker_event, _run, _position in sorted(
            (item for item in current_run if item[0] == best),
            key=lambda item: str(item[1]["id"]),
        ):
            speech_id = str(speech_event["id"])
            speaker_id = str(speaker_event["id"])
            token = hashlib.sha256(
                f"speaker-alignment-v1\0{speech_id}\0{speaker_id}".encode("utf-8")
            ).hexdigest()[:32]
            planned.append(
                {
                    "id": f"relation:speaker-alignment-{token}",
                    "type": "local:speaker_alignment",
                    "arguments": [
                        {
                            "role": "speech",
                            "ref_kind": "event",
                            "ref_id": speech_id,
                        },
                        {
                            "role": "speaker_segment",
                            "ref_kind": "event",
                            "ref_id": speaker_id,
                        },
                    ],
                    "generator_id": _GENERATOR["id"],
                    "annotator_id": _ANNOTATOR["id"],
                    "rights_id": str(speech_event["rights_id"]),
                    "layer": "normalized_hypothesis",
                    "confidence": {
                        "kind": "temporal_overlap_ratio",
                        "value": min(1.0, overlap / speech_duration),
                    },
                    "review_status": "machine_suggested",
                }
            )
    return tuple(planned)


def diarization_run_id(event: Mapping[str, Any]) -> str:
    """Return the stable run boundary used to keep diarization reruns separate."""

    body = event.get("body")
    if isinstance(body, Mapping):
        explicit = body.get("analysis_run_id")
        if isinstance(explicit, str) and explicit:
            return f"run:{explicit}"
        raw_artifact_id = body.get("raw_artifact_id")
        if isinstance(raw_artifact_id, str) and raw_artifact_id:
            marker = "-anonymous_diarization"
            prefix, found, _suffix = raw_artifact_id.partition(marker)
            return f"artifact:{prefix if found else raw_artifact_id}"
    actor_id = event.get("actor_id")
    if isinstance(actor_id, str) and actor_id:
        return f"actor:{actor_id}"
    generator_id = event.get("generator_id")
    if isinstance(generator_id, str) and generator_id:
        return f"generator:{generator_id}"
    return f"event:{event.get('id', 'unknown')}"


def _anonymous_cluster(event: Mapping[str, Any]) -> str | None:
    body = event.get("body")
    value = body.get("value") if isinstance(body, Mapping) else None
    cluster = value.get("anonymous_cluster_id") if isinstance(value, Mapping) else None
    return cluster if isinstance(cluster, str) and cluster else None


def _event_spans(
    event: Mapping[str, Any],
    targets: Mapping[str, Mapping[str, Any]],
) -> tuple[tuple[str, str, int, int], ...]:
    result: list[tuple[str, str, int, int]] = []
    for target_id in event.get("target_ids", ()):
        target = targets.get(str(target_id))
        span = _valid_event_span(target)
        if span is not None:
            result.append(span)
    return tuple(result)


def _valid_event_span(target: object) -> tuple[str, str, int, int] | None:
    if not isinstance(target, Mapping):
        return None
    selector = target.get("selector")
    if not isinstance(selector, Mapping):
        return None
    source_id = target.get("source_id")
    stream_id = selector.get("stream_id")
    start = selector.get("start_us")
    duration = selector.get("duration_us")
    if not _has_valid_span_values(source_id, stream_id, start, duration):
        return None
    return source_id, stream_id, start, start + duration


def _has_valid_span_values(
    source_id: object,
    stream_id: object,
    start: object,
    duration: object,
) -> bool:
    if not isinstance(source_id, str):
        return False
    if not isinstance(stream_id, str):
        return False
    if not _is_non_boolean_int(start):
        return False
    if not _is_non_boolean_int(duration):
        return False
    return start >= 0 and duration > 0


def _is_non_boolean_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _total_overlap(
    left: Iterable[tuple[str, str, int, int]],
    right: Iterable[tuple[str, str, int, int]],
) -> int:
    return sum(
        max(0, min(left_end, right_end) - max(left_start, right_start))
        for left_source, left_stream, left_start, left_end in left
        for right_source, right_stream, right_start, right_end in right
        if (left_source, left_stream) == (right_source, right_stream)
    )


def _append_or_require_equal(
    collection: list[dict[str, Any]], record: Mapping[str, Any]
) -> None:
    matches = [item for item in collection if item.get("id") == record["id"]]
    expected = dict(record)
    if not matches:
        collection.append(expected)
    elif len(matches) != 1 or matches[0] != expected:
        raise SpeakerAlignmentError(
            f"Existing record {record['id']!r} has incompatible meaning."
        )
