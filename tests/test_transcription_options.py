from __future__ import annotations

import unittest

from notewitness.domain.timeline import MediaSpan
from notewitness.domain.transcription_options import (
    DiarizationMode,
    DisfluencyPolicy,
    LanguageMode,
    TranscriptExportFormat,
    TranscriptionJobSpec,
)


def _job_spec(**overrides: object) -> TranscriptionJobSpec:
    values: dict[str, object] = {
        "job_id": "job:transcription-options",
        "spans": (MediaSpan("source:lesson", "audio", 0, 60_000_000),),
        "model_profile_id": "profile:precise",
        "language_mode": LanguageMode.FIXED,
        "requested_language": "de",
        "diarization_mode": DiarizationMode.EXACT,
        "exact_speaker_count": 2,
        "detect_overlap": True,
        "disfluency_policy": DisfluencyPolicy.INCLUDE,
        "pause_threshold_ms": 1_000,
        "visible_timestamps": True,
        "timestamp_interval_ms": 60_000,
        "output_format": TranscriptExportFormat.HTML,
    }
    values.update(overrides)
    return TranscriptionJobSpec(**values)  # type: ignore[arg-type]


class TranscriptionOptionsTests(unittest.TestCase):
    def test_adapter_setting_boundaries_reject_booleans_nonfinite_and_wrong_types(self) -> None:
        for settings in (
            {"beam_size": 1, "vad_threshold": 0.0},
            {"beam_size": 100, "vad_threshold": 1.0},
        ):
            with self.subTest(settings=settings):
                self.assertEqual(
                    settings,
                    dict(_job_spec(adapter_settings=settings).adapter_settings),
                )
        for value in (True, 0, 101, "5"):
            with self.subTest(beam_size=value):
                with self.assertRaisesRegex(
                    ValueError, "beam_size must be an integer in \\[1, 100\\]"
                ):
                    _job_spec(adapter_settings={"beam_size": value})
        for value in (True, float("nan"), float("inf"), -0.1, 1.1, "0.5"):
            with self.subTest(vad_threshold=value):
                with self.assertRaisesRegex(
                    ValueError, "vad_threshold must be in \\[0, 1\\]"
                ):
                    _job_spec(adapter_settings={"vad_threshold": value})


if __name__ == "__main__":
    unittest.main()
