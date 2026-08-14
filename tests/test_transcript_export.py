from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from notewitness.application.transcript_export import (
    TranscriptEvidenceExportService,
    TranscriptExportError,
    _source_targets,
)
from notewitness.application.transcript_review_service import add_project_actor
from notewitness.application.workbench import (
    accept_evidence_suggestion,
    revise_evidence_annotation,
)
from notewitness.media_ingest import ingest_media
from notewitness.project import initialize_project
from notewitness.project_store import ProjectStore


class TranscriptWorkbenchExportTests(unittest.TestCase):
    def test_source_targets_accepts_only_valid_source_time_anchors_in_event_order(self) -> None:
        targets = {
            "target:later": {"source_id": "source:lesson", "selector": {"start_us": 5, "duration_us": 1}},
            "target:wrong-source": {"source_id": "source:other", "selector": {"start_us": 0, "duration_us": 1}},
            "target:no-selector": {"source_id": "source:lesson", "selector": None},
            "target:bool-start": {"source_id": "source:lesson", "selector": {"start_us": True, "duration_us": 1}},
            "target:bool-duration": {"source_id": "source:lesson", "selector": {"start_us": 0, "duration_us": True}},
            "target:negative-start": {"source_id": "source:lesson", "selector": {"start_us": -1, "duration_us": 1}},
            "target:zero-duration": {"source_id": "source:lesson", "selector": {"start_us": 0, "duration_us": 0}},
            "target:not-mapping": "not a target",
            "target:first": {"source_id": "source:lesson", "selector": {"start_us": 0, "duration_us": 1}},
        }

        matches = _source_targets(
            {"target_ids": tuple(targets)},
            targets,  # type: ignore[arg-type]
            "source:lesson",
        )

        self.assertEqual((targets["target:later"], targets["target:first"]), matches)

    def test_exports_accepted_only_or_explicit_machine_layer_with_format_controls(self) -> None:
        with TemporaryDirectory() as temporary:
            project, source_id = _project(Path(temporary))
            service = TranscriptEvidenceExportService.for_project(project)
            with self.assertRaisesRegex(TranscriptExportError, "No accepted"):
                service.export(export_format="text", filename="accepted.txt", source_id=source_id,
                               evidence_layer="accepted_only", rights_authorized=True,
                               loss_preview_acknowledged=True)
            exported = service.export(export_format="text", filename="all.txt", source_id=source_id,
                                       evidence_layer="include_machine_suggestions", rights_authorized=True,
                                       loss_preview_acknowledged=True, visible_timestamps=True,
                                       timestamp_interval_ms=60_000, pause_threshold_ms=1_000)
            self.assertEqual(1, exported.record_count)
            self.assertEqual("include_machine_suggestions", exported.evidence_layer.value)
            rendered = (project / "exports" / "all.txt").read_text()
            self.assertIn("[00:00:00.000]", rendered)
            self.assertIn("[actor:researcher]", rendered)
            self.assertIn("[UNREVIEWED MACHINE SUGGESTION]", rendered)
            self.assertTrue(any(
                loss.field == "evidence_graph_metadata"
                for loss in exported.documented_losses
            ))
            self.assertEqual(0o600, (project / "exports" / "all.txt").stat().st_mode & 0o777)
            with self.assertRaisesRegex(TranscriptExportError, "rights authorization"):
                service.export(export_format="html", filename="blocked.html", source_id=source_id,
                               evidence_layer="include_machine_suggestions", rights_authorized=False,
                               loss_preview_acknowledged=True)

    def test_webvtt_documents_unsupported_inline_controls_as_losses(self) -> None:
        with TemporaryDirectory() as temporary:
            project, source_id = _project(Path(temporary))
            exported = TranscriptEvidenceExportService.for_project(project).export(
                export_format="webvtt", filename="lesson.vtt", source_id=source_id,
                evidence_layer="include_machine_suggestions", rights_authorized=True,
                loss_preview_acknowledged=True, visible_timestamps=True,
                timestamp_interval_ms=60_000, pause_threshold_ms=2_000,
            )
            self.assertTrue(any(loss.field == "pause_threshold_ms" for loss in exported.documented_losses))
            self.assertTrue((project / "exports" / "lesson.vtt").read_text().startswith("WEBVTT"))

    def test_accepted_export_omits_superseded_suggestion_and_revision(self) -> None:
        with TemporaryDirectory() as temporary:
            project, source_id = _project(Path(temporary))
            before = ProjectStore(project).load()
            accepted = accept_evidence_suggestion(
                str(project),
                event_id="event:speech",
                author_id="actor:researcher",
                actor_id="actor:researcher",
                reason="Auditioned locally.",
                expected_sha256=before.sha256,
            )
            revised = revise_evidence_annotation(
                str(project),
                event_id=accepted.record_ids[0],
                author_id="actor:researcher",
                actor_id="actor:researcher",
                reason="Corrected after replay.",
                replacement_text="Play the phrase very lightly.",
                expected_sha256=accepted.project_sha256,
            )
            self.assertEqual(1, len(revised.record_ids))

            exported = TranscriptEvidenceExportService.for_project(project).export(
                export_format="text",
                filename="reviewed.txt",
                source_id=source_id,
                evidence_layer="accepted_only",
                rights_authorized=True,
                loss_preview_acknowledged=True,
            )
            rendered = (project / "exports" / "reviewed.txt").read_text()

            self.assertEqual(1, exported.record_count)
            self.assertIn("Play the phrase very lightly.", rendered)
            self.assertNotIn("[UNREVIEWED MACHINE SUGGESTION]", rendered)
            self.assertNotIn("Play the phrase lightly.\n", rendered)


def _project(parent: Path) -> tuple[Path, str]:
    parent.chmod(0o700)
    project = parent / "study"
    initialize_project(project)
    media = parent / "lesson.wav"
    media.write_bytes(b"synthetic")
    media.chmod(0o600)
    imported = ingest_media(project, media, create_restricted_rights=True)
    add_project_actor(str(project), actor_id="actor:researcher", role="researcher")
    snapshot = ProjectStore(project).load()
    def append(payload: dict[str, object]) -> None:
        payload["generators"].append({"id": "generator:asr", "kind": "machine", "name": "fixture", "version": "1", "model": "fixture", "weight_hash_state": "sha256:" + "a" * 64})  # type: ignore[index]
        payload["targets"].append({"id": "target:speech", "source_id": imported.source_id, "selector": {"stream_id": "audio", "start_us": 0, "duration_us": 2_000_000}, "musical_selector": None, "alignment_state": "not_applicable"})  # type: ignore[index]
        payload["events"].append({"id": "event:speech", "type": "speech", "scope": "evidence", "actor_id": "actor:researcher", "target_ids": ["target:speech"], "body": {"format": "text", "value": "Play the phrase lightly."}, "alternatives": [], "generator_id": "generator:asr", "rights_id": imported.rights_id, "layer": "normalized_hypothesis", "confidence": {"kind": "probability", "value": 0.9}, "review_status": "machine_suggested"})  # type: ignore[index]
    ProjectStore(project).mutate(append, expected_sha256=snapshot.sha256)
    return project, imported.source_id
