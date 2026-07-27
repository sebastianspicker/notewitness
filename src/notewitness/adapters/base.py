"""Small, network-free boundary shared by local ASR and MIR adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True, slots=True)
class SourceSpan:
    source_id: str
    start_us: int
    duration_us: int

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in (self.start_us, self.duration_us)
        ):
            raise ValueError("Source spans use non-negative integer microseconds.")


@dataclass(frozen=True, slots=True)
class Hypothesis:
    kind: str
    span: SourceSpan
    value: Mapping[str, Any]
    confidence: Mapping[str, Any]
    generator_id: str


class AnalysisAdapter(Protocol):
    name: str
    version: str

    def analyze(self, span: SourceSpan) -> Sequence[Hypothesis]:
        """Return suggestions without mutating graph or accepted annotations."""
