"""Small dependency-free implementation of analysis-suite JSON v1 output."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping


MAX_BYTES = 2 * 1024 * 1024
MAX_ITEMS = 50_000
MAX_STRING = 4_096
REQUEST_KEYS = {
    "schema_version", "stage", "version", "generator_id", "model", "job_id",
    "source_id", "media", "score", "spans", "parameters", "continuation_token",
}
IDENTITY_KEYS = {"source_id", "path", "sha256", "size_bytes"}


class BridgeError(RuntimeError):
    """A local provider cannot truthfully satisfy this request."""


def main_request(argv: list[str], allowed_stages: set[str]) -> dict[str, Any]:
    if argv != ["--request", "request.json"]:
        raise BridgeError("expected exactly: --request request.json")
    payload = _load_request()
    _validate_request_shape(payload)
    _validate_request_contract(payload, allowed_stages)
    return payload


def _load_request() -> dict[str, Any]:
    try:
        raw = Path("request.json").read_bytes()
    except OSError as exc:
        raise BridgeError("request.json is unavailable") from exc
    if len(raw) > MAX_BYTES:
        raise BridgeError("request.json exceeds 2 MiB")
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, BridgeError) as exc:
        raise BridgeError("request.json must be valid unique-key UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise BridgeError("request.json does not match analysis-suite JSON v1")
    return payload


def _validate_request_shape(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict) or set(payload) != REQUEST_KEYS:
        raise BridgeError("request.json does not match analysis-suite JSON v1")


def _validate_request_contract(payload: dict[str, Any], allowed_stages: set[str]) -> None:
    if payload.get("schema_version") != 1:
        raise BridgeError("unsupported request schema_version")
    stage = payload.get("stage")
    if stage not in allowed_stages:
        raise BridgeError("bridge does not support the requested stage")
    _bounded_string(payload.get("source_id"), "source_id")
    _bounded_string(payload.get("job_id"), "job_id")
    _bounded_string(payload.get("version"), "version")
    _bounded_string(payload.get("generator_id"), "generator_id")
    _identity(payload.get("media"), "media")
    _identity(payload.get("model"), "model")
    if payload["media"]["source_id"] != payload["source_id"]:
        raise BridgeError("media source_id must match request source_id")
    _validate_spans(payload.get("spans"))
    if payload.get("score") is not None:
        _identity(payload["score"], "score")
    if not isinstance(payload.get("parameters"), dict):
        raise BridgeError("parameters must be an object")
    if payload.get("continuation_token") is not None:
        _bounded_string(payload["continuation_token"], "continuation_token")


def _validate_spans(spans: Any) -> None:
    if not isinstance(spans, list) or not spans or len(spans) > MAX_ITEMS:
        raise BridgeError("spans must be a non-empty bounded array")
    for span in spans:
        _span(span)


def media_path(request: Mapping[str, Any]) -> str:
    return request["media"]["path"]


def model_path(request: Mapping[str, Any]) -> str:
    return request["model"]["path"]


def response(hypotheses: Iterable[dict[str, Any]], diagnostics: Iterable[str] = ()) -> None:
    items = list(hypotheses)
    if len(items) > MAX_ITEMS:
        raise BridgeError("provider returned too many hypotheses")
    output = {
        "state": "ready" if items else "not_detected",
        "hypotheses": items,
        "diagnostics": list(dict.fromkeys(diagnostics)),
        "continuation_token": None,
    }
    encoded = json.dumps(output, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise BridgeError("provider response exceeds 2 MiB")
    sys.stdout.buffer.write(encoded)


def bounded_span(request: Mapping[str, Any], start_us: int, end_us: int) -> dict[str, Any]:
    if isinstance(start_us, bool) or isinstance(end_us, bool) or start_us < 0 or end_us <= start_us:
        raise BridgeError("provider returned an invalid time span")
    for requested in request["spans"]:
        limit_start = requested["start_us"]
        limit_end = limit_start + requested["duration_us"]
        if limit_start <= start_us and end_us <= limit_end:
            return {"stream_id": requested["stream_id"], "start_us": start_us,
                    "duration_us": end_us - start_us}
    raise BridgeError("provider returned a span outside the request")


def seconds_to_us(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise BridgeError(f"{label} must be a non-negative finite number")
    return int(round(float(value) * 1_000_000))


def confidence(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise BridgeError("provider confidence must be in [0, 1]")
    return float(value)


def fail(exc: Exception) -> None:
    print(f"provider bridge failed: {exc}", file=sys.stderr)
    raise SystemExit(2)


def _identity(value: Any, label: str) -> None:
    if not isinstance(value, dict) or set(value) != IDENTITY_KEYS:
        raise BridgeError(f"{label} must be a runtime-owned identity object")
    _bounded_string(value.get("source_id"), f"{label}.source_id")
    _identity_path(value.get("path"), label)
    _identity_checksum(value.get("sha256"), label)
    _positive_integer(value.get("size_bytes"), f"{label}.size_bytes")


def _identity_path(value: Any, label: str) -> None:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise BridgeError(f"{label}.path must be absolute")


def _identity_checksum(value: Any, label: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise BridgeError(f"{label}.sha256 must be a lowercase SHA-256")


def _positive_integer(value: Any, label: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise BridgeError(f"{label} must be positive")


def _span(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"stream_id", "start_us", "duration_us"}:
        raise BridgeError("span has unknown or missing keys")
    _bounded_string(value.get("stream_id"), "span.stream_id")
    for key in ("start_us", "duration_us"):
        if not isinstance(value.get(key), int) or isinstance(value[key], bool) or value[key] < 0:
            raise BridgeError(f"span.{key} must be a non-negative integer")
    if value["duration_us"] == 0:
        raise BridgeError("span.duration_us must be positive")


def _bounded_string(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value or len(value) > MAX_STRING:
        raise BridgeError(f"{label} must be a bounded non-empty string")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BridgeError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result
