from __future__ import annotations

import unittest

from notewitness.lessons.actors import (
    INELIGIBLE_HUMAN_EVIDENCE_ROLES,
    is_human_evidence_author,
)
from notewitness.lessons.review_rules import (
    MAX_IDENTIFIER_CHARS,
    ReviewError,
    bounded_text,
    ensure_human_generator,
    identifier,
    is_normalized_machine_suggestion,
)


class ReviewRulesTests(unittest.TestCase):
    def test_identifier_bounds(self) -> None:
        identifier("x" * MAX_IDENTIFIER_CHARS, "id")
        for value in ("", "x" * (MAX_IDENTIFIER_CHARS + 1), 7):
            with self.assertRaisesRegex(ReviewError, "bounded non-empty identifier"):
                identifier(value, "id")  # type: ignore[arg-type]

    def test_bounded_text_strips_and_limits(self) -> None:
        self.assertEqual("note", bounded_text("  note  ", "text", 4))
        with self.assertRaisesRegex(ReviewError, "non-empty text"):
            bounded_text("   ", "text", 4)
        with self.assertRaisesRegex(ReviewError, "exceeds 3 characters"):
            bounded_text("note", "text", 3)

    def test_machine_suggestion_predicate_is_exact(self) -> None:
        generators = {
            "g:machine": {"kind": "machine"},
            "g:human": {"kind": "human"},
        }
        source = {
            "review_status": "machine_suggested",
            "layer": "normalized_hypothesis",
            "generator_id": "g:machine",
        }
        self.assertTrue(is_normalized_machine_suggestion(source, generators))
        for change in (
            {"review_status": "human_accepted"},
            {"layer": "accepted_annotation"},
            {"generator_id": "g:human"},
            {"generator_id": "g:missing"},
        ):
            self.assertFalse(
                is_normalized_machine_suggestion({**source, **change}, generators)
            )

    def test_human_generator_is_deterministic_and_idempotent(self) -> None:
        payload: dict[str, object] = {"generators": []}
        first = ensure_human_generator(payload, "actor:a")  # type: ignore[arg-type]
        second = ensure_human_generator(payload, "actor:a")  # type: ignore[arg-type]
        other = ensure_human_generator(payload, "actor:b")  # type: ignore[arg-type]
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertEqual(2, len(payload["generators"]))  # type: ignore[arg-type]

    def test_reserved_roles_cannot_author_human_evidence(self) -> None:
        for role in INELIGIBLE_HUMAN_EVIDENCE_ROLES | {" Machine ", "SYSTEM"}:
            self.assertFalse(is_human_evidence_author({"role": role}))
        self.assertFalse(is_human_evidence_author(None))
        self.assertFalse(is_human_evidence_author({"role": 3}))
        self.assertTrue(
            is_human_evidence_author({"role": "music analysis researcher"})
        )


if __name__ == "__main__":
    unittest.main()
