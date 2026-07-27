"""Offline metronome scheduling and tuner calculations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
import math


MAX_METRONOME_TICKS = 10_000
MAX_TIMELINE_US = 2**63 - 1
_NOTE_NAMES = ("C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B")


class TuningDirection(StrEnum):
    FLAT = "flat"
    IN_TUNE = "in_tune"
    SHARP = "sharp"


class MetronomeAccent(StrEnum):
    BAR = "bar"
    BEAT = "beat"
    SUBDIVISION = "subdivision"


@dataclass(frozen=True, slots=True)
class TunerReading:
    frequency_hz: float
    reference_hz: float
    midi_note: int
    note_name: str
    octave: int
    cents_offset: float
    direction: TuningDirection


def tuner_reading(
    frequency_hz: float,
    *,
    a4_hz: float = 440.0,
    in_tune_cents: float = 5.0,
) -> TunerReading:
    """Map a local pitch estimate to the nearest equal-tempered note."""

    for value, field_name in ((frequency_hz, "frequency_hz"), (a4_hz, "a4_hz")):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field_name} must be a finite positive number.")
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{field_name} must be a finite positive number.")
    if not 300.0 <= a4_hz <= 500.0:
        raise ValueError("a4_hz must be in the supported range [300, 500].")
    if (
        isinstance(in_tune_cents, bool)
        or not isinstance(in_tune_cents, (int, float))
        or not math.isfinite(in_tune_cents)
        or not 0.0 < in_tune_cents <= 50.0
    ):
        raise ValueError("in_tune_cents must be in the range (0, 50].")

    continuous_midi = 69.0 + 12.0 * (
        math.log2(frequency_hz) - math.log2(a4_hz)
    )
    if not -0.5 <= continuous_midi < 127.5:
        raise ValueError("frequency_hz is outside the supported MIDI 0-127 range.")
    midi_note = math.floor(continuous_midi + 0.5)
    reference_hz = a4_hz * (2.0 ** ((midi_note - 69) / 12.0))
    cents_offset = (continuous_midi - midi_note) * 100.0
    if cents_offset < -in_tune_cents:
        direction = TuningDirection.FLAT
    elif cents_offset > in_tune_cents:
        direction = TuningDirection.SHARP
    else:
        direction = TuningDirection.IN_TUNE
    return TunerReading(
        frequency_hz=float(frequency_hz),
        reference_hz=reference_hz,
        midi_note=midi_note,
        note_name=_NOTE_NAMES[midi_note % 12],
        octave=(midi_note // 12) - 1,
        cents_offset=cents_offset,
        direction=direction,
    )


@dataclass(frozen=True, slots=True)
class MetronomeTick:
    index: int
    at_us: int
    bar: int
    beat: int
    subdivision: int
    accent: MetronomeAccent


@dataclass(frozen=True, slots=True)
class MetronomePlan:
    bpm: float
    beats_per_bar: int = 4
    subdivisions_per_beat: int = 1

    def __post_init__(self) -> None:
        if (
            isinstance(self.bpm, bool)
            or not isinstance(self.bpm, (int, float))
            or not math.isfinite(self.bpm)
            or not 20.0 <= self.bpm <= 400.0
        ):
            raise ValueError("bpm must be a finite value from 20 through 400.")
        for value, field_name, maximum in (
            (self.beats_per_bar, "beats_per_bar", 32),
            (self.subdivisions_per_beat, "subdivisions_per_beat", 16),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 1 <= value <= maximum
            ):
                raise ValueError(f"{field_name} must be an integer from 1 through {maximum}.")

    def schedule(self, bars: int, *, start_us: int = 0) -> tuple[MetronomeTick, ...]:
        if not isinstance(bars, int) or isinstance(bars, bool) or bars < 1:
            raise ValueError("bars must be a positive integer.")
        if (
            not isinstance(start_us, int)
            or isinstance(start_us, bool)
            or not 0 <= start_us <= MAX_TIMELINE_US
        ):
            raise ValueError("start_us must fit the canonical non-negative timeline.")
        tick_count = bars * self.beats_per_bar * self.subdivisions_per_beat
        if tick_count > MAX_METRONOME_TICKS:
            raise ValueError(
                f"A metronome schedule is limited to {MAX_METRONOME_TICKS} ticks."
            )
        interval_us = (
            Decimal(60_000_000)
            / Decimal(str(self.bpm))
            / Decimal(self.subdivisions_per_beat)
        )
        final_offset = int(
            (interval_us * (tick_count - 1)).to_integral_value(
                rounding=ROUND_HALF_UP
            )
        )
        if start_us + final_offset > MAX_TIMELINE_US:
            raise ValueError("Metronome schedule exceeds the canonical timeline.")
        ticks: list[MetronomeTick] = []
        ticks_per_bar = self.beats_per_bar * self.subdivisions_per_beat
        for index in range(tick_count):
            position = index % ticks_per_bar
            beat = position // self.subdivisions_per_beat
            subdivision = position % self.subdivisions_per_beat
            if position == 0:
                accent = MetronomeAccent.BAR
            elif subdivision == 0:
                accent = MetronomeAccent.BEAT
            else:
                accent = MetronomeAccent.SUBDIVISION
            offset = int(
                (interval_us * index).to_integral_value(rounding=ROUND_HALF_UP)
            )
            ticks.append(
                MetronomeTick(
                    index=index,
                    at_us=start_us + offset,
                    bar=(index // ticks_per_bar) + 1,
                    beat=beat + 1,
                    subdivision=subdivision + 1,
                    accent=accent,
                )
            )
        return tuple(ticks)
