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
_ProjectedHypothesis = tuple[str, str, Any, str, Mapping[str, Any] | None]


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
        _validate_context_graph_ids(self)
        _validate_context_run_token(self)
        _validate_context_metadata(self)
        _validate_context_artifact_sha256(self)
        _validate_context_artifact_size(self)
        _validate_context_run_id(self)
        _validate_context_parameters(self)


@dataclass(frozen=True, slots=True)
class AnalysisEvidenceRecords:
    actor_id: str | None
    generator_id: str
    target_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    hypothesis_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _AnalysisProjectionInput:
    source_id: str
    batches: tuple[AnalysisBatch, ...]
    context: AnalysisEvidenceContext
    known_note_or_pitch_ids: Iterable[str]


@dataclass(frozen=True, slots=True)
class _AnalysisProjectionPlan:
    source_id: str
    rights_id: str
    context: AnalysisEvidenceContext
    hypotheses: tuple[object, ...]
    hypothesis_ids: tuple[str, ...]
    note_or_pitch_ids: set[str]
    actor_id: str | None


@dataclass(frozen=True, slots=True)
class _ProjectedEvidenceRecords:
    targets: list[dict[str, Any]]
    events: list[dict[str, Any]]


def append_analysis_batches(
    payload: dict[str, Any],
    *,
    source_id: str,
    batches: Iterable[AnalysisBatch],
    context: AnalysisEvidenceContext,
    known_note_or_pitch_ids: Iterable[str] = (),
) -> AnalysisEvidenceRecords:
    """Append normalized machine suggestions without accepting their claims."""

    projection_input = _AnalysisProjectionInput(
        source_id=source_id,
        batches=tuple(batches),
        context=context,
        known_note_or_pitch_ids=known_note_or_pitch_ids,
    )
    plan = _plan_analysis_projection(payload, projection_input)
    projected_records = _build_projected_evidence_records(plan)
    return _commit_projected_evidence(payload, plan, projected_records)


def _plan_analysis_projection(
    payload: dict[str, Any],
    projection_input: _AnalysisProjectionInput,
) -> _AnalysisProjectionPlan:
    rights_id = _projection_rights_id(payload, projection_input)
    hypotheses, hypothesis_ids = _validated_projection_hypotheses(projection_input)
    note_or_pitch_ids = _projection_note_or_pitch_ids(
        projection_input,
        hypotheses,
    )
    actor_id = (
        f"actor:analysis-unknown-{projection_input.context.run_token}"
        if hypotheses
        else None
    )
    return _AnalysisProjectionPlan(
        source_id=projection_input.source_id,
        rights_id=rights_id,
        context=projection_input.context,
        hypotheses=hypotheses,
        hypothesis_ids=hypothesis_ids,
        note_or_pitch_ids=note_or_pitch_ids,
        actor_id=actor_id,
    )


def _projection_rights_id(
    payload: dict[str, Any],
    projection_input: _AnalysisProjectionInput,
) -> str:
    planned = projection_input.batches
    if not planned or len(planned) > MAX_PROJECTED_BATCHES:
        raise AnalysisEvidenceError(
            f"Analysis projection requires 1-{MAX_PROJECTED_BATCHES} batches."
        )
    source = _record(payload, "sources", projection_input.source_id)
    rights_id = source.get("rights_id")
    if not isinstance(rights_id, str):
        raise AnalysisEvidenceError("Analysis source has no valid rights record.")
    return rights_id


def _validated_projection_hypotheses(
    projection_input: _AnalysisProjectionInput,
) -> tuple[tuple[object, ...], tuple[str, ...]]:
    hypotheses = tuple(
        item for batch in projection_input.batches for item in batch.hypotheses
    )
    if len(hypotheses) > MAX_PROJECTED_HYPOTHESES:
        raise AnalysisEvidenceError(
            f"Analysis projection exceeds {MAX_PROJECTED_HYPOTHESES} hypotheses."
        )
    hypothesis_ids = require_unique_hypothesis_ids(projection_input.batches)
    if any(item.span.source_id != projection_input.source_id for item in hypotheses):
        raise AnalysisEvidenceError("Analysis hypotheses must target the selected source.")
    if any(
        item.generator_id != projection_input.context.generator_id
        for item in hypotheses
    ):
        raise AnalysisEvidenceError("Analysis generator provenance does not match context.")
    return hypotheses, hypothesis_ids


def _projection_note_or_pitch_ids(
    projection_input: _AnalysisProjectionInput,
    hypotheses: tuple[object, ...],
) -> set[str]:
    note_or_pitch_ids = {
        item.hypothesis_id
        for item in hypotheses
        if isinstance(item, (NoteHypothesis, PitchPointHypothesis))
    }
    external_note_or_pitch_ids = tuple(projection_input.known_note_or_pitch_ids)
    if any(
        not isinstance(item, str) or not item
        for item in external_note_or_pitch_ids
    ):
        raise AnalysisEvidenceError(
            "Known note or pitch IDs must be non-empty strings."
        )
    note_or_pitch_ids.update(external_note_or_pitch_ids)
    return note_or_pitch_ids


def _build_projected_evidence_records(
    plan: _AnalysisProjectionPlan,
) -> _ProjectedEvidenceRecords:
    targets: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for index, hypothesis in enumerate(plan.hypotheses, start=1):
        event_type, body_format, body_value, alignment, musical_selector = (
            _project_hypothesis(hypothesis, plan.note_or_pitch_ids)
        )
        target_id = f"target:analysis-{plan.context.run_token}-{index}"
        event_id = f"event:analysis-{plan.context.run_token}-{index}"
        targets.append(
            {
                "id": target_id,
                "source_id": plan.source_id,
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
                "actor_id": plan.actor_id,
                "target_ids": [target_id],
                "body": {
                    "format": body_format,
                    "value": body_value,
                    "analysis_state": hypothesis.state.value,
                    "hypothesis_id": hypothesis.hypothesis_id,
                    "raw_artifact_id": plan.context.raw_artifact_id,
                    "raw_artifact_sha256": plan.context.raw_artifact_sha256,
                    "raw_artifact_size_bytes": plan.context.raw_artifact_size_bytes,
                    **(
                        {"analysis_run_id": plan.context.analysis_run_id}
                        if plan.context.analysis_run_id is not None
                        else {}
                    ),
                },
                "alternatives": [],
                "generator_id": plan.context.generator_id,
                "rights_id": plan.rights_id,
                "layer": "normalized_hypothesis",
                "confidence": confidence,
                "review_status": "machine_suggested",
            }
        )
    return _ProjectedEvidenceRecords(targets=targets, events=events)


def _commit_projected_evidence(
    payload: dict[str, Any],
    plan: _AnalysisProjectionPlan,
    projected_records: _ProjectedEvidenceRecords,
) -> AnalysisEvidenceRecords:
    targets = projected_records.targets
    events = projected_records.events
    _require_json_object({"targets": targets, "events": events}, "projection")
    _require_new_ids(payload, "targets", (item["id"] for item in targets))
    _require_new_ids(payload, "events", (item["id"] for item in events))

    generator = {
        "id": plan.context.generator_id,
        "kind": "machine",
        "name": plan.context.generator_name,
        "version": plan.context.generator_version,
        "model": plan.context.model_name,
        "weight_hash_state": plan.context.weight_hash_state,
        "parameters": dict(plan.context.parameters),
    }
    _append_or_require_equal(payload, "generators", generator)
    if plan.actor_id is not None:
        _append_or_require_equal(
            payload,
            "actors",
            {
                "id": plan.actor_id,
                "role": "unknown",
                "visibility": "restricted",
            },
        )
    _collection(payload, "targets").extend(targets)
    _collection(payload, "events").extend(events)
    return AnalysisEvidenceRecords(
        actor_id=plan.actor_id,
        generator_id=plan.context.generator_id,
        target_ids=tuple(item["id"] for item in targets),
        event_ids=tuple(item["id"] for item in events),
        hypothesis_ids=plan.hypothesis_ids,
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
) -> _ProjectedHypothesis:
    if isinstance(hypothesis, ActivityHypothesis):
        return _project_activity_hypothesis(hypothesis)
    if isinstance(hypothesis, SpeakerSegmentHypothesis):
        return _project_speaker_segment_hypothesis(hypothesis)
    if isinstance(hypothesis, NoteHypothesis):
        return _project_note_hypothesis(hypothesis)
    if isinstance(hypothesis, PitchPointHypothesis):
        return _project_pitch_point_hypothesis(hypothesis)
    if isinstance(hypothesis, InstrumentHypothesis):
        return _project_instrument_hypothesis(hypothesis)
    if isinstance(hypothesis, ScoreAlignmentHypothesis):
        return _project_score_alignment_hypothesis(hypothesis, note_or_pitch_ids)
    raise AnalysisEvidenceError(
        f"Unsupported projected hypothesis type: {type(hypothesis).__name__}."
    )


def _project_activity_hypothesis(hypothesis: ActivityHypothesis) -> _ProjectedHypothesis:
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


def _project_speaker_segment_hypothesis(
    hypothesis: SpeakerSegmentHypothesis,
) -> _ProjectedHypothesis:
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


def _project_note_hypothesis(hypothesis: NoteHypothesis) -> _ProjectedHypothesis:
    value: dict[str, Any] = {
        "midi_pitch": hypothesis.midi_pitch,
        "frequency_hz": hypothesis.frequency_hz,
    }
    if hypothesis.source_track_id is not None:
        value["source_track_id"] = hypothesis.source_track_id
    if hypothesis.amplitude is not None:
        value["amplitude"] = hypothesis.amplitude
    if hypothesis.velocity is not None:
        value["velocity"] = hypothesis.velocity
    if hypothesis.pitch_bend_values:
        value["pitch_bend_values"] = list(hypothesis.pitch_bend_values)
        value["pitch_bend_unit"] = hypothesis.pitch_bend_unit
    return (
        "local:note",
        "application/vnd.notewitness.note+json",
        value,
        "unknown",
        None,
    )


def _project_pitch_point_hypothesis(
    hypothesis: PitchPointHypothesis,
) -> _ProjectedHypothesis:
    return (
        "local:pitch",
        "application/vnd.notewitness.pitch+json",
        {"frequency_hz": hypothesis.frequency_hz},
        "not_alignable",
        None,
    )


def _project_instrument_hypothesis(
    hypothesis: InstrumentHypothesis,
) -> _ProjectedHypothesis:
    if hypothesis.actor_id is not None:
        raise AnalysisEvidenceError(
            "Automatic instrument detection cannot attribute a human actor."
        )
    value: dict[str, Any] = {"instrument_label": hypothesis.instrument_label}
    if hypothesis.anonymous_instrument_track_id is not None:
        value["anonymous_instrument_track_id"] = (
            hypothesis.anonymous_instrument_track_id
        )
    return (
        "local:instrument",
        "application/vnd.notewitness.instrument+json",
        value,
        "not_applicable",
        None,
    )


def _project_score_alignment_hypothesis(
    hypothesis: ScoreAlignmentHypothesis,
    note_or_pitch_ids: set[str],
) -> _ProjectedHypothesis:
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


def _validate_context_graph_ids(context: AnalysisEvidenceContext) -> None:
    for value, label in (
        (context.generator_id, "generator_id"),
        (context.raw_artifact_id, "raw_artifact_id"),
    ):
        if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
            raise ValueError(f"{label} must be a valid graph ID.")


def _validate_context_run_token(context: AnalysisEvidenceContext) -> None:
    if not isinstance(context.run_token, str) or not _RUN_TOKEN.fullmatch(
        context.run_token
    ):
        raise ValueError("run_token must contain only ID-safe characters.")


def _validate_context_metadata(context: AnalysisEvidenceContext) -> None:
    for value, label in (
        (context.generator_name, "generator_name"),
        (context.generator_version, "generator_version"),
        (context.model_name, "model_name"),
        (context.weight_hash_state, "weight_hash_state"),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string.")


def _validate_context_artifact_sha256(context: AnalysisEvidenceContext) -> None:
    if not SHA256_PATTERN.fullmatch(context.raw_artifact_sha256):
        raise ValueError("raw_artifact_sha256 must be a lowercase SHA-256.")


def _validate_context_artifact_size(context: AnalysisEvidenceContext) -> None:
    if (
        not isinstance(context.raw_artifact_size_bytes, int)
        or isinstance(context.raw_artifact_size_bytes, bool)
        or context.raw_artifact_size_bytes < 0
    ):
        raise ValueError("raw_artifact_size_bytes must be non-negative.")


def _validate_context_run_id(context: AnalysisEvidenceContext) -> None:
    run_id = context.analysis_run_id
    if run_id is None:
        return
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("analysis_run_id must be a bounded non-empty string.")
    if len(run_id) > 512:
        raise ValueError("analysis_run_id must be a bounded non-empty string.")


def _validate_context_parameters(context: AnalysisEvidenceContext) -> None:
    _require_json_object(context.parameters, "parameters")


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
