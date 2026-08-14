from __future__ import annotations

import math
import unittest

from notewitness.infrastructure.sqlite_job_store import _lease_seconds


class LeaseSecondsValidationTests(unittest.TestCase):
    def test_accepts_bounded_positive_finite_numbers(self) -> None:
        for value in (1, 1.5, 86_400):
            with self.subTest(value=value):
                result = _lease_seconds(value)
                self.assertIs(float, type(result))
                self.assertEqual(float(value), result)

    def test_rejects_invalid_values_with_exact_message(self) -> None:
        for value in (True, "1", math.nan, math.inf, -math.inf, 0, -1, 86_401):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "^lease_seconds must be finite and between 0 and 86400\\.$"
            ):
                _lease_seconds(value)  # type: ignore[arg-type]

    def test_preserves_comparison_protocol(self) -> None:
        class LowerBoundProbe(float):
            def __gt__(self, other: object) -> bool:
                return False

            def __le__(self, other: object) -> bool:
                return False

        class UpperBoundProbe(float):
            def __gt__(self, other: object) -> bool:
                return other == 0

            def __le__(self, other: object) -> bool:
                return False

        for value in (LowerBoundProbe(1), UpperBoundProbe(1)):
            with self.subTest(value=type(value).__name__), self.assertRaisesRegex(
                ValueError, "^lease_seconds must be finite and between 0 and 86400\\.$"
            ):
                _lease_seconds(value)
