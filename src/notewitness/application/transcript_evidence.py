"""Normalize one local transcript document into reviewable graph suggestions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from notewitness.adapters.whisper_cli import WhisperCLIResult
from notewitness.domain.transcription import CanonicalTranscriptEvidence


MAX_GRAPH_TRANSCRIPT_SEGMENTS = 2_000


class TranscriptEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TranscriptEvidenceRecords:
    actor_id: str | None
    generator_id: str
    target_ids: tuple[str, ...]
    event_ids: tuple[str, ...]


def append_machine_transcript(
    payload: dict[str, Any],
    *,
    result: WhisperCLIResult,
    canonical: CanonicalTranscriptEvidence,
) -> TranscriptEvidenceRecords:
    """Append suggestions only; human acceptance remains a separate operation."""

    document = result.document
    if len(document.segments) > MAX_GRAPH_TRANSCRIPT_SEGMENTS:
        raise TranscriptEvidenceError(
            "Transcript exceeds the graph projection bound; retain it as an artifact."
        )
    source = _record(payload, "sources", document.source_id)
    rights_id = source.get("rights_id")
    if not isinstance(rights_id, str):
        raise TranscriptEvidenceError("Transcript source has no valid rights record.")

    generator_id = (
        f"generator:whisper-{result.model.sha256[:10]}-"
        f"{result.runtime_fingerprint_sha256[:10]}"
    )
    generator = {
        "id": generator_id,
        "kind": "machine",
        "name": "Local OpenAI Whisper CLI",
        "version": result.launcher.sha256[:16],
        "model": result.model.path_name,
        "weight_hash_state": f"sha256:{result.model.sha256}",
        "parameters": {
            "ffmpeg_sha256": (
                result.ffmpeg.sha256 if result.ffmpeg is not None else None
            ),
            "launcher_sha256": result.launcher.sha256,
            "network_isolated": result.network_isolated,
            "runtime_fingerprint_sha256": result.runtime_fingerprint_sha256,
        },
    }
    _append_or_require_equal(payload, "generators", generator)

    actor_id: str | None = None
    if document.segments:
        actor_id = "actor:unknown"
        _append_or_require_unknown_actor(
            payload,
            {
                "id": actor_id,
                "role": "unknown",
                "visibility": "restricted",
            },
        )

    run_token = result.document.document_id.rpartition(":")[2]
    target_ids: list[str] = []
    event_ids: list[str] = []
    targets = _collection(payload, "targets")
    events = _collection(payload, "events")
    for index, segment in enumerate(document.segments, start=1):
        target_id = f"target:asr-{run_token}-{index}"
        event_id = f"event:asr-{run_token}-{index}"
        target_ids.append(target_id)
        event_ids.append(event_id)
        targets.append(
            {
                "id": target_id,
                "source_id": document.source_id,
                "selector": {
                    "stream_id": document.stream_id,
                    "start_us": segment.start_us,
                    "duration_us": segment.end_us - segment.start_us,
                    "spatial": None,
                },
                "musical_selector": None,
                "alignment_state": "not_applicable",
            }
        )
        events.append(
            {
                "id": event_id,
                "type": "speech",
                "scope": "evidence",
                "actor_id": actor_id,
                "target_ids": [target_id],
                "body": {
                    "format": "text",
                    "value": segment.text,
                    "language": segment.language,
                    "normalized_artifact_id": (
                        canonical.normalized_transcript_artifact_id
                    ),
                    "normalized_artifact_sha256": (
                        canonical.normalized_transcript_sha256
                    ),
                    "normalized_artifact_size_bytes": (
                        canonical.normalized_transcript_size_bytes
                    ),
                    "raw_artifact_id": canonical.raw_response_artifact_id,
                    "raw_artifact_sha256": canonical.raw_response_sha256,
                    "raw_artifact_size_bytes": canonical.raw_response_size_bytes,
                    "run_id": document.run_id,
                    "segment_id": segment.segment_id,
                    "word_ids": list(segment.word_ids),
                },
                "alternatives": [],
                "generator_id": generator_id,
                "rights_id": rights_id,
                "layer": "normalized_hypothesis",
                "confidence": {
                    "kind": "model_probability",
                    "value": segment.confidence,
                },
                "review_status": "machine_suggested",
            }
        )
    return TranscriptEvidenceRecords(
        actor_id=actor_id,
        generator_id=generator_id,
        target_ids=tuple(target_ids),
        event_ids=tuple(event_ids),
    )


def _collection(payload: dict[str, Any], name: str) -> list[dict[str, Any]]:
    value = payload.get(name)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise TranscriptEvidenceError(f"Project collection {name!r} is malformed.")
    return value


def _record(payload: dict[str, Any], collection: str, record_id: str) -> dict[str, Any]:
    matches = [
        item
        for item in _collection(payload, collection)
        if item.get("id") == record_id
    ]
    if len(matches) != 1:
        raise TranscriptEvidenceError(
            f"Project requires exactly one {collection} record {record_id!r}."
        )
    return matches[0]


def _append_or_require_equal(
    payload: dict[str, Any], collection: str, record: dict[str, Any]
) -> None:
    records = _collection(payload, collection)
    existing = [item for item in records if item.get("id") == record["id"]]
    if not existing:
        records.append(record)
        return
    if len(existing) != 1 or existing[0] != record:
        raise TranscriptEvidenceError(
            f"Existing {collection} record {record['id']!r} has different provenance."
        )


def _append_or_require_unknown_actor(
    payload: dict[str, Any], record: dict[str, Any]
) -> None:
    actors = _collection(payload, "actors")
    existing = [item for item in actors if item.get("id") == record["id"]]
    if not existing:
        actors.append(record)
        return
    if len(existing) != 1 or existing[0] != record:
        raise TranscriptEvidenceError("actor:unknown has incompatible project meaning.")
