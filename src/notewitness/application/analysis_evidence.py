"""Append typed local-analysis hypotheses as reviewable graph evidence."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Iterable, Mapping

from notewitness.domain.analysis import (
    ActivityHypothesis,
    AnalysisBatch,
    InstrumentHypothesis,
    NoteHypothesis,
    PitchPointHypothesis,
    ScoreAlignmentHypothesis,
    SpeakerSegmentHypothesis,
)
from notewitness.evidence_contract import ID_PATTERN, SHA256_PATTERN


MAX_PROJECTED_BATCHES = 32
MAX_PROJECTED_HYPOTHESES = 50_000
_RUN_TOKEN = re.compile(r"^[A-Za-z0-9._-]+$")


class AnalysisEvidenceError(RuntimeError):
    """Typed analysis output cannot be represented safely in the graph."""


@dataclass(frozen=True, slots=True)
class AnalysisEvidenceContext:
    run_token: str
    generator_id: str
    generator_name: str
    generator_version: str
    model_name: str
    weight_hash_state: str
    raw_artifact_id: str
    raw_artifact_sha256: str
    raw_artifact_size_bytes: int
    parameters: Mapping[str, Any]
    analysis_run_id: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.generator_id, "generator_id"),
            (self.raw_artifact_id, "raw_artifact_id"),
        ):
            if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
                raise ValueError(f"{label} must be a valid graph ID.")
        if not isinstance(self.run_token, str) or not _RUN_TOKEN.fullmatch(
            self.run_token
        ):
            raise ValueError("run_token must contain only ID-safe characters.")
        for value, label in (
            (self.generator_name, "generator_name"),
            (self.generator_version, "generator_version"),
            (self.model_name, "model_name"),
            (self.weight_hash_state, "weight_hash_state"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be a non-empty string.")
        if not SHA256_PATTERN.fullmatch(self.raw_artifact_sha256):
            raise ValueError("raw_artifact_sha256 must be a lowercase SHA-256.")
        if (
            not isinstance(self.raw_artifact_size_bytes, int)
            or isinstance(self.raw_artifact_size_bytes, bool)
            or self.raw_artifact_size_bytes < 0
        ):
            raise ValueError("raw_artifact_size_bytes must be non-negative.")
        if self.analysis_run_id is not None and (
            not isinstance(self.analysis_run_id, str)
            or not self.analysis_run_id
            or len(self.analysis_run_id) > 512
        ):
            raise ValueError("analysis_run_id must be a bounded non-empty string.")
        _require_json_object(self.parameters, "parameters")


@dataclass(frozen=True, slots=True)
class AnalysisEvidenceRecords:
    actor_id: str | None
    generator_id: str
    target_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    hypothesis_ids: tuple[str, ...]


def append_analysis_batches(
    payload: dict[str, Any],
    *,
    source_id: str,
    batches: Iterable[AnalysisBatch],
    context: AnalysisEvidenceContext,
    known_note_or_pitch_ids: Iterable[str] = (),
) -> AnalysisEvidenceRecords:
    """Append normalized machine suggestions without accepting their claims."""

    planned = tuple(batches)
    if not planned or len(planned) > MAX_PROJECTED_BATCHES:
        raise AnalysisEvidenceError(
            f"Analysis projection requires 1-{MAX_PROJECTED_BATCHES} batches."
        )
    source = _record(payload, "sources", source_id)
    rights_id = source.get("rights_id")
    if not isinstance(rights_id, str):
        raise AnalysisEvidenceError("Analysis source has no valid rights record.")

    hypotheses = tuple(item for batch in planned for item in batch.hypotheses)
    if len(hypotheses) > MAX_PROJECTED_HYPOTHESES:
        raise AnalysisEvidenceError(
            f"Analysis projection exceeds {MAX_PROJECTED_HYPOTHESES} hypotheses."
        )
    hypothesis_ids = require_unique_hypothesis_ids(planned)
    if any(item.span.source_id != source_id for item in hypotheses):
        raise AnalysisEvidenceError("Analysis hypotheses must target the selected source.")
    if any(item.generator_id != context.generator_id for item in hypotheses):
        raise AnalysisEvidenceError("Analysis generator provenance does not match context.")

    note_or_pitch_ids = {
        item.hypothesis_id
        for item in hypotheses
        if isinstance(item, (NoteHypothesis, PitchPointHypothesis))
    }
    external_note_or_pitch_ids = tuple(known_note_or_pitch_ids)
    if any(
        not isinstance(item, str) or not item
        for item in external_note_or_pitch_ids
    ):
        raise AnalysisEvidenceError(
            "Known note or pitch IDs must be non-empty strings."
        )
    note_or_pitch_ids.update(external_note_or_pitch_ids)
    actor_id = f"actor:analysis-unknown-{context.run_token}" if hypotheses else None
    targets: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for index, hypothesis in enumerate(hypotheses, start=1):
        event_type, body_format, body_value, alignment, musical_selector = (
            _project_hypothesis(hypothesis, note_or_pitch_ids)
        )
        target_id = f"target:analysis-{context.run_token}-{index}"
        event_id = f"event:analysis-{context.run_token}-{index}"
        targets.append(
            {
                "id": target_id,
                "source_id": source_id,
                "selector": {
                    "stream_id": hypothesis.span.stream_id,
                    "start_us": hypothesis.span.start_us,
                    "duration_us": hypothesis.span.duration_us,
                    "spatial": None,
                },
                "musical_selector": musical_selector,
                "alignment_state": alignment,
            }
        )
        confidence: dict[str, Any] = {"kind": "adapter_reported"}
        if hypothesis.confidence is not None:
            confidence["value"] = hypothesis.confidence
        events.append(
            {
                "id": event_id,
                "type": event_type,
                "scope": "evidence",
                "actor_id": actor_id,
                "target_ids": [target_id],
                "body": {
                    "format": body_format,
                    "value": body_value,
                    "analysis_state": hypothesis.state.value,
                    "hypothesis_id": hypothesis.hypothesis_id,
                    "raw_artifact_id": context.raw_artifact_id,
                    "raw_artifact_sha256": context.raw_artifact_sha256,
                    "raw_artifact_size_bytes": context.raw_artifact_size_bytes,
                    **(
                        {"analysis_run_id": context.analysis_run_id}
                        if context.analysis_run_id is not None
                        else {}
                    ),
                },
                "alternatives": [],
                "generator_id": context.generator_id,
                "rights_id": rights_id,
                "layer": "normalized_hypothesis",
                "confidence": confidence,
                "review_status": "machine_suggested",
            }
        )
    _require_json_object({"targets": targets, "events": events}, "projection")
    _require_new_ids(payload, "targets", (item["id"] for item in targets))
    _require_new_ids(payload, "events", (item["id"] for item in events))

    generator = {
        "id": context.generator_id,
        "kind": "machine",
        "name": context.generator_name,
        "version": context.generator_version,
        "model": context.model_name,
        "weight_hash_state": context.weight_hash_state,
        "parameters": dict(context.parameters),
    }
    _append_or_require_equal(payload, "generators", generator)
    if actor_id is not None:
        _append_or_require_equal(
            payload,
            "actors",
            {
                "id": actor_id,
                "role": "unknown",
                "visibility": "restricted",
            },
        )
    _collection(payload, "targets").extend(targets)
    _collection(payload, "events").extend(events)
    return AnalysisEvidenceRecords(
        actor_id=actor_id,
        generator_id=context.generator_id,
        target_ids=tuple(item["id"] for item in targets),
        event_ids=tuple(item["id"] for item in events),
        hypothesis_ids=hypothesis_ids,
    )


def require_unique_hypothesis_ids(
    batches: Iterable[AnalysisBatch],
) -> tuple[str, ...]:
    """Reject ambiguous hypothesis identities across a complete analysis run."""

    hypothesis_ids = tuple(
        hypothesis.hypothesis_id
        for batch in batches
        for hypothesis in batch.hypotheses
    )
    if len(hypothesis_ids) != len(set(hypothesis_ids)):
        raise AnalysisEvidenceError(
            "Analysis hypothesis IDs must be globally unique across the run."
        )
    return hypothesis_ids


def _project_hypothesis(
    hypothesis: object,
    note_or_pitch_ids: set[str],
) -> tuple[str, str, Any, str, Mapping[str, Any] | None]:
    if isinstance(hypothesis, ActivityHypothesis):
        if hypothesis.kind is None:
            raise AnalysisEvidenceError("Activity hypotheses require a projected kind.")
        event_type = hypothesis.kind.value
        if event_type == "other_sound":
            event_type = "local:other_sound"
        return (
            event_type,
            "application/vnd.notewitness.activity+json",
            {"kind": hypothesis.kind.value},
            "not_applicable",
            None,
        )
    if isinstance(hypothesis, SpeakerSegmentHypothesis):
        if hypothesis.confirmed_actor_id is not None:
            raise AnalysisEvidenceError(
                "Automatic diarization cannot confirm a human actor identity."
            )
        return (
            "local:diarization",
            "application/vnd.notewitness.speaker-segment+json",
            {"anonymous_cluster_id": hypothesis.anonymous_cluster_id},
            "not_applicable",
            None,
        )
    if isinstance(hypothesis, NoteHypothesis):
        return (
            "local:note",
            "application/vnd.notewitness.note+json",
            {
                "midi_pitch": hypothesis.midi_pitch,
                "frequency_hz": hypothesis.frequency_hz,
                **(
                    {"source_track_id": hypothesis.source_track_id}
                    if hypothesis.source_track_id is not None
                    else {}
                ),
                **(
                    {"amplitude": hypothesis.amplitude}
                    if hypothesis.amplitude is not None
                    else {}
                ),
                **(
                    {"velocity": hypothesis.velocity}
                    if hypothesis.velocity is not None
                    else {}
                ),
                **(
                    {
                        "pitch_bend_values": list(
                            hypothesis.pitch_bend_values
                        ),
                        "pitch_bend_unit": hypothesis.pitch_bend_unit,
                    }
                    if hypothesis.pitch_bend_values
                    else {}
                ),
            },
            "unknown",
            None,
        )
    if isinstance(hypothesis, PitchPointHypothesis):
        return (
            "local:pitch",
            "application/vnd.notewitness.pitch+json",
            {"frequency_hz": hypothesis.frequency_hz},
            "not_alignable",
            None,
        )
    if isinstance(hypothesis, InstrumentHypothesis):
        if hypothesis.actor_id is not None:
            raise AnalysisEvidenceError(
                "Automatic instrument detection cannot attribute a human actor."
            )
        return (
            "local:instrument",
            "application/vnd.notewitness.instrument+json",
            {
                "instrument_label": hypothesis.instrument_label,
                **(
                    {
                        "anonymous_instrument_track_id": (
                            hypothesis.anonymous_instrument_track_id
                        )
                    }
                    if hypothesis.anonymous_instrument_track_id is not None
                    else {}
                ),
            },
            "not_applicable",
            None,
        )
    if isinstance(hypothesis, ScoreAlignmentHypothesis):
        missing = set(hypothesis.source_hypothesis_ids) - note_or_pitch_ids
        if missing:
            raise AnalysisEvidenceError(
                "Score alignment references unknown note or pitch hypotheses."
            )
        musical_selector = _score_selector(hypothesis)
        return (
            "local:score_alignment",
            "application/vnd.notewitness.score-alignment+json",
            {
                "outcome": hypothesis.outcome.value,
                "score_id": hypothesis.score_id,
                "score_position": (
                    dict(hypothesis.score_position)
                    if hypothesis.score_position is not None
                    else None
                ),
                "source_hypothesis_ids": list(hypothesis.source_hypothesis_ids),
            },
            hypothesis.outcome.value,
            musical_selector,
        )
    raise AnalysisEvidenceError(
        f"Unsupported projected hypothesis type: {type(hypothesis).__name__}."
    )


def _score_selector(
    hypothesis: ScoreAlignmentHypothesis,
) -> Mapping[str, Any] | None:
    if hypothesis.outcome.value != "aligned":
        return None
    if hypothesis.score_id is None or hypothesis.score_position is None:
        raise AnalysisEvidenceError("Aligned score evidence requires a position.")
    position = dict(hypothesis.score_position)
    if set(position) & {"kind", "score_id"}:
        raise AnalysisEvidenceError("Score position uses reserved selector fields.")
    return {
        "kind": "score_position",
        "score_id": hypothesis.score_id,
        **position,
    }


def _require_json_object(value: Mapping[str, Any], label: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite JSON data.") from exc


def _collection(payload: dict[str, Any], name: str) -> list[dict[str, Any]]:
    value = payload.get(name)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise AnalysisEvidenceError(f"Project collection {name!r} is malformed.")
    return value


def _record(payload: dict[str, Any], collection: str, record_id: str) -> dict[str, Any]:
    matches = [
        item
        for item in _collection(payload, collection)
        if item.get("id") == record_id
    ]
    if len(matches) != 1:
        raise AnalysisEvidenceError(
            f"Project requires exactly one {collection} record {record_id!r}."
        )
    return matches[0]


def _require_new_ids(
    payload: dict[str, Any],
    collection: str,
    record_ids: Iterable[str],
) -> None:
    existing = {str(item.get("id")) for item in _collection(payload, collection)}
    planned = tuple(record_ids)
    if len(planned) != len(set(planned)) or existing.intersection(planned):
        raise AnalysisEvidenceError(
            f"Analysis projection would collide with existing {collection} IDs."
        )


def _append_or_require_equal(
    payload: dict[str, Any],
    collection: str,
    record: dict[str, Any],
) -> None:
    records = _collection(payload, collection)
    existing = [item for item in records if item.get("id") == record["id"]]
    if not existing:
        records.append(record)
        return
    if len(existing) != 1 or existing[0] != record:
        raise AnalysisEvidenceError(
            f"Existing {collection} record {record['id']!r} has different provenance."
        )
