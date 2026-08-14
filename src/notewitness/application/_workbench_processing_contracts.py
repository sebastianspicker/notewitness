"""Shared contracts and validation for the private workbench-processing parts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Protocol

from notewitness.project_store import ProjectStore


MAX_WORKBENCH_JOBS = 100
MAX_WORKBENCH_JOB_ATTEMPTS = 100
MAX_STATUS_MESSAGE_CHARS = 240
# Local tools use a five-second TERM grace period before SIGKILL. The worker
# needs enough time to observe cancellation, reap that process, publish the
# terminal job state, and leave its loop. This remains deliberately bounded:
# retaining the ownership lock is safer than letting a second service overlap
# a still-running local tool.
WORKBENCH_SHUTDOWN_WAIT_SECONDS = 12.0


class WorkbenchProcessingError(RuntimeError):
    """A GUI processing request violated its bounded local contract."""


class WorkbenchJobKind(StrEnum):
    TRANSCRIPTION = "transcription"
    ANALYSIS = "analysis"
    COMPLETE = "complete"


class WorkbenchJobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class WorkbenchJob:
    job_id: str
    kind: WorkbenchJobKind
    source_id: str
    state: WorkbenchJobState
    progress_percent: int
    status_message: str
    error_code: str | None
    retryable: bool
    cancel_requested: bool
    completed_steps: tuple[str, ...]
    attempt: int
    created_at: str
    updated_at: str

    def as_public_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        payload["state"] = self.state.value
        payload["label"] = {
            WorkbenchJobKind.TRANSCRIPTION: "Speech transcription",
            WorkbenchJobKind.ANALYSIS: "Music and teaching analysis",
            WorkbenchJobKind.COMPLETE: "Complete local pass",
        }[self.kind]
        return payload


class WorkbenchExecutor(Protocol):
    def status(self) -> Mapping[str, object]:
        """Return non-sensitive readiness booleans and explanatory state."""

    def execute(
        self,
        kind: WorkbenchJobKind,
        source_id: str,
        *,
        job_id: str,
        attempt: int,
        cancellation_requested: Callable[[], bool],
        report_progress: Callable[[int, str], None],
        completed_steps: frozenset[str],
        mark_step_completed: Callable[[str], None],
    ) -> None:
        """Run only startup-approved local adapters for one project source."""


class DisabledWorkbenchExecutor:
    def status(self) -> Mapping[str, object]:
        return {
            "analysis_ready": False,
            "complete_ready": False,
            "configured": False,
            "missing_complete_modalities": [
                "speech_transcription",
                "anonymous_diarization",
                "note_transcription",
                "instrument_diarization",
            ],
            "modalities": {
                "speech_transcription": False,
                "anonymous_diarization": False,
                "note_transcription": False,
                "instrument_detection": False,
                "instrument_diarization": False,
            },
            "network_used": False,
            "transcription_ready": False,
        }

    def execute(
        self,
        kind: WorkbenchJobKind,
        source_id: str,
        *,
        job_id: str,
        attempt: int,
        cancellation_requested: Callable[[], bool],
        report_progress: Callable[[int, str], None],
        completed_steps: frozenset[str],
        mark_step_completed: Callable[[str], None],
    ) -> None:
        raise WorkbenchProcessingError("local_runtime_not_configured")


def require_project_media(project_root: Path, source_id: str) -> None:
    source_identifier(source_id)
    payload = ProjectStore(project_root).load().payload
    matches = [item for item in payload["sources"] if item.get("id") == source_id]
    if len(matches) != 1:
        raise WorkbenchProcessingError("media_source_not_found")
    uri = matches[0].get("uri")
    relative = PurePosixPath(str(uri)) if isinstance(uri, str) else PurePosixPath()
    is_project_media = (
        not relative.is_absolute()
        and "\\" not in str(uri)
        and bool(relative.parts)
        and relative.parts[0] == "media"
        and len(relative.parts) == 2
        and not any(part in {"", ".", ".."} for part in relative.parts)
    )
    if not is_project_media:
        raise WorkbenchProcessingError("source_is_not_project_media")


def job_identifier(value: str) -> None:
    if not isinstance(value, str) or not value.startswith("job:workbench-") or len(value) > 128:
        raise WorkbenchProcessingError("invalid_job_id")


def source_identifier(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise WorkbenchProcessingError("invalid_source_id")


def status_message(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_STATUS_MESSAGE_CHARS:
        raise ValueError("status message must be bounded non-empty text")
    return value.strip()


def error_code(value: str) -> str:
    valid = (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 96
        and not any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in value)
    )
    return value if valid else "local_processing_failed"


def safe_exception_code(exc: Exception) -> str:
    name = type(exc).__name__
    converted = "".join(
        ("_" + character.lower()) if character.isupper() else character
        for character in name
    ).lstrip("_")
    return error_code(converted)


def required_steps(kind: WorkbenchJobKind) -> frozenset[str]:
    return {
        WorkbenchJobKind.TRANSCRIPTION: frozenset({"transcription"}),
        WorkbenchJobKind.ANALYSIS: frozenset({"analysis"}),
        WorkbenchJobKind.COMPLETE: frozenset({"transcription", "analysis"}),
    }[kind]


def cancelled_message(completed_steps: tuple[str, ...]) -> str:
    completed = frozenset(completed_steps)
    if completed == {"transcription"}:
        return "Cancelled after speech transcription; completed evidence remains ready for review"
    if completed == {"analysis"}:
        return "Cancelled after music analysis; completed evidence remains ready for review"
    if completed:
        return "Cancelled after a completed stage; saved evidence remains ready for review"
    return "Cancelled before publishing new evidence"


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
