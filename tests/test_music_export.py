from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from notewitness.application.music_export import (
    MusicExportError,
    MusicExportFormat,
    SymbolicMusicExportService,
)
from notewitness.project import initialize_project
from notewitness.project_store import ProjectStore


class MusicExportTests(unittest.TestCase):
    def test_csv_is_exact_and_new_only(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            initialize_project(root)
            _append_note(root, "event:later", 2_000, 1_500, 61.0, "track:b")
            _append_note(
                root,
                "event:first",
                1_000,
                1_000,
                60.5,
                "track:a",
                frequency_hz=261.6256,
                amplitude=0.75,
                velocity=88,
                pitch_bend_values=(-0.1, 0.2),
                pitch_bend_unit="basic-pitch:semitone-offset",
            )
            service = SymbolicMusicExportService.for_project(root)

            result = service.export(export_format="csv", filename="notes.csv", rights_authorized=True, loss_preview_acknowledged=True)
            self.assertEqual(
                "event_id,target_id,source_id,stream_id,start_us,duration_us,midi_pitch,frequency_hz,amplitude,velocity,pitch_bend_unit,pitch_bend_values,instrument_track_id,review_status\n"
                'event:first,target:event:first,source:fixture,audio,1000,1000,60.5,261.6256,0.75,88,basic-pitch:semitone-offset,"[-0.1,0.2]",track:a,machine_suggested\n'
                "event:later,target:event:later,source:fixture,audio,2000,1500,61,,,,,,track:b,machine_suggested\n",
                Path(result.path).read_text(),
            )
            with self.assertRaisesRegex(Exception, "Refusing to replace"):
                service.export(export_format="csv", filename="notes.csv", rights_authorized=True, loss_preview_acknowledged=True)

    def test_midi_is_parseable_and_separates_tracks(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            initialize_project(root)
            _append_note(
                root,
                "event:a",
                0,
                1_000,
                60.0,
                "track:a",
                amplitude=0.8,
                velocity=77,
                pitch_bend_values=(0.0, 0.25),
                pitch_bend_unit="basic-pitch:semitone-offset",
            )
            _append_note(root, "event:b", 0, 1_000, 62.0, "track:b")
            result = SymbolicMusicExportService.for_project(root).export(export_format=MusicExportFormat.MIDI, filename="notes.mid", rights_authorized=True, loss_preview_acknowledged=True)
            raw = Path(result.path).read_bytes()
            self.assertEqual(b"MThd", raw[:4])
            self.assertEqual((6).to_bytes(4, "big"), raw[4:8])
            self.assertEqual(3, int.from_bytes(raw[10:12], "big"))
            self.assertEqual(2, raw.count(b"\x90"))
            self.assertIn(b"track:a", raw)
            self.assertIn(b"\x90\x3c\x4d", raw)
            self.assertEqual(
                {"source_span_provenance", "amplitude", "pitch_bends"},
                {loss.field for loss in result.documented_losses},
            )

    def test_midi_requires_one_source_and_merges_overlapping_same_pitch(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            initialize_project(root)
            _append_note(root, "event:a", 0, 2_000, 60.0, "track:a", velocity=40)
            _append_note(root, "event:b", 1_000, 2_000, 60.0, "track:a", velocity=90)
            _add_source(root, "source:second")
            _append_note(
                root,
                "event:second-source",
                0,
                1_000,
                64.0,
                "track:a",
                source_id="source:second",
            )
            service = SymbolicMusicExportService.for_project(root)

            with self.assertRaisesRegex(MusicExportError, "one explicit source"):
                service.preflight(
                    export_format="midi",
                    filename="mixed.mid",
                    rights_authorized=True,
                    loss_preview_acknowledged=True,
                )
            result = service.export(
                export_format="midi",
                filename="one-source.mid",
                rights_authorized=True,
                loss_preview_acknowledged=True,
                source_id="source:fixture",
            )

            raw = Path(result.path).read_bytes()
            self.assertEqual(("source:fixture",), result.source_ids)
            self.assertEqual(2, result.record_count)
            self.assertEqual(1, raw.count(b"\x90"))
            self.assertIn(b"\x90\x3c\x5a", raw)
            self.assertIn(b"source:fixture | track:a", raw)
            self.assertIn(
                "overlapping_same_pitch",
                {loss.field for loss in result.documented_losses},
            )

    def test_accepted_note_replaces_its_machine_suggestion_in_export(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            initialize_project(root)
            _append_note(root, "event:machine", 0, 1_000, 60.0, "track:a")
            _accept_note(root, "event:machine", "event:accepted")

            result = SymbolicMusicExportService.for_project(root).export(
                export_format="csv",
                filename="accepted.csv",
                rights_authorized=True,
                loss_preview_acknowledged=True,
            )

            self.assertEqual(1, result.record_count)
            exported = Path(result.path).read_text()
            self.assertEqual(2, len(exported.splitlines()))
            self.assertTrue(exported.splitlines()[1].startswith("event:accepted,"))

    def test_rejects_unreviewable_invalid_or_absent_notes_and_rights_gates(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            initialize_project(root)
            service = SymbolicMusicExportService.for_project(root)
            self.assertEqual(
                (),
                service.preflight(
                    export_format="midi",
                    filename="none.mid",
                    rights_authorized=True,
                    loss_preview_acknowledged=True,
                ).losses,
            )
            with self.assertRaisesRegex(MusicExportError, "No reviewable"):
                service.export(export_format="csv", filename="none.csv", rights_authorized=True, loss_preview_acknowledged=True)
            _append_note(root, "event:invalid", 0, 1_000, 128.0, None)
            with self.assertRaisesRegex(MusicExportError, "invalid MIDI pitch"):
                service.export(export_format="csv", filename="invalid.csv", rights_authorized=True, loss_preview_acknowledged=True)
            _remove_event(root, "event:invalid")
            _append_note(root, "event:valid", 0, 1_000, 60.0, None)
            with self.assertRaisesRegex(MusicExportError, "rights"):
                service.export(export_format="csv", filename="rights.csv", rights_authorized=False, loss_preview_acknowledged=True)
            with self.assertRaisesRegex(MusicExportError, "acknowledgement"):
                service.export(export_format="csv", filename="loss.csv", rights_authorized=True, loss_preview_acknowledged=False)


def _append_note(
    root: Path,
    event_id: str,
    start_us: int,
    duration_us: int,
    pitch: float,
    track: str | None,
    *,
    status: str = "machine_suggested",
    frequency_hz: float | None = None,
    amplitude: float | None = None,
    velocity: int | None = None,
    pitch_bend_values: tuple[float, ...] = (),
    pitch_bend_unit: str | None = None,
    source_id: str = "source:fixture",
) -> None:
    store = ProjectStore(root)
    snapshot = store.load()
    def change(payload: dict[str, object]) -> None:
        payload["targets"].append({"id": f"target:{event_id}", "source_id": source_id, "selector": {"stream_id": "audio", "start_us": start_us, "duration_us": duration_us}, "alignment_state": "unknown"})  # type: ignore[index,union-attr]
        payload["events"].append({"id": event_id, "type": "local:note", "scope": "evidence", "actor_id": "actor:fixture", "target_ids": [f"target:{event_id}"], "body": {"format": "notewitness.note.v1", "value": {"midi_pitch": pitch, **({"instrument_track_id": track} if track else {}), **({"frequency_hz": frequency_hz} if frequency_hz is not None else {}), **({"amplitude": amplitude} if amplitude is not None else {}), **({"velocity": velocity} if velocity is not None else {}), **({"pitch_bend_values": list(pitch_bend_values), "pitch_bend_unit": pitch_bend_unit} if pitch_bend_values else {})}}, "alternatives": [], "generator_id": "generator:fixture", "rights_id": "rights:fixture", "layer": "normalized_hypothesis", "confidence": {"kind": "not_applicable"}, "review_status": status})  # type: ignore[index,union-attr]
    _ensure_fixture_records(store, snapshot.payload)
    snapshot = store.load()
    store.mutate(change, expected_sha256=snapshot.sha256)


def _add_source(root: Path, source_id: str) -> None:
    store = ProjectStore(root)
    snapshot = store.load()

    def change(payload: dict[str, object]) -> None:
        payload["sources"].append(  # type: ignore[index,union-attr]
            {
                "id": source_id,
                "kind": "recording",
                "uri": f"media/{source_id.rpartition(':')[2]}.wav",
                "sha256": "c" * 64,
                "rights_id": "rights:fixture",
            }
        )

    store.mutate(change, expected_sha256=snapshot.sha256)


def _accept_note(root: Path, source_event_id: str, accepted_event_id: str) -> None:
    store = ProjectStore(root)
    snapshot = store.load()

    def change(payload: dict[str, object]) -> None:
        payload["generators"].append(  # type: ignore[index,union-attr]
            {
                "id": "generator:human-fixture",
                "kind": "human",
                "name": "fixture reviewer",
                "version": "1",
                "weight_hash_state": "not_applicable",
            }
        )
        source = next(
            item for item in payload["events"] if item["id"] == source_event_id  # type: ignore[index,union-attr]
        )
        accepted = dict(source)
        accepted["id"] = accepted_event_id
        accepted["review_status"] = "human_accepted"
        accepted["layer"] = "accepted_annotation"
        accepted["generator_id"] = "generator:human-fixture"
        accepted["confidence"] = {"kind": "human_review"}
        accepted["body"] = {
            **source["body"],  # type: ignore[index]
            "source_suggestion_id": source_event_id,
        }
        payload["events"].append(accepted)  # type: ignore[index,union-attr]
        payload["revisions"].append(  # type: ignore[index,union-attr]
            {
                "id": "revision:accept-fixture",
                "record_id": accepted_event_id,
                "parent_revision_ids": [],
                "author_id": "actor:fixture",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "operation": "adjudicate",
                "reason": "Fixture review.",
            }
        )

    store.mutate(change, expected_sha256=snapshot.sha256)


def _ensure_fixture_records(store: ProjectStore, payload: dict[str, object]) -> None:
    if payload["sources"]:  # type: ignore[index]
        return
    snapshot = store.load()
    def change(candidate: dict[str, object]) -> None:
        candidate["rights"].append({"id": "rights:fixture", "access": "restricted", "remote_processing": False, "model_training": False, "retention": "project"})  # type: ignore[index,union-attr]
        candidate["sources"].append({"id": "source:fixture", "kind": "recording", "uri": "media/fixture.wav", "sha256": "a" * 64, "rights_id": "rights:fixture"})  # type: ignore[index,union-attr]
        candidate["actors"].append({"id": "actor:fixture", "role": "student", "visibility": "restricted"})  # type: ignore[index,union-attr]
        candidate["generators"].append({"id": "generator:fixture", "kind": "machine", "name": "fixture", "version": "1", "model": "fixture", "weight_hash_state": "sha256:" + "a" * 64})  # type: ignore[index,union-attr]
    store.mutate(change, expected_sha256=snapshot.sha256)


def _remove_event(root: Path, event_id: str) -> None:
    store = ProjectStore(root)
    snapshot = store.load()
    def change(payload: dict[str, object]) -> None:
        payload["events"][:] = [item for item in payload["events"] if item["id"] != event_id]  # type: ignore[index,union-attr]
        payload["targets"][:] = [item for item in payload["targets"] if item["id"] != f"target:{event_id}"]  # type: ignore[index,union-attr]
    store.mutate(change, expected_sha256=snapshot.sha256)


if __name__ == "__main__":
    unittest.main()
