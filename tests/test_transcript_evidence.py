from __future__ import annotations

import unittest

from notewitness.application.transcript_evidence import (
    TranscriptEvidenceError,
    _append_or_require_unknown_actor,
)


class TranscriptEvidenceTests(unittest.TestCase):
    def test_unknown_actor_must_have_the_exact_restricted_generic_meaning(self) -> None:
        record = {"id": "actor:unknown", "role": "unknown", "visibility": "restricted"}
        payload = {"actors": [dict(record)]}

        _append_or_require_unknown_actor(payload, record)

        payload["actors"][0]["instrument_role"] = "voice"
        with self.assertRaisesRegex(TranscriptEvidenceError, "incompatible"):
            _append_or_require_unknown_actor(payload, record)


if __name__ == "__main__":
    unittest.main()
