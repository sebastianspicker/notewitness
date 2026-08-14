from __future__ import annotations

import math
import re
import unittest

from notewitness.domain.utilities import (
    MetronomeAccent,
    MetronomePlan,
    TuningDirection,
    tuner_reading,
)


class OfflineUtilityTests(unittest.TestCase):
    def test_tuner_maps_frequency_to_note_and_cents(self) -> None:
        concert_a = tuner_reading(440.0)
        sharp_a = tuner_reading(445.0)

        self.assertEqual("A", concert_a.note_name)
        self.assertEqual(4, concert_a.octave)
        self.assertAlmostEqual(0.0, concert_a.cents_offset)
        self.assertEqual(TuningDirection.IN_TUNE, concert_a.direction)
        self.assertEqual(TuningDirection.SHARP, sharp_a.direction)

        custom = tuner_reading(442.0, a4_hz=442.0)
        self.assertEqual(TuningDirection.IN_TUNE, custom.direction)

    def test_tuner_rejects_extreme_or_meaningless_inputs(self) -> None:
        for invalid in (5e-324, float("inf"), float("1.7976931348623157e308")):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    tuner_reading(invalid)
        with self.assertRaises(ValueError):
            tuner_reading(440.0, in_tune_cents=100.0)

    def test_tuner_validation_rejects_booleans_and_nonfinite_values_in_order(self) -> None:
        cases = (
            ((True,), {}, "frequency_hz must be a finite positive number."),
            ((float("nan"),), {}, "frequency_hz must be a finite positive number."),
            ((440.0,), {"a4_hz": True}, "a4_hz must be a finite positive number."),
            (
                (440.0,),
                {"a4_hz": float("inf")},
                "a4_hz must be a finite positive number.",
            ),
            (
                (440.0,),
                {"a4_hz": 299.0},
                "a4_hz must be in the supported range [300, 500].",
            ),
            (
                (440.0,),
                {"in_tune_cents": True},
                "in_tune_cents must be in the range (0, 50].",
            ),
            (
                (440.0,),
                {"in_tune_cents": float("nan")},
                "in_tune_cents must be in the range (0, 50].",
            ),
        )
        for args, kwargs, message in cases:
            with self.subTest(args=args, kwargs=kwargs):
                with self.assertRaisesRegex(ValueError, f"^{re.escape(message)}$"):
                    tuner_reading(*args, **kwargs)

        with self.assertRaisesRegex(
            ValueError, "^frequency_hz must be a finite positive number\\.$"
        ):
            tuner_reading(True, a4_hz=True, in_tune_cents=True)

    def test_tuner_preserves_threshold_direction_and_midi_boundaries(self) -> None:
        for cents, expected in (
            (-10.0, TuningDirection.FLAT),
            (10.0, TuningDirection.SHARP),
        ):
            frequency = 440.0 * 2.0 ** (cents / 1200.0)
            measured = tuner_reading(frequency)
            threshold = abs(measured.cents_offset)
            with self.subTest(cents=cents, boundary="equal"):
                self.assertEqual(
                    TuningDirection.IN_TUNE,
                    tuner_reading(frequency, in_tune_cents=threshold).direction,
                )
            with self.subTest(cents=cents, boundary="beyond"):
                self.assertEqual(
                    expected,
                    tuner_reading(
                        frequency,
                        in_tune_cents=math.nextafter(threshold, 0.0),
                    ).direction,
                )

        lowest = tuner_reading(440.0 * 2.0 ** (-69.0 / 12.0))
        highest = tuner_reading(440.0 * 2.0 ** (58.0 / 12.0))
        self.assertEqual(0, lowest.midi_note)
        self.assertEqual(127, highest.midi_note)
        with self.assertRaisesRegex(
            ValueError, "^frequency_hz is outside the supported MIDI 0-127 range\\.$"
        ):
            tuner_reading(440.0 * 2.0 ** (59.0 / 12.0))

    def test_metronome_schedule_is_bounded_and_drift_resistant(self) -> None:
        ticks = MetronomePlan(
            bpm=120.0, beats_per_bar=4, subdivisions_per_beat=2
        ).schedule(2)

        self.assertEqual(16, len(ticks))
        self.assertEqual(0, ticks[0].at_us)
        self.assertEqual(250_000, ticks[1].at_us)
        self.assertEqual(2_000_000, ticks[8].at_us)
        self.assertEqual(MetronomeAccent.BAR, ticks[0].accent)
        self.assertEqual(MetronomeAccent.SUBDIVISION, ticks[1].accent)

        with self.assertRaises(ValueError):
            MetronomePlan(120).schedule(10_001)
        with self.assertRaises(ValueError):
            MetronomePlan(20).schedule(1, start_us=2**63 - 1)

    def test_metronome_validation_and_fractional_timing_are_exact(self) -> None:
        for bpm in (True, float("nan"), float("inf"), 19.0, 401.0):
            with self.subTest(field="bpm", invalid=bpm):
                with self.assertRaisesRegex(
                    ValueError, "^bpm must be a finite value from 20 through 400\\.$"
                ):
                    MetronomePlan(bpm)
        for field_name, kwargs, message in (
            (
                "beats_per_bar",
                {"beats_per_bar": True},
                "beats_per_bar must be an integer from 1 through 32.",
            ),
            (
                "subdivisions_per_beat",
                {"subdivisions_per_beat": True},
                "subdivisions_per_beat must be an integer from 1 through 16.",
            ),
        ):
            with self.subTest(field=field_name):
                with self.assertRaisesRegex(ValueError, f"^{re.escape(message)}$"):
                    MetronomePlan(120.0, **kwargs)

        plan = MetronomePlan(123.45, beats_per_bar=2, subdivisions_per_beat=3)
        ticks = plan.schedule(2, start_us=17)
        self.assertEqual(
            [
                17,
                162_026,
                324_035,
                486_044,
                648_053,
                810_062,
                972_070,
                1_134_079,
                1_296_088,
                1_458_097,
                1_620_106,
                1_782_115,
            ],
            [tick.at_us for tick in ticks],
        )
        self.assertEqual(
            [
                MetronomeAccent.BAR,
                MetronomeAccent.SUBDIVISION,
                MetronomeAccent.SUBDIVISION,
                MetronomeAccent.BEAT,
                MetronomeAccent.SUBDIVISION,
                MetronomeAccent.SUBDIVISION,
            ]
            * 2,
            [tick.accent for tick in ticks],
        )
        for bars, start_us, message in (
            (True, 0, "bars must be a positive integer."),
            (1, True, "start_us must fit the canonical non-negative timeline."),
        ):
            with self.subTest(bars=bars, start_us=start_us):
                with self.assertRaisesRegex(ValueError, f"^{message}$"):
                    plan.schedule(bars, start_us=start_us)


if __name__ == "__main__":
    unittest.main()
