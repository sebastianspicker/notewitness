"""Byte renderers for deterministic symbolic-note exports."""

from __future__ import annotations

import csv
import io
import json
import math
import struct
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from notewitness.application.music_export import SymbolicNote


_TICKS_PER_QUARTER = 1_000
_US_PER_QUARTER = 1_000_000


def csv_bytes(notes: tuple[SymbolicNote, ...]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        (
            "event_id",
            "target_id",
            "source_id",
            "stream_id",
            "start_us",
            "duration_us",
            "midi_pitch",
            "frequency_hz",
            "amplitude",
            "velocity",
            "pitch_bend_unit",
            "pitch_bend_values",
            "instrument_track_id",
            "review_status",
        )
    )
    for note in notes:
        writer.writerow(
            (
                note.event_id,
                note.target_id,
                note.source_id,
                note.stream_id,
                note.start_us,
                note.duration_us,
                _pitch_text(note.midi_pitch),
                _optional_number_text(note.frequency_hz),
                _optional_number_text(note.amplitude),
                "" if note.velocity is None else note.velocity,
                note.pitch_bend_unit or "",
                json.dumps(note.pitch_bend_values, separators=(",", ":"))
                if note.pitch_bend_values
                else "",
                note.instrument_track_id or "",
                note.review_status,
            )
        )
    return output.getvalue().encode("utf-8")


def _pitch_text(pitch: float) -> str:
    return str(int(pitch)) if pitch.is_integer() else format(pitch, ".12g")


def _optional_number_text(value: float | None) -> str:
    return "" if value is None else _pitch_text(value)


def midi_bytes(notes: tuple[SymbolicNote, ...]) -> bytes:
    groups: dict[tuple[str, str], list[SymbolicNote]] = {}
    for note in notes:
        groups.setdefault(_midi_group_key(note), []).append(note)
    tempo = (
        b"\x00\xff\x51\x03"
        + _US_PER_QUARTER.to_bytes(3, "big")
        + b"\x00\xff\x2f\x00"
    )
    tracks = [tempo] + [
        _midi_track(f"{key[0]} | {key[1]}", groups[key]) for key in sorted(groups)
    ]
    header = b"MThd" + struct.pack(
        ">IHHH", 6, 1, len(tracks), _TICKS_PER_QUARTER
    )
    return header + b"".join(
        b"MTrk" + struct.pack(">I", len(track)) + track for track in tracks
    )


def _midi_track(track_id: str, notes: list[SymbolicNote]) -> bytes:
    events: list[tuple[int, int, int, int, bool]] = []
    for start, end, pitch, velocity in merged_midi_notes(notes):
        events.extend(
            ((start, 1, pitch, velocity, True), (end, 0, pitch, 0, False))
        )
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


def merged_midi_notes(
    notes: Iterable[SymbolicNote],
) -> tuple[tuple[int, int, int, int], ...]:
    merged = [
        interval
        for pitch, intervals in _midi_intervals_by_pitch(notes).items()
        for interval in _merge_pitch_intervals(pitch, intervals)
    ]
    return tuple(sorted(merged))


def _midi_intervals_by_pitch(
    notes: Iterable[SymbolicNote],
) -> dict[int, list[tuple[int, int, int]]]:
    by_pitch: dict[int, list[tuple[int, int, int]]] = {}
    for note in notes:
        start = _rounded_tick(note.start_us)
        end = max(start + 1, _rounded_tick(note.start_us + note.duration_us))
        by_pitch.setdefault(_rounded_midi_pitch(note), []).append(
            (start, end, note.velocity if note.velocity is not None else 96)
        )
    return by_pitch


def _merge_pitch_intervals(
    pitch: int,
    intervals: Iterable[tuple[int, int, int]],
) -> tuple[tuple[int, int, int, int], ...]:
    merged: list[tuple[int, int, int, int]] = []
    current: list[int] | None = None
    for start, end, velocity in sorted(intervals):
        if current is None:
            current = [start, end, velocity]
            continue
        if start < current[1]:
            current[1] = max(current[1], end)
            current[2] = max(current[2], velocity)
            continue
        merged.append((current[0], current[1], pitch, current[2]))
        current = [start, end, velocity]
    if current is not None:
        merged.append((current[0], current[1], pitch, current[2]))
    return tuple(merged)


def overlapping_same_pitch_ids(
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
        from notewitness.application.music_export import MusicExportError

        raise MusicExportError("MIDI events must be ordered by non-negative time.")
    chunks = [value & 0x7F]
    while value > 0x7F:
        value >>= 7
        chunks.append(0x80 | (value & 0x7F))
    return bytes(reversed(chunks))
