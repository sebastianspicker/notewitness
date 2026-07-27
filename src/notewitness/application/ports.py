"""Replaceable boundaries for local infrastructure and analysis engines."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Protocol

from notewitness.domain.analysis import (
    AnalysisBatch,
    AnalysisRequest,
    AnalysisStage,
    JobCheckpoint,
)
from notewitness.domain.capture import CaptureHandle, CaptureRequest, CapturedMedia
from notewitness.domain.interop import ExportPreflight, ExportResult
from notewitness.domain.lesson import LessonNotes
from notewitness.domain.utilities import MetronomeTick


class CapturePort(Protocol):
    """Backend called by a worker/UI coordinator, never the playback thread."""

    def start(self, request: CaptureRequest) -> CaptureHandle:
        """Start capture using only the inputs the user explicitly selected."""

    def finish(self, handle: CaptureHandle) -> CapturedMedia:
        """Finalize media and return its content identity."""

    def cancel(self, handle: CaptureHandle) -> None:
        """Stop capture and discard incomplete output under backend policy."""


class AnalysisPort(Protocol):
    """One local analysis stage that emits suggestions, never accepted edits."""

    stage: AnalysisStage
    name: str
    version: str
    generator_id: str

    def analyze(self, request: AnalysisRequest) -> AnalysisBatch:
        """Run a bounded request and expose incomplete/unsupported states."""


class ProjectRepositoryPort(Protocol):
    def load_payload(self, path: Path) -> Mapping[str, Any]:
        """Load one validated project snapshot."""

    def append_revision(self, project_id: str, revision: Mapping[str, Any]) -> str:
        """Append a revision without overwriting accepted human work."""


class JobStorePort(Protocol):
    def save_checkpoint(self, checkpoint: JobCheckpoint) -> None:
        """Persist bounded progress so cancellation leaves valid state."""

    def load_checkpoint(self, job_id: str) -> JobCheckpoint | None:
        """Return the last durable checkpoint, if any."""


class ExportPort(Protocol):
    def preflight(self, project_id: str, export_format: str) -> ExportPreflight:
        """Preview rights and semantic loss before writing."""

    def export(self, preflight: ExportPreflight) -> ExportResult:
        """Write only an executable, acknowledged preflight."""


class LessonCatalogPort(Protocol):
    def neighbors(self, project_id: str) -> tuple[str | None, str | None]:
        """Resolve previous/next authorized lessons in a local collection."""

    def add_notes(self, notes: LessonNotes) -> None:
        """Index a private lesson artifact without copying source media."""


class PitchInputPort(Protocol):
    def latest_frequency_hz(self) -> float | None:
        """Return a local monophonic pitch estimate without retaining voiceprints."""


class MetronomeOutputPort(Protocol):
    def play(self, ticks: tuple[MetronomeTick, ...]) -> None:
        """Render a bounded click schedule through a local audio backend."""
