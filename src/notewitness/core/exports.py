"""Loss-aware, rights-aware export contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ExportFormat(StrEnum):
    TEXT = "text"
    HTML = "html"
    WEBVTT = "webvtt"
    SRT = "srt"
    EAF = "eaf"
    QDPX = "qdpx"
    JAMS = "jams"
    MIDI = "midi"
    MUSICXML = "musicxml"
    MEI = "mei"
    MATCH = "match"
    JSON_LD = "json_ld"
    RO_CRATE = "ro_crate"


class LossSeverity(StrEnum):
    INFORMATIONAL = "informational"
    LOSSY = "lossy"
    BLOCKING = "blocking"


@dataclass(frozen=True, slots=True)
class ProjectionLoss:
    field: str
    reason: str
    severity: LossSeverity
    affected_record_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.field or not self.reason:
            raise ValueError("Projection losses require a field and reason.")
        if not isinstance(self.severity, LossSeverity):
            raise ValueError("Projection losses require a typed severity.")
        if not isinstance(self.affected_record_ids, tuple) or not self.affected_record_ids or any(
            not record_id for record_id in self.affected_record_ids
        ):
            raise ValueError("Projection losses require affected record IDs.")
        if len(self.affected_record_ids) != len(set(self.affected_record_ids)):
            raise ValueError("Projection-loss record IDs must be unique.")


@dataclass(frozen=True, slots=True)
class ExportPreflight:
    export_format: ExportFormat
    destination: str
    selected_record_ids: tuple[str, ...]
    rights_authorized: bool
    losses: tuple[ProjectionLoss, ...]
    loss_preview_acknowledged: bool

    def __post_init__(self) -> None:
        if not isinstance(self.export_format, ExportFormat):
            raise ValueError("Export preflight requires a typed format.")
        if not isinstance(self.destination, str) or not self.destination:
            raise ValueError("Export preflight requires a destination.")
        if not isinstance(self.selected_record_ids, tuple) or not self.selected_record_ids or any(
            not record_id for record_id in self.selected_record_ids
        ):
            raise ValueError("Export preflight requires selected record IDs.")
        if len(self.selected_record_ids) != len(set(self.selected_record_ids)):
            raise ValueError("Export preflight record IDs must be unique.")
        if not isinstance(self.losses, tuple) or any(
            not isinstance(loss, ProjectionLoss) for loss in self.losses
        ):
            raise ValueError("Export preflight losses must be typed records.")
        if not isinstance(self.rights_authorized, bool) or not isinstance(
            self.loss_preview_acknowledged, bool
        ):
            raise ValueError("Export authorization decisions must be booleans.")

    @property
    def executable(self) -> bool:
        return bool(
            self.destination
            and self.rights_authorized
            and self.loss_preview_acknowledged
            and not any(loss.severity is LossSeverity.BLOCKING for loss in self.losses)
        )
