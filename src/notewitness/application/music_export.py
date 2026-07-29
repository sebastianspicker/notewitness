"""Deterministic, local-only exports of reviewable symbolic-note evidence."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import io
import json
import math
from pathlib import Path
import struct
from typing import Any, Iterable, Mapping

from notewitness.domain.interop import LossSeverity, ProjectionLoss
from notewitness.local_artifacts import write_new_private_bytes
from notewitness.project_store import ProjectStore


MAX_EXPORTED_NOTES = 10_000
_TICKS_PER_QUARTER = 1_000
_US_PER_QUARTER = 1_000_000
_EXPORTABLE_STATUSES = frozenset({"machine_suggested", "human_accepted", "human_created"})


class MusicExportError(RuntimeError):
    """A requested symbolic export is unsafe or cannot be projected faithfully."""


class MusicExportFormat(StrEnum):
    CSV = "csv"
    MIDI = "midi"


@dataclass(frozen=True, slots=True)
class SymbolicNote:
    event_id: str
    target_id: str
    source_id: str
    stream_id: str
    start_us: int
    duration_us: int
    midi_pitch: float
    frequency_hz: float | None
    amplitude: float | None
    velocity: int | None
    pitch_bend_values: tuple[float, ...]
    pitch_bend_unit: str | None
    instrument_track_id: str | None
    review_status: str


@dataclass(frozen=True, slots=True)
class MusicExportPreflight:
    export_format: MusicExportFormat
    destination: str
    notes: tuple[SymbolicNote, ...]
    source_ids: tuple[str, ...]
    rights_authorized: bool
    loss_preview_acknowledged: bool
    losses: tuple[ProjectionLoss, ...]

    @property
    def executable(self) -> bool:
        return bool(self.notes and self.rights_authorized and self.loss_preview_acknowledged)


@dataclass(frozen=True, slots=True)
class MusicExportResult:
    export_format: MusicExportFormat
    path: str
    record_count: int
    source_ids: tuple[str, ...]
    checksum_sha256: str
    documented_losses: tuple[ProjectionLoss, ...]


class SymbolicMusicExportService:
    """Projects validated project evidence into private, new-only CSV or SMF files."""

    def __init__(self, project_store: ProjectStore) -> None:
        self._store = project_store

    @classmethod
    def for_project(cls, project_root: str | Path) -> "SymbolicMusicExportService":
        return cls(ProjectStore(project_root))

    def preflight(
        self,
        *,
        export_format: MusicExportFormat | str,
        filename: str,
        rights_authorized: bool,
        loss_preview_acknowledged: bool,
        source_id: str | None = None,
    ) -> MusicExportPreflight:
        format_value = _coerce_format(export_format)
        destination = _destination(self._store.ensure_private_directory("exports"), filename, format_value)
        payload = self._store.load().payload
        selected_source_id = _source_filter(payload, source_id)
        notes = _extract_notes(payload, selected_source_id)
        source_ids = tuple(sorted({note.source_id for note in notes}))
        if format_value is MusicExportFormat.MIDI and len(source_ids) > 1:
            raise MusicExportError(
                "MIDI export requires one explicit source when notes span multiple recordings."
            )
        losses = _projection_losses(notes, format_value)
        return MusicExportPreflight(
            export_format=format_value,
            destination=str(destination),
            notes=notes,
            source_ids=source_ids,
            rights_authorized=_require_bool(rights_authorized, "rights_authorized"),
            loss_preview_acknowledged=_require_bool(
                loss_preview_acknowledged, "loss_preview_acknowledged"
            ),
            losses=losses,
        )

    def export(
        self,
        *,
        export_format: MusicExportFormat | str,
        filename: str,
        rights_authorized: bool,
        loss_preview_acknowledged: bool,
        source_id: str | None = None,
    ) -> MusicExportResult:
        preflight = self.preflight(
            export_format=export_format,
            filename=filename,
            rights_authorized=rights_authorized,
            loss_preview_acknowledged=loss_preview_acknowledged,
            source_id=source_id,
        )
        if not preflight.rights_authorized:
            raise MusicExportError("Export requires explicit rights authorization.")
        if not preflight.loss_preview_acknowledged:
            raise MusicExportError("Export requires explicit acknowledgement of projection losses.")
        if not preflight.notes:
            raise MusicExportError("No reviewable or accepted local:note evidence is available.")
        contents = (
            _csv_bytes(preflight.notes)
            if preflight.export_format is MusicExportFormat.CSV
            else _midi_bytes(preflight.notes)
        )
        published = write_new_private_bytes(preflight.destination, contents)
        return MusicExportResult(
            export_format=preflight.export_format,
            path=str(published),
            record_count=len(preflight.notes),
            source_ids=preflight.source_ids,
            checksum_sha256=hashlib.sha256(contents).hexdigest(),
            documented_losses=preflight.losses,
        )


def _coerce_format(value: MusicExportFormat | str) -> MusicExportFormat:
    try:
        return MusicExportFormat(value)
    except ValueError as exc:
        raise MusicExportError("Symbolic export format must be 'csv' or 'midi'.") from exc


def _require_bool(value: bool, label: str) -> bool:
    if not isinstance(value, bool):
        raise MusicExportError(f"{label} must be an explicit boolean decision.")
    return value


def _destination(exports: Path, filename: str, export_format: MusicExportFormat) -> Path:
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise MusicExportError("Export filename must be a non-empty basename.")
    suffix = ".csv" if export_format is MusicExportFormat.CSV else ".mid"
    if not filename.endswith(suffix):
        raise MusicExportError(f"{export_format.value} exports must use the {suffix} suffix.")
    return exports / filename


def _source_filter(payload: Mapping[str, Any], source_id: str | None) -> str | None:
    if source_id is None:
        return None
    if not isinstance(source_id, str) or not source_id or len(source_id) > 512:
        raise MusicExportError("Export source_id must be a bounded non-empty string.")
    if not any(
        isinstance(item, Mapping) and item.get("id") == source_id
        for item in payload.get("sources", ())
    ):
        raise MusicExportError("The selected export source does not exist.")
    return source_id


def _extract_notes(
    payload: Mapping[str, Any], selected_source_id: str | None
) -> tuple[SymbolicNote, ...]:
    targets = _targets_by_id(payload)
    events = tuple(item for item in payload.get("events", ()) if isinstance(item, Mapping))
    accepted_sources = _accepted_note_suggestion_ids(events)
    notes = [
        note
        for event in events
        for note in _event_notes(event, targets, accepted_sources, selected_source_id)
    ]
    if len(notes) > MAX_EXPORTED_NOTES:
        raise MusicExportError(f"Symbolic export is limited to {MAX_EXPORTED_NOTES} notes.")
    return tuple(sorted(notes, key=_note_sort_key))


def _targets_by_id(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        item["id"]: item
        for item in payload.get("targets", ())
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }


def _accepted_note_suggestion_ids(events: Iterable[Mapping[str, Any]]) -> set[str]:
    return {
        body["source_suggestion_id"]
        for event in events
        if event.get("type") == "local:note"
        and event.get("review_status") == "human_accepted"
        and isinstance((body := event.get("body")), Mapping)
        and isinstance(body.get("source_suggestion_id"), str)
    }


def _event_notes(
    event: Mapping[str, Any],
    targets: Mapping[str, Mapping[str, Any]],
    accepted_sources: set[str],
    selected_source_id: str | None,
) -> tuple[SymbolicNote, ...]:
    if not _is_exportable_note(event, accepted_sources):
        return ()
    event_id, value, status = _note_parts(event)
    return tuple(
        note
        for target_id in event.get("target_ids", ())
        if isinstance(target_id, str)
        if (
            note := _note_from_target(
                event_id, status, value, target_id, targets, selected_source_id
            )
        ) is not None
    )


def _is_exportable_note(event: Mapping[str, Any], accepted_sources: set[str]) -> bool:
    status = event.get("review_status")
    return (
        event.get("type") == "local:note"
        and status in _EXPORTABLE_STATUSES
        and not (status == "machine_suggested" and event.get("id") in accepted_sources)
    )


def _note_parts(event: Mapping[str, Any]) -> tuple[str, Mapping[str, Any], str]:
    event_id, body, status = event.get("id"), event.get("body"), event.get("review_status")
    if not isinstance(event_id, str) or not isinstance(body, Mapping):
        raise MusicExportError("local:note evidence must have an ID and structured body.")
    value = body.get("value")
    pitch = value.get("midi_pitch") if isinstance(value, Mapping) else None
    if not _valid_midi_pitch(pitch):
        raise MusicExportError(f"Note {event_id!r} has an invalid MIDI pitch.")
    return event_id, value, str(status)


def _valid_midi_pitch(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= 127
    )


def _note_from_target(
    event_id: str,
    review_status: str,
    value: Mapping[str, Any],
    target_id: str,
    targets: Mapping[str, Mapping[str, Any]],
    selected_source_id: str | None,
) -> SymbolicNote | None:
    target = targets.get(target_id)
    if target is None:
        raise MusicExportError(f"Note {event_id!r} targets no source span.")
    source_id, stream_id, start_us, duration_us = _target_span(event_id, target_id, target)
    if selected_source_id is not None and source_id != selected_source_id:
        return None
    bends, bend_unit = _pitch_bends(value)
    return SymbolicNote(
        event_id, target_id, source_id, stream_id, start_us, duration_us,
        float(value["midi_pitch"]),
        _optional_finite_number(value, "frequency_hz", minimum=0.0, inclusive=False),
        _optional_finite_number(value, "amplitude", minimum=0.0, maximum=1.0),
        _optional_velocity(value), bends, bend_unit, _track_id(value, target), review_status,
    )


def _target_span(
    event_id: str, target_id: str, target: Mapping[str, Any]
) -> tuple[str, str, int, int]:
    selector = target.get("selector")
    if not isinstance(selector, Mapping):
        raise MusicExportError(f"Note {event_id!r} target {target_id!r} has no source span.")
    start_us, duration_us = selector.get("start_us"), selector.get("duration_us")
    if not _valid_timing(start_us, duration_us):
        raise MusicExportError(f"Note {event_id!r} target {target_id!r} has invalid timing.")
    source_id, stream_id = target.get("source_id"), selector.get("stream_id")
    if not isinstance(source_id, str) or not isinstance(stream_id, str) or not stream_id:
        raise MusicExportError(f"Note {event_id!r} target {target_id!r} has invalid source span.")
    return source_id, stream_id, start_us, duration_us


def _valid_timing(start_us: Any, duration_us: Any) -> bool:
    return (
        isinstance(start_us, int)
        and not isinstance(start_us, bool)
        and isinstance(duration_us, int)
        and not isinstance(duration_us, bool)
        and start_us >= 0
        and duration_us > 0
    )


def _note_sort_key(note: SymbolicNote) -> tuple[int, str, float, str, str]:
    return (
        note.start_us, note.instrument_track_id or "", note.midi_pitch,
        note.event_id, note.target_id,
    )


def _optional_finite_number(
    value: Mapping[str, Any] | Any,
    key: str,
    *,
    minimum: float,
    maximum: float | None = None,
    inclusive: bool = True,
) -> float | None:
    candidate = value.get(key) if isinstance(value, Mapping) else None
    if candidate is None:
        return None
    if not _finite_number(candidate):
        raise MusicExportError(f"Note evidence has an invalid {key} value.")
    if not _within_bounds(candidate, minimum, maximum, inclusive):
        raise MusicExportError(f"Note evidence has an invalid {key} value.")
    return float(candidate)


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _within_bounds(
    value: float, minimum: float, maximum: float | None, inclusive: bool
) -> bool:
    return (value >= minimum if inclusive else value > minimum) and (
        maximum is None or value <= maximum
    )


def _optional_velocity(value: Mapping[str, Any] | Any) -> int | None:
    candidate = value.get("velocity") if isinstance(value, Mapping) else None
    if candidate is None:
        return None
    if not isinstance(candidate, int) or isinstance(candidate, bool) or not 0 <= candidate <= 127:
        raise MusicExportError("Note evidence has an invalid velocity value.")
    return candidate


def _pitch_bends(value: Mapping[str, Any] | Any) -> tuple[tuple[float, ...], str | None]:
    raw = value.get("pitch_bend_values") if isinstance(value, Mapping) else None
    unit = value.get("pitch_bend_unit") if isinstance(value, Mapping) else None
    if raw is None:
        return _absent_pitch_bends(unit)
    if not _valid_pitch_bends(raw, unit):
        raise MusicExportError("Note evidence has invalid pitch-bend values or units.")
    return tuple(float(item) for item in raw), unit


def _absent_pitch_bends(unit: Any) -> tuple[tuple[float, ...], None]:
    if unit is not None:
        raise MusicExportError("Note evidence has a pitch-bend unit without values.")
    return (), None


def _valid_pitch_bends(raw: Any, unit: Any) -> bool:
    return (
        isinstance(raw, list)
        and bool(raw)
        and len(raw) <= 100_000
        and all(_finite_number(item) for item in raw)
        and isinstance(unit, str)
        and bool(unit)
    )


def _track_id(value: Mapping[str, Any] | Any, target: Mapping[str, Any]) -> str | None:
    candidates = (
        value.get("source_track_id") if isinstance(value, Mapping) else None,
        value.get("instrument_track_id") if isinstance(value, Mapping) else None,
        target.get("instrument_track_id"),
        target.get("musical_selector", {}).get("instrument_track_id") if isinstance(target.get("musical_selector"), Mapping) else None,
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _projection_losses(notes: tuple[SymbolicNote, ...], export_format: MusicExportFormat) -> tuple[ProjectionLoss, ...]:
    if export_format is MusicExportFormat.CSV or not notes:
        return ()
    ids = _unique_event_ids(notes)
    losses = [_provenance_loss(ids)]
    for field, reason, affected in _conditional_losses(notes):
        if affected:
            losses.append(ProjectionLoss(field, reason, LossSeverity.LOSSY, affected))
    return tuple(losses)


def _provenance_loss(ids: tuple[str, ...]) -> ProjectionLoss:
    return ProjectionLoss(
        "source_span_provenance",
        "Standard MIDI events cannot retain source, target, review, or rights provenance.",
        LossSeverity.LOSSY,
        ids,
    )


def _conditional_losses(
    notes: tuple[SymbolicNote, ...],
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    return (
        ("midi_pitch", "Standard MIDI 1.0 note numbers are integers; fractional pitches are rounded.", _unique_event_ids(note for note in notes if not note.midi_pitch.is_integer())),
        ("timing", "Standard MIDI timing is quantized to one millisecond in this deterministic projection.", _unique_event_ids(note for note in notes if note.start_us % 1_000 or note.duration_us % 1_000)),
        ("amplitude", "Provider amplitude is not equivalent to MIDI velocity and is omitted from this projection.", _unique_event_ids(note for note in notes if note.amplitude is not None)),
        ("pitch_bends", "Per-note pitch-bend curves require an explicit channel/range policy and are omitted from this MIDI projection.", _unique_event_ids(note for note in notes if note.pitch_bend_values)),
        ("overlapping_same_pitch", "Overlapping notes that round to the same MIDI pitch on one source track are merged to avoid premature note-off playback.", _overlapping_same_pitch_ids(notes)),
    )


def _unique_event_ids(notes: Iterable[SymbolicNote]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(note.event_id for note in notes))


def _csv_bytes(notes: tuple[SymbolicNote, ...]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("event_id", "target_id", "source_id", "stream_id", "start_us", "duration_us", "midi_pitch", "frequency_hz", "amplitude", "velocity", "pitch_bend_unit", "pitch_bend_values", "instrument_track_id", "review_status"))
    for note in notes:
        writer.writerow((note.event_id, note.target_id, note.source_id, note.stream_id, note.start_us, note.duration_us, _pitch_text(note.midi_pitch), _optional_number_text(note.frequency_hz), _optional_number_text(note.amplitude), "" if note.velocity is None else note.velocity, note.pitch_bend_unit or "", json.dumps(note.pitch_bend_values, separators=(",", ":")) if note.pitch_bend_values else "", note.instrument_track_id or "", note.review_status))
    return output.getvalue().encode("utf-8")


def _pitch_text(pitch: float) -> str:
    return str(int(pitch)) if pitch.is_integer() else format(pitch, ".12g")


def _optional_number_text(value: float | None) -> str:
    return "" if value is None else _pitch_text(value)


def _midi_bytes(notes: tuple[SymbolicNote, ...]) -> bytes:
    groups: dict[tuple[str, str], list[SymbolicNote]] = {}
    for note in notes:
        groups.setdefault(_midi_group_key(note), []).append(note)
    tempo = b"\x00\xff\x51\x03" + _US_PER_QUARTER.to_bytes(3, "big") + b"\x00\xff\x2f\x00"
    tracks = [tempo] + [
        _midi_track(f"{key[0]} | {key[1]}", groups[key]) for key in sorted(groups)
    ]
    header = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), _TICKS_PER_QUARTER)
    return header + b"".join(b"MTrk" + struct.pack(">I", len(track)) + track for track in tracks)


def _midi_track(track_id: str, notes: list[SymbolicNote]) -> bytes:
    events: list[tuple[int, int, int, int, bool]] = []
    for start, end, pitch, velocity in _merged_midi_notes(notes):
        events.extend(((start, 1, pitch, velocity, True), (end, 0, pitch, 0, False)))
    previous = 0
    raw = bytearray()
    track_name = track_id.encode("utf-8")
    raw.extend(b"\x00\xff\x03" + _vlq(len(track_name)) + track_name)
    for moment, _order, pitch, velocity, on in sorted(events):
        raw.extend(_vlq(moment - previous))
        raw.extend((0x90 if on else 0x80, pitch, velocity))
        previous = moment
    raw.extend(b"\x00\xff\x2f\x00")
    return bytes(raw)


def _midi_group_key(note: SymbolicNote) -> tuple[str, str]:
    return (note.source_id, note.instrument_track_id or "track:unassigned")


def _rounded_midi_pitch(note: SymbolicNote) -> int:
    return int(math.floor(note.midi_pitch + 0.5))


def _merged_midi_notes(
    notes: Iterable[SymbolicNote],
) -> tuple[tuple[int, int, int, int], ...]:
    by_pitch: dict[int, list[tuple[int, int, int]]] = {}
    for note in notes:
        start = _rounded_tick(note.start_us)
        end = max(start + 1, _rounded_tick(note.start_us + note.duration_us))
        by_pitch.setdefault(_rounded_midi_pitch(note), []).append(
            (start, end, note.velocity if note.velocity is not None else 96)
        )
    merged: list[tuple[int, int, int, int]] = []
    for pitch, intervals in by_pitch.items():
        current: list[int] | None = None
        for start, end, velocity in sorted(intervals):
            if current is not None and start < current[1]:
                current[1] = max(current[1], end)
                current[2] = max(current[2], velocity)
                continue
            if current is not None:
                merged.append((current[0], current[1], pitch, current[2]))
            current = [start, end, velocity]
        if current is not None:
            merged.append((current[0], current[1], pitch, current[2]))
    return tuple(sorted(merged))


def _overlapping_same_pitch_ids(
    notes: Iterable[SymbolicNote],
) -> tuple[str, ...]:
    grouped: dict[tuple[str, str, int], list[SymbolicNote]] = {}
    for note in notes:
        source_id, track_id = _midi_group_key(note)
        grouped.setdefault(
            (source_id, track_id, _rounded_midi_pitch(note)), []
        ).append(note)
    affected: list[str] = []
    for candidates in grouped.values():
        active_end = -1
        active_ids: list[str] = []
        for note in sorted(
            candidates,
            key=lambda item: (item.start_us, item.duration_us, item.event_id),
        ):
            end = note.start_us + note.duration_us
            if note.start_us < active_end:
                affected.extend(active_ids)
                affected.append(note.event_id)
                active_ids.append(note.event_id)
                active_end = max(active_end, end)
            else:
                active_ids = [note.event_id]
                active_end = end
    return tuple(dict.fromkeys(affected))


def _rounded_tick(value_us: int) -> int:
    return (value_us + 500) // 1_000


def _vlq(value: int) -> bytes:
    if value < 0:
        raise MusicExportError("MIDI events must be ordered by non-negative time.")
    chunks = [value & 0x7F]
    while value > 0x7F:
        value >>= 7
        chunks.append(0x80 | (value & 0x7F))
    return bytes(reversed(chunks))
