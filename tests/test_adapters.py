from __future__ import annotations

import unittest

from notewitness.adapters.base import SourceSpan


class SourceSpanTests(unittest.TestCase):
    def test_span_uses_non_negative_integer_microseconds(self) -> None:
        self.assertEqual(250, SourceSpan("source:test", 100, 250).duration_us)
        for invalid in (-1, 1.5, True):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    SourceSpan("source:test", invalid, 1)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
