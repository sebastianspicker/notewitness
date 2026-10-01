from __future__ import annotations

import json
import unittest

from notewitness.core.strict_json import is_sha256_hex, reject_duplicate_keys


class _CallerError(RuntimeError):
    pass


class RejectDuplicateKeysTests(unittest.TestCase):
    def test_duplicate_key_raises_the_callers_exception_and_message(self) -> None:
        hook = reject_duplicate_keys(lambda key: _CallerError(f"caller duplicate {key!r}."))

        with self.assertRaises(_CallerError) as caught:
            json.loads('{"outer": {"a": 1, "a": 2}}', object_pairs_hook=hook)

        self.assertIs(_CallerError, type(caught.exception))
        self.assertEqual("caller duplicate 'a'.", str(caught.exception))

    def test_value_error_factories_are_not_rewrapped_by_json(self) -> None:
        hook = reject_duplicate_keys(lambda key: ValueError(f"duplicate object key {key!r}"))

        with self.assertRaisesRegex(ValueError, "^duplicate object key 'k'$") as caught:
            json.loads('[{"k": 1, "k": 1}]', object_pairs_hook=hook)

        self.assertNotIsInstance(caught.exception, json.JSONDecodeError)

    def test_unique_objects_keep_values_and_order(self) -> None:
        hook = reject_duplicate_keys(lambda key: _CallerError(key))

        payload = json.loads('{"b": 1, "a": {"c": [1, {"d": null}]}}', object_pairs_hook=hook)

        self.assertEqual({"b": 1, "a": {"c": [1, {"d": None}]}}, payload)
        self.assertEqual(["b", "a"], list(payload))

    def test_same_key_in_sibling_objects_is_allowed(self) -> None:
        hook = reject_duplicate_keys(lambda key: _CallerError(key))

        self.assertEqual(
            [{"a": 1}, {"a": 2}],
            json.loads('[{"a": 1}, {"a": 2}]', object_pairs_hook=hook),
        )


class IsSha256HexTests(unittest.TestCase):
    def test_accepts_exactly_64_lowercase_hex_characters(self) -> None:
        self.assertTrue(is_sha256_hex("0123456789abcdef" * 4))

    def test_rejects_near_misses(self) -> None:
        valid = "a" * 64
        for value in (
            "",
            valid[:-1],
            valid + "a",
            valid[:-1] + "A",
            valid[:-1] + "g",
            valid[:-1] + "\n",
            valid + "\n",
            " " + valid[1:],
            valid[:-1] + "٣",
        ):
            with self.subTest(value=value):
                self.assertFalse(is_sha256_hex(value))

    def test_rejects_non_strings(self) -> None:
        for value in (None, 0, b"a" * 64, list("a" * 64), tuple("a" * 64)):
            with self.subTest(value=value):
                self.assertFalse(is_sha256_hex(value))


if __name__ == "__main__":
    unittest.main()
