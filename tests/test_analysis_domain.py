from __future__ import annotations

import inspect
import math
import unittest

from notewitness.domain.analysis import AnalysisState, NoteHypothesis
from notewitness.domain.timeline import MediaSpan


def note(**overrides: object) -> NoteHypothesis:
    values: dict[str, object] = {
        "hypothesis_id": "note:test",
        "span": MediaSpan("source:test", "audio", 0, 1_000),
        "state": AnalysisState.READY,
        "midi_pitch": 60.0,
        "frequency_hz": 440.0,
        "confidence": 0.5,
        "generator_id": "generator:test",
    }
    values.update(overrides)
    return NoteHypothesis(**values)  # type: ignore[arg-type]


class NoteHypothesisValidationTests(unittest.TestCase):
    def assert_note_error(self, values: dict[str, object], message: str) -> None:
        with self.assertRaises(ValueError) as context:
            note(**values)
        self.assertEqual(message, str(context.exception))

    def test_signature_fields_and_frozen_semantics_remain_public_contract(self) -> None:
        parameters = inspect.signature(NoteHypothesis).parameters
        self.assertEqual(
            (
                "hypothesis_id",
                "span",
                "state",
                "midi_pitch",
                "frequency_hz",
                "confidence",
                "generator_id",
                "source_track_id",
                "amplitude",
                "velocity",
                "pitch_bend_values",
                "pitch_bend_unit",
            ),
            tuple(parameters),
        )
        for name in (
            "hypothesis_id",
            "span",
            "state",
            "midi_pitch",
            "frequency_hz",
            "confidence",
            "generator_id",
        ):
            self.assertIs(inspect.Parameter.empty, parameters[name].default)
        self.assertIsNone(parameters["source_track_id"].default)
        self.assertIsNone(parameters["amplitude"].default)
        self.assertIsNone(parameters["velocity"].default)
        self.assertEqual((), parameters["pitch_bend_values"].default)
        self.assertIsNone(parameters["pitch_bend_unit"].default)
        instance = note()
        with self.assertRaises((AttributeError, TypeError)):
            instance.amplitude = 0.8  # type: ignore[misc]

    def test_equality_includes_defaults_and_each_meaningful_field(self) -> None:
        self.assertEqual(note(), note())
        values = {
            "hypothesis_id": "note:complete",
            "span": MediaSpan("source:test", "audio", 0, 1_000),
            "state": AnalysisState.READY,
            "midi_pitch": 60.0,
            "frequency_hz": 440.0,
            "confidence": 0.5,
            "generator_id": "generator:complete",
            "source_track_id": "track:complete",
            "amplitude": 0.8,
            "velocity": 90,
            "pitch_bend_values": (0.0, 0.25),
            "pitch_bend_unit": "semitones",
        }
        reference = note(**values)
        differences = (
            ("hypothesis_id", "note:other"),
            ("span", MediaSpan("source:test", "audio", 1, 1_000)),
            ("state", AnalysisState.UNCERTAIN),
            ("midi_pitch", 61.0),
            ("frequency_hz", 441.0),
            ("confidence", 0.6),
            ("generator_id", "generator:other"),
            ("source_track_id", "track:other"),
            ("amplitude", 0.7),
            ("velocity", 91),
            ("pitch_bend_values", (0.0, 0.5)),
            ("pitch_bend_unit", "cents"),
        )

        self.assertEqual(reference, note(**values))
        for field, replacement in differences:
            with self.subTest(field=field):
                self.assertNotEqual(reference, note(**(values | {field: replacement})))

    def test_ready_note_pitch_accepts_each_pitch_form_and_boundaries(self) -> None:
        cases = (
            {"midi_pitch": 0.0, "frequency_hz": None},
            {"midi_pitch": 127.0, "frequency_hz": None},
            {"midi_pitch": None, "frequency_hz": float.fromhex("0x0.0000000000001p-1022")},
            {"midi_pitch": None, "frequency_hz": 440.0},
            {"midi_pitch": 60, "frequency_hz": 440},
        )

        for values in cases:
            with self.subTest(values=values):
                self.assertIsInstance(note(**values), NoteHypothesis)

    def test_non_ready_notes_can_omit_pitch(self) -> None:
        self.assertEqual(
            AnalysisState.NOT_DETECTED,
            note(
                state=AnalysisState.NOT_DETECTED,
                midi_pitch=None,
                frequency_hz=None,
            ).state,
        )

    def test_note_pitch_rejects_bools_nonfinite_and_out_of_range_values(self) -> None:
        cases = (
            ({"midi_pitch": True}, "midi_pitch must be finite and in the range [0, 127]."),
            ({"midi_pitch": math.nan}, "midi_pitch must be finite and in the range [0, 127]."),
            ({"midi_pitch": math.inf}, "midi_pitch must be finite and in the range [0, 127]."),
            ({"midi_pitch": -math.inf}, "midi_pitch must be finite and in the range [0, 127]."),
            ({"midi_pitch": -0.000001}, "midi_pitch must be finite and in the range [0, 127]."),
            ({"midi_pitch": 127.000001}, "midi_pitch must be finite and in the range [0, 127]."),
            ({"frequency_hz": True}, "frequency_hz must be a positive finite number."),
            ({"frequency_hz": math.nan}, "frequency_hz must be a positive finite number."),
            ({"frequency_hz": math.inf}, "frequency_hz must be a positive finite number."),
            ({"frequency_hz": -math.inf}, "frequency_hz must be a positive finite number."),
            ({"frequency_hz": 0.0}, "frequency_hz must be a positive finite number."),
            ({"frequency_hz": -0.000001}, "frequency_hz must be a positive finite number."),
        )

        for values, message in cases:
            with self.subTest(values=values):
                self.assert_note_error(values, message)

    def test_amplitude_and_velocity_truth_table(self) -> None:
        accepted = (
            {"amplitude": None, "velocity": None},
            {"amplitude": 0.0, "velocity": 0},
            {"amplitude": 1.0, "velocity": 127},
            {"amplitude": 0.5, "velocity": 64},
        )
        rejected = (
            ({"amplitude": True}, "amplitude must be finite and in the range [0, 1]."),
            ({"amplitude": math.nan}, "amplitude must be finite and in the range [0, 1]."),
            ({"amplitude": math.inf}, "amplitude must be finite and in the range [0, 1]."),
            ({"amplitude": -math.inf}, "amplitude must be finite and in the range [0, 1]."),
            ({"amplitude": -0.000001}, "amplitude must be finite and in the range [0, 1]."),
            ({"amplitude": 1.000001}, "amplitude must be finite and in the range [0, 1]."),
            ({"velocity": True}, "velocity must be an integer in the range [0, 127]."),
            ({"velocity": 1.0}, "velocity must be an integer in the range [0, 127]."),
            ({"velocity": -1}, "velocity must be an integer in the range [0, 127]."),
            ({"velocity": 128}, "velocity must be an integer in the range [0, 127]."),
        )

        for values in accepted:
            with self.subTest(accepted=values):
                self.assertIsInstance(note(**values), NoteHypothesis)
        for values, message in rejected:
            with self.subTest(rejected=values):
                self.assert_note_error(values, message)

    def test_pitch_bend_truth_table_accepts_bounded_finite_tuple_with_unit(self) -> None:
        cases = (
            {"pitch_bend_values": (), "pitch_bend_unit": None},
            {"pitch_bend_values": (), "pitch_bend_unit": "semitones"},
            {"pitch_bend_values": (-1, 0.0, 1.5), "pitch_bend_unit": "semitones"},
            {"pitch_bend_values": (0.0,) * 50_000, "pitch_bend_unit": "basic-pitch:semitone-offset"},
        )

        for values in cases:
            with self.subTest(length=len(values["pitch_bend_values"])):
                self.assertIsInstance(note(**values), NoteHypothesis)

    def test_pitch_bend_truth_table_rejects_invalid_values_and_units(self) -> None:
        cases = (
            ({"pitch_bend_values": [0.0], "pitch_bend_unit": "semitones"}, "pitch_bend_values must be bounded finite numbers."),
            ({"pitch_bend_values": (0.0,) * 50_001, "pitch_bend_unit": "semitones"}, "pitch_bend_values must be bounded finite numbers."),
            ({"pitch_bend_values": (True,), "pitch_bend_unit": "semitones"}, "pitch_bend_values must be bounded finite numbers."),
            ({"pitch_bend_values": ("0.0",), "pitch_bend_unit": "semitones"}, "pitch_bend_values must be bounded finite numbers."),
            ({"pitch_bend_values": (math.nan,), "pitch_bend_unit": "semitones"}, "pitch_bend_values must be bounded finite numbers."),
            ({"pitch_bend_values": (math.inf,), "pitch_bend_unit": "semitones"}, "pitch_bend_values must be bounded finite numbers."),
            ({"pitch_bend_values": (-math.inf,), "pitch_bend_unit": "semitones"}, "pitch_bend_values must be bounded finite numbers."),
            ({"pitch_bend_values": (0.0,), "pitch_bend_unit": None}, "Pitch-bend values require an explicit unit."),
            ({"pitch_bend_values": (), "pitch_bend_unit": ""}, "pitch_bend_unit must be a bounded run-local identifier."),
            ({"pitch_bend_values": (), "pitch_bend_unit": "semi tones"}, "pitch_bend_unit must be a bounded run-local identifier."),
            ({"pitch_bend_values": (), "pitch_bend_unit": "x" * 257}, "pitch_bend_unit must be a bounded run-local identifier."),
            ({"pitch_bend_values": (), "pitch_bend_unit": True}, "pitch_bend_unit must be a bounded run-local identifier."),
        )

        for values, message in cases:
            with self.subTest(values=values):
                self.assert_note_error(values, message)

    def test_validation_order_is_stable_when_multiple_contracts_are_invalid(self) -> None:
        cases = (
            (
                {"midi_pitch": None, "frequency_hz": None, "amplitude": 2.0},
                "Ready note hypotheses require pitch evidence.",
            ),
            (
                {"midi_pitch": -1.0, "frequency_hz": 0.0},
                "midi_pitch must be finite and in the range [0, 127].",
            ),
            (
                {"frequency_hz": 0.0, "amplitude": 2.0},
                "frequency_hz must be a positive finite number.",
            ),
            (
                {"amplitude": 2.0, "velocity": 128},
                "amplitude must be finite and in the range [0, 1].",
            ),
            (
                {"velocity": 128, "pitch_bend_values": (math.nan,), "pitch_bend_unit": "semitones"},
                "velocity must be an integer in the range [0, 127].",
            ),
            (
                {"pitch_bend_values": (math.nan,), "pitch_bend_unit": ""},
                "pitch_bend_values must be bounded finite numbers.",
            ),
            (
                {"pitch_bend_values": (0.0,), "pitch_bend_unit": ""},
                "pitch_bend_unit must be a bounded run-local identifier.",
            ),
        )

        for values, message in cases:
            with self.subTest(values=values):
                self.assert_note_error(values, message)
