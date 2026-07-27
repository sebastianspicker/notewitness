"""Canonical media-time values shared across workbench domains."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


def _non_negative_microseconds(value: int, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer microsecond value.")
    return value


@dataclass(frozen=True, slots=True)
class MediaSpan:
    """A bounded span on one source stream's monotonic timeline."""

    source_id: str
    stream_id: str
    start_us: int
    duration_us: int

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("source_id must not be empty.")
        if not self.stream_id:
            raise ValueError("stream_id must not be empty.")
        _non_negative_microseconds(self.start_us, "start_us")
        _non_negative_microseconds(self.duration_us, "duration_us")

    @property
    def end_us(self) -> int:
        return self.start_us + self.duration_us


@dataclass(frozen=True, slots=True)
class EvidenceAnchor:
    """A graph target resolved to playable media and optional musical time."""

    target_id: str
    span: MediaSpan
    alignment_state: str
    musical_selector: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.target_id:
            raise ValueError("target_id must not be empty.")
        if not self.alignment_state:
            raise ValueError("alignment_state must not be empty.")

