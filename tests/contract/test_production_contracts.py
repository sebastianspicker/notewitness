from __future__ import annotations

import importlib
import unittest

from notewitness.interfaces.capabilities import (
    CapabilityLevel,
    capability_manifest,
    profile_readiness,
)
from notewitness.core.exports import (
    ExportFormat,
    ExportPreflight,
    LossSeverity,
    ProjectionLoss,
)


class ProductionContractTests(unittest.TestCase):
    def test_capability_code_surfaces_resolve(self) -> None:
        for capability in capability_manifest()["capabilities"]:
            surface = capability["code_surface"]
            if not surface.startswith("notewitness.") or surface.endswith(".js"):
                continue
            parts = surface.split(".")
            module = None
            remaining: list[str] = []
            for end in range(len(parts), 0, -1):
                try:
                    module = importlib.import_module(".".join(parts[:end]))
                except ModuleNotFoundError:
                    continue
                remaining = parts[end:]
                break
            self.assertIsNotNone(module, surface)
            resolved = module
            for name in remaining:
                self.assertTrue(hasattr(resolved, name), surface)
                resolved = getattr(resolved, name)

    def test_capability_manifest_has_unique_owned_surfaces(self) -> None:
        manifest = capability_manifest()
        items = manifest["capabilities"]
        identifiers = [item["capability_id"] for item in items]

        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertTrue(all(item["code_surface"] for item in items))
        self.assertGreater(manifest["counts"][CapabilityLevel.AVAILABLE.value], 0)

    def test_profiles_distinguish_code_from_external_engines(self) -> None:
        readiness = profile_readiness("tonic-local")

        self.assertFalse(readiness["ready"])
        for capability_id in (
            "activity_segmentation",
            "local_asr",
            "anonymous_diarization",
        ):
            self.assertIn(capability_id, readiness["missing"])
        for capability_id in (
            "one_action_capture",
            "local_playback_backend",
            "graphical_workbench",
            "local_lesson_digest",
        ):
            self.assertNotIn(capability_id, readiness["missing"])

        extended = profile_readiness("notewitness-v0.1")
        for capability_id in ("note_detection", "instrument_detection"):
            self.assertIn(capability_id, extended["missing"])
        self.assertNotIn("live_pitch_input", extended["missing"])

        noscribe = profile_readiness("noscribe-research")
        for capability_id in (
            "research_transcription_options",
            "transcription_language_modes",
            "transcription_speaker_options",
        ):
            self.assertIn(capability_id, noscribe["missing"])
        for capability_id in (
            "local_media_ingest",
            "transcript_correction_workspace",
            "transcription_run_manifest",
            "html_text_vtt_transcript_exports",
        ):
            self.assertNotIn(capability_id, noscribe["missing"])

    def test_export_blocks_unacknowledged_or_blocking_loss(self) -> None:
        blocking = ProjectionLoss(
            field="participant_identity",
            reason="destination cannot preserve the access restriction",
            severity=LossSeverity.BLOCKING,
            affected_record_ids=("actor:student",),
        )
        preflight = ExportPreflight(
            export_format=ExportFormat.EAF,
            destination="lesson.eaf",
            selected_record_ids=("event:instruction",),
            rights_authorized=True,
            losses=(blocking,),
            loss_preview_acknowledged=True,
        )

        self.assertFalse(preflight.executable)
        with self.assertRaisesRegex(ValueError, "booleans"):
            ExportPreflight(
                export_format=ExportFormat.WEBVTT,
                destination="lesson.vtt",
                selected_record_ids=("event:instruction",),
                rights_authorized="false",  # type: ignore[arg-type]
                losses=(),
                loss_preview_acknowledged="false",  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(ValueError, "selected"):
            ExportPreflight(
                export_format=ExportFormat.WEBVTT,
                destination="lesson.vtt",
                selected_record_ids=(),
                rights_authorized=True,
                losses=(),
                loss_preview_acknowledged=True,
            )


if __name__ == "__main__":
    unittest.main()
