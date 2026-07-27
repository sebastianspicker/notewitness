"""Shared immutable JSON validation for transcription domain records."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime
from enum import StrEnum
import json
import math
import re
from typing import Any, TypeVar

from notewitness.domain.timeline import MediaSpan


MAX_SETTINGS = 256
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SECRET_SETTING_PARTS = frozenset(
    {"api_key", "authorization", "credential", "password", "secret", "token"}
)
_EnumType = TypeVar("_EnumType", bound=StrEnum)


class FrozenJsonObject(Mapping[str, Any]):
    """JSON-serializable mapping that cannot change after construction."""

    __slots__ = ("_items",)

    def __init__(self, values: Mapping[str, Any]) -> None:
        object.__setattr__(self, "_items", tuple(values.items()))

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("Research settings snapshots are immutable.")

    def __getitem__(self, key: str) -> Any:
        for candidate, value in self._items:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __deepcopy__(self, memo: dict[int, object]) -> "FrozenJsonObject":
        return self


def _freeze_settings(
    settings: Mapping[str, Any], field_name: str
) -> FrozenJsonObject:
    if not isinstance(settings, Mapping):
        raise ValueError(f"{field_name} must be a JSON object.")
    if len(settings) > MAX_SETTINGS:
        raise ValueError(f"{field_name} is limited to {MAX_SETTINGS} entries.")
    keys = tuple(settings)
    for key in keys:
        if not isinstance(key, str) or not key:
            raise ValueError(f"{field_name} keys must be non-empty strings.")
        normalized = key.casefold().replace("-", "_")
        if any(part in normalized for part in _SECRET_SETTING_PARTS):
            raise ValueError(f"{field_name} must not contain secret settings.")
    frozen: dict[str, Any] = {}
    for key in sorted(keys):
        frozen[key] = _freeze_json(settings[key], field_name)
    result = FrozenJsonObject(frozen)
    try:
        json.dumps(_json_ready(result), allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be finite JSON data.") from exc
    return result


def _freeze_json(value: Any, field_name: str) -> Any:
    if isinstance(value, Mapping):
        return _freeze_settings(value, field_name)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, field_name) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError(f"{field_name} must be finite JSON data.")


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    return value


def _require_enum(
    value: object, enum_type: type[_EnumType], field_name: str
) -> None:
    if not isinstance(value, enum_type):
        raise ValueError(f"{field_name} must be a {enum_type.__name__} value.")


def _require_bool(value: object, field_name: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean.")


def _finite_number_in_range(value: object, minimum: float, maximum: float) -> bool:
    return bool(
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and minimum <= value <= maximum
    )


def _validate_timestamp(
    value: str | None, field_name: str, *, required: bool = False
) -> datetime | None:
    if value is None:
        if required:
            raise ValueError(f"{field_name} is required.")
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO 8601 timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO 8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone.")
    return parsed


def _span_contains(container: MediaSpan, candidate: MediaSpan) -> bool:
    return bool(
        container.source_id == candidate.source_id
        and container.stream_id == candidate.stream_id
        and candidate.start_us >= container.start_us
        and candidate.end_us <= container.end_us
    )
