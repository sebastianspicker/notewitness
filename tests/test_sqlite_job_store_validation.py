from __future__ import annotations

import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from notewitness.infrastructure.sqlite_job_store import SQLiteJobStore, _lease_seconds


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


class BusyTimeoutValidationTests(unittest.TestCase):
    def test_constructor_rejects_non_decimal_or_out_of_range_timeouts(self) -> None:
        values = (True, "5000", 1.5, math.nan, math.inf, -math.inf, 0, 60_001)
        with TemporaryDirectory() as temporary:
            database = Path(temporary) / "jobs.sqlite"
            for value in values:
                with self.subTest(value=value), self.assertRaisesRegex(
                    ValueError, "^busy_timeout_ms must be between 1 and 60000\\.$"
                ):
                    SQLiteJobStore(database, busy_timeout_ms=value)  # type: ignore[arg-type]

    def test_connection_uses_a_bounded_decimal_pragma(self) -> None:
        connection = MagicMock()
        with TemporaryDirectory() as temporary, patch(
            "notewitness.infrastructure.sqlite_job_store.sqlite3.connect", return_value=connection
        ):
            SQLiteJobStore(Path(temporary) / "jobs.sqlite", busy_timeout_ms=12_345)

        connection.execute.assert_any_call("PRAGMA busy_timeout = 12345")
