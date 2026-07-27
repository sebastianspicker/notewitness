"""Local capture records without binding the core to an audio framework."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
import re


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CaptureState(StrEnum):
    IDLE = "idle"
    RECORDING = "recording"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CaptureRequest:
    session_id: str
    destination: str
    rights_id: str
    audio_input_id: str | None = None
    video_input_id: str | None = None

    def __post_init__(self) -> None:
        if not self.session_id or not self.destination or not self.rights_id:
            raise ValueError("Capture requests require session, destination, and rights IDs.")
        if self.audio_input_id is None and self.video_input_id is None:
            raise ValueError("Capture requests require at least one explicitly selected input.")
        destination = PurePosixPath(self.destination)
        if (
            destination.is_absolute()
            or "\\" in self.destination
            or not destination.parts
            or destination.parts[0] != "media"
            or any(part in {"", ".", ".."} for part in destination.parts)
        ):
            raise ValueError(
                "Capture destination must be a project-relative media object path."
            )


@dataclass(frozen=True, slots=True)
class CaptureHandle:
    session_id: str
    backend_token: str


@dataclass(frozen=True, slots=True)
class CapturedMedia:
    session_id: str
    path: str
    sha256: str
    duration_us: int
    byte_count: int
    rights_id: str

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.sha256):
            raise ValueError("Captured media require a lowercase SHA-256 digest.")
        for value, name in (
            (self.duration_us, "duration_us"),
            (self.byte_count, "byte_count"),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
        destination = PurePosixPath(self.path)
        if destination.is_absolute() or any(
            part in {"", ".", ".."} for part in destination.parts
        ):
            raise ValueError("Captured media path must remain project-relative.")
        if not self.rights_id:
            raise ValueError("Captured media require a rights ID.")
