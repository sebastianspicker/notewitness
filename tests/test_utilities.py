from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
