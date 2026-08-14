"""Bounded JSON normalization for local analysis CLI output."""

from __future__ import annotations

import json
import math
from typing import Any, Callable, Mapping

from notewitness.domain.analysis import (
    ActivityHypothesis,
    AnalysisBatch,
    AnalysisResult,
    AnalysisStage,
    AnalysisState,
    AlignmentOutcome,
    InstrumentHypothesis,
    NoteHypothesis,
    PitchPointHypothesis,
    ScoreAlignmentHypothesis,
    SpeakerSegmentHypothesis,
)
from notewitness.domain.lesson import ActivityKind
from notewitness.domain.timeline import MediaSpan


MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_ITEMS = 50_000
MAX_STRING_CHARS = 4_096


class AnalysisCLIError(RuntimeError):
    """The local executable violated the bounded analysis JSON protocol."""


def parse_batch(
    raw: str,
    request: Any,
    adapter: Any,
    *,
    json_value: Callable[[Any, str], Any],
    unique_object: Callable[[list[tuple[str, Any]]], dict[str, Any]],
    expect_keys: Callable[[Any, set[str], str], None],
    enum: Callable[[Any, Any, str], Any],
    list_value: Callable[[Any, str], list[Any]],
    string_tuple: Callable[[Any, str], tuple[str, ...]],
    nullable_string: Callable[[Any, str], str | None],
    hypothesis: Callable[[Any, Any, Any, int], Any],
) -> AnalysisBatch:
    if len(raw.encode("utf-8")) > MAX_JSON_BYTES:
        raise AnalysisCLIError("Analysis CLI JSON output exceeds the bounded contract.")
    try:
        payload = json.loads(raw, object_pairs_hook=unique_object)
        payload = json_value(payload, "output")
    except (json.JSONDecodeError, AnalysisCLIError) as exc:
        raise AnalysisCLIError("Analysis CLI did not emit valid JSON.") from exc
    expect_keys(
        payload,
        {"state", "hypotheses", "diagnostics", "continuation_token"},
        "output",
    )
    state = enum(AnalysisState, payload["state"], "output.state")
    hypotheses_raw = list_value(payload["hypotheses"], "output.hypotheses")
    if len(hypotheses_raw) > MAX_JSON_ITEMS:
        raise AnalysisCLIError("Analysis CLI returned too many hypotheses.")
    diagnostics = string_tuple(payload["diagnostics"], "output.diagnostics")
    continuation = nullable_string(
        payload["continuation_token"], "output.continuation_token"
    )
    hypotheses = tuple(
        hypothesis(item, request, adapter, index)
        for index, item in enumerate(hypotheses_raw)
    )
    try:
        return AnalysisBatch(
            AnalysisResult(
                stage=adapter.stage,
                state=state,
                hypothesis_ids=tuple(item.hypothesis_id for item in hypotheses),
                diagnostics=diagnostics,
                continuation_token=continuation,
            ),
            hypotheses,
        )
    except ValueError as exc:
        raise AnalysisCLIError(
            "Analysis CLI output violates the analysis contract."
        ) from exc


def hypothesis(
    raw: Any,
    request: Any,
    adapter: Any,
    index: int,
    *,
    expect_keys_with_optional: Callable[[Any, set[str], set[str], str], None],
    string: Callable[[Any, str], str],
    enum: Callable[[Any, Any, str], Any],
    span: Callable[[Any, Any, str], MediaSpan],
    nullable_number: Callable[[Any, str], float | None],
    nullable_string: Callable[[Any, str], str | None],
    nullable_integer_in_range: Callable[[Any, int, int, str], int | None],
    number_tuple: Callable[[Any, str], tuple[float, ...]],
    json_value: Callable[[Any, str], Any],
    mapping: Callable[[Any, str], Mapping[str, Any]],
    string_tuple: Callable[[Any, str], tuple[str, ...]],
) -> Any:
    label = f"output.hypotheses[{index}]"
    common = {"hypothesis_id", "span", "state", "confidence"}
    stage_fields = {
        AnalysisStage.ACTIVITY_SEGMENTATION: {"kind"},
        AnalysisStage.ANONYMOUS_DIARIZATION: {"anonymous_cluster_id"},
        AnalysisStage.NOTE_TRANSCRIPTION: {"midi_pitch", "frequency_hz"},
        AnalysisStage.CONTINUOUS_PITCH: {"frequency_hz"},
        AnalysisStage.INSTRUMENT_DETECTION: {"instrument_label"},
        AnalysisStage.INSTRUMENT_DIARIZATION: {
            "instrument_label",
            "anonymous_instrument_track_id",
        },
        AnalysisStage.SCORE_ALIGNMENT: {
            "outcome",
            "score_id",
            "score_position",
            "source_hypothesis_ids",
        },
    }
    optional_fields = {
        AnalysisStage.NOTE_TRANSCRIPTION: {
            "amplitude",
            "pitch_bend_unit",
            "pitch_bend_values",
            "source_track_id",
            "velocity",
        },
        AnalysisStage.INSTRUMENT_DETECTION: {"anonymous_instrument_track_id"},
    }
    expect_keys_with_optional(
        raw,
        common | stage_fields[adapter.stage],
        optional_fields.get(adapter.stage, set()),
        label,
    )
    hypothesis_id = string(raw["hypothesis_id"], f"{label}.hypothesis_id")
    state = enum(AnalysisState, raw["state"], f"{label}.state")
    parsed_span = span(raw["span"], request, f"{label}.span")
    confidence = nullable_number(raw["confidence"], f"{label}.confidence")
    try:
        if adapter.stage is AnalysisStage.ACTIVITY_SEGMENTATION:
            kind_raw = raw["kind"]
            kind = (
                None
                if kind_raw is None
                else enum(ActivityKind, kind_raw, f"{label}.kind")
            )
            return ActivityHypothesis(
                hypothesis_id,
                parsed_span,
                state,
                kind,
                confidence,
                adapter.generator_id,
            )
        if adapter.stage is AnalysisStage.ANONYMOUS_DIARIZATION:
            return SpeakerSegmentHypothesis(
                hypothesis_id,
                parsed_span,
                state,
                nullable_string(
                    raw["anonymous_cluster_id"],
                    f"{label}.anonymous_cluster_id",
                ),
                None,
                confidence,
                adapter.generator_id,
            )
        if adapter.stage is AnalysisStage.NOTE_TRANSCRIPTION:
            return NoteHypothesis(
                hypothesis_id,
                parsed_span,
                state,
                nullable_number(raw["midi_pitch"], f"{label}.midi_pitch"),
                nullable_number(raw["frequency_hz"], f"{label}.frequency_hz"),
                confidence,
                adapter.generator_id,
                nullable_string(raw.get("source_track_id"), f"{label}.source_track_id"),
                nullable_number(raw.get("amplitude"), f"{label}.amplitude"),
                nullable_integer_in_range(
                    raw.get("velocity"),
                    0,
                    127,
                    f"{label}.velocity",
                ),
                number_tuple(
                    raw.get("pitch_bend_values", []),
                    f"{label}.pitch_bend_values",
                ),
                nullable_string(raw.get("pitch_bend_unit"), f"{label}.pitch_bend_unit"),
            )
        if adapter.stage is AnalysisStage.CONTINUOUS_PITCH:
            return PitchPointHypothesis(
                hypothesis_id,
                parsed_span,
                state,
                nullable_number(raw["frequency_hz"], f"{label}.frequency_hz"),
                confidence,
                adapter.generator_id,
            )
        if adapter.stage in {
            AnalysisStage.INSTRUMENT_DETECTION,
            AnalysisStage.INSTRUMENT_DIARIZATION,
        }:
            return InstrumentHypothesis(
                hypothesis_id,
                parsed_span,
                state,
                nullable_string(raw["instrument_label"], f"{label}.instrument_label"),
                None,
                confidence,
                adapter.generator_id,
                nullable_string(
                    raw.get("anonymous_instrument_track_id"),
                    f"{label}.anonymous_instrument_track_id",
                ),
            )
        outcome = enum(AlignmentOutcome, raw["outcome"], f"{label}.outcome")
        score_position = raw["score_position"]
        if score_position is not None:
            score_position = mapping(
                json_value(score_position, f"{label}.score_position"),
                f"{label}.score_position",
            )
        return ScoreAlignmentHypothesis(
            hypothesis_id,
            parsed_span,
            state,
            outcome,
            nullable_string(raw["score_id"], f"{label}.score_id"),
            score_position,
            string_tuple(
                raw["source_hypothesis_ids"],
                f"{label}.source_hypothesis_ids",
            ),
            confidence,
            adapter.generator_id,
        )
    except ValueError as exc:
        raise AnalysisCLIError(
            f"{label} violates the typed hypothesis contract."
        ) from exc


def span(raw: Any, request: Any, label: str) -> MediaSpan:
    expect_keys(raw, {"stream_id", "start_us", "duration_us"}, label)
    stream = string(raw["stream_id"], f"{label}.stream_id")
    start = integer(raw["start_us"], f"{label}.start_us")
    duration = integer(raw["duration_us"], f"{label}.duration_us")
    try:
        parsed_span = MediaSpan(request.source_id, stream, start, duration)
    except ValueError as exc:
        raise AnalysisCLIError(f"{label} is invalid.") from exc
    if not any(
        parsed_span.stream_id == allowed.stream_id
        and allowed.start_us <= parsed_span.start_us
        and parsed_span.end_us <= allowed.end_us
        for allowed in request.spans
    ):
        raise AnalysisCLIError(f"{label} lies outside the requested source span.")
    return parsed_span


def span_payload(span: MediaSpan, *, include_source: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "stream_id": span.stream_id,
        "start_us": span.start_us,
        "duration_us": span.duration_us,
    }
    if include_source:
        payload["source_id"] = span.source_id
    return payload


def expect_keys(value: Any, expected: set[str], label: str) -> None:
    if set(mapping(value, label)) != expected:
        raise AnalysisCLIError(f"{label} has unknown or missing keys.")


def expect_keys_with_optional(
    value: Any,
    required: set[str],
    optional: set[str],
    label: str,
) -> None:
    keys = set(mapping(value, label))
    if required - keys or keys - required - optional:
        raise AnalysisCLIError(f"{label} has unknown or missing keys.")


def mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise AnalysisCLIError(f"{label} must be an object.")
    return value


def list_value(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise AnalysisCLIError(f"{label} must be an array.")
    return value


def string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_STRING_CHARS:
        raise AnalysisCLIError(f"{label} must be a bounded non-empty string.")
    return value


def nullable_string(value: Any, label: str) -> str | None:
    return None if value is None else string(value, label)


def string_tuple(value: Any, label: str) -> tuple[str, ...]:
    result = tuple(string(item, label) for item in list_value(value, label))
    if len(result) > MAX_JSON_ITEMS or len(result) != len(set(result)):
        raise AnalysisCLIError(f"{label} must contain bounded unique strings.")
    return result


def integer(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise AnalysisCLIError(f"{label} must be a non-negative integer.")
    return value


def nullable_number(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AnalysisCLIError(f"{label} must be a finite number or null.")
    if not math.isfinite(value):
        raise AnalysisCLIError(f"{label} must be a finite number or null.")
    return float(value)


def nullable_integer_in_range(
    value: Any,
    minimum: int,
    maximum: int,
    label: str,
) -> int | None:
    if value is None:
        return None
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise AnalysisCLIError(
            f"{label} must be an integer in [{minimum}, {maximum}] or null."
        )
    return value


def number_tuple(value: Any, label: str) -> tuple[float, ...]:
    items = list_value(value, label)
    if len(items) > MAX_JSON_ITEMS:
        raise AnalysisCLIError(f"{label} contains too many values.")
    result: list[float] = []
    for item in items:
        parsed = nullable_number(item, label)
        if parsed is None:
            raise AnalysisCLIError(f"{label} must contain only finite numbers.")
        result.append(parsed)
    return tuple(result)


def enum(enum_type: Any, value: Any, label: str) -> Any:
    try:
        return enum_type(string(value, label))
    except ValueError as exc:
        raise AnalysisCLIError(f"{label} has an unsupported value.") from exc


def json_bytes(value: Any, label: str) -> bytes:
    try:
        encoded = json.dumps(
            value,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AnalysisCLIError(f"{label} is not JSON-safe.") from exc
    if len(encoded) > MAX_JSON_BYTES:
        raise AnalysisCLIError(f"{label} exceeds the bounded JSON contract.")
    return encoded


def json_value(value: Any, label: str, depth: int = 0) -> Any:
    if depth > MAX_JSON_DEPTH:
        raise AnalysisCLIError(f"{label} exceeds the JSON nesting limit.")
    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str) and len(value) > MAX_STRING_CHARS:
            raise AnalysisCLIError(f"{label} contains an oversized string.")
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AnalysisCLIError(f"{label} contains a non-finite number.")
        return value
    if isinstance(value, Mapping):
        if len(value) > MAX_JSON_ITEMS:
            raise AnalysisCLIError(f"{label} contains too many items.")
        return {
            string(key, f"{label} key"): json_value(item, label, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_JSON_ITEMS:
            raise AnalysisCLIError(f"{label} contains too many items.")
        return [json_value(item, label, depth + 1) for item in value]
    raise AnalysisCLIError(f"{label} is not JSON-safe.")


def reject_path_like_values(value: Any, label: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if "path" in key.lower() or "media" in key.lower():
                raise AnalysisCLIError(f"{label} must not contain media paths.")
            reject_path_like_values(item, label)
    elif isinstance(value, list):
        for item in value:
            reject_path_like_values(item, label)
    elif isinstance(value, str) and (value.startswith("/") or value.startswith("~")):
        raise AnalysisCLIError(f"{label} must not contain filesystem paths.")


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AnalysisCLIError(f"Analysis CLI output duplicates key {key!r}.")
        result[key] = value
    return result
