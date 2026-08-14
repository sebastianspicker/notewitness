from __future__ import annotations

import json
import stat

from notewitness.project_store import ProjectStore

from tests.workbench_server_test_support import WorkbenchServerTestCase


class WorkbenchServerExportTests(WorkbenchServerTestCase):
    def test_music_export_endpoint_is_explicit_private_and_path_safe(self) -> None:
        def add_note(payload: dict[str, object]) -> None:
            payload["generators"].append({  # type: ignore[index,union-attr]
                "id": "generator:notes", "kind": "machine", "name": "fixture notes", "version": "1",
                "model": "fixture", "weight_hash_state": "sha256:" + "a" * 64,
            })
            payload["targets"].append({  # type: ignore[index,union-attr]
                "id": "target:note-one", "source_id": self.imported.source_id,
                "selector": {"stream_id": "audio", "start_us": 100_000, "duration_us": 500_000, "spatial": None},
                "musical_selector": None, "alignment_state": "unknown",
            })
            payload["events"].append({  # type: ignore[index,union-attr]
                "id": "event:note-one", "type": "local:note", "scope": "evidence", "actor_id": "actor:researcher",
                "target_ids": ["target:note-one"], "body": {"format": "application/vnd.notewitness.note+json", "value": {"midi_pitch": 64.0, "source_track_id": "instrument-track-01"}},
                "alternatives": [], "generator_id": "generator:notes", "rights_id": self.imported.rights_id,
                "layer": "normalized_hypothesis", "confidence": {"kind": "adapter_reported", "value": 0.8}, "review_status": "machine_suggested",
            })
        ProjectStore(self.project).mutate(add_note)
        snapshot = json.loads(self._request("GET", "/api/workbench")[2])
        headers = {"Content-Type": "application/json", "Origin": self.server.origin, "X-NoteWitness-CSRF": snapshot["csrf_token"]}
        request = {
            "format": "midi", "filename": "lesson-notes.mid", "source_id": self.imported.source_id,
            "authorize_local_export": False, "acknowledge_export_losses": True,
        }
        self.assertEqual(422, self._request("POST", "/api/exports/music", body=json.dumps(request).encode(), headers=headers)[0])
        request["authorize_local_export"] = True
        status, _, raw = self._request("POST", "/api/exports/music", body=json.dumps(request).encode(), headers=headers)
        result = json.loads(raw)
        self.assertEqual(201, status)
        self.assertEqual("lesson-notes.mid", result["filename"])
        self.assertEqual([self.imported.source_id], result["source_ids"])
        self.assertNotIn(str(self.project), raw.decode())
        exported = self.project / "exports" / "lesson-notes.mid"
        self.assertTrue(exported.is_file())
        self.assertEqual(0o600, stat.S_IMODE(exported.stat().st_mode))

    def test_transcript_export_endpoint_keeps_machine_evidence_explicit(self) -> None:
        def add_speech(payload: dict[str, object]) -> None:
            payload["generators"].append({  # type: ignore[index,union-attr]
                "id": "generator:speech-export", "kind": "machine", "name": "fixture speech", "version": "1",
                "model": "fixture", "weight_hash_state": "sha256:" + "b" * 64,
            })
            payload["targets"].append({  # type: ignore[index,union-attr]
                "id": "target:speech-export", "source_id": self.imported.source_id,
                "selector": {"stream_id": "audio", "start_us": 0, "duration_us": 500_000, "spatial": None},
                "musical_selector": None, "alignment_state": "not_applicable",
            })
            payload["events"].append({  # type: ignore[index,union-attr]
                "id": "event:speech-export", "type": "speech", "scope": "evidence", "actor_id": "actor:researcher",
                "target_ids": ["target:speech-export"], "body": {"format": "text", "value": "Repeat the cadence."},
                "alternatives": [], "generator_id": "generator:speech-export", "rights_id": self.imported.rights_id,
                "layer": "normalized_hypothesis", "confidence": {"kind": "model_probability", "value": 0.8}, "review_status": "machine_suggested",
            })
        ProjectStore(self.project).mutate(add_speech)
        snapshot = json.loads(self._request("GET", "/api/workbench")[2])
        headers = {"Content-Type": "application/json", "Origin": self.server.origin, "X-NoteWitness-CSRF": snapshot["csrf_token"]}
        request = {
            "acknowledge_export_losses": True, "authorize_local_export": False,
            "evidence_layer": "include_machine_suggestions", "filename": "lesson-transcript.txt", "format": "text",
            "pause_threshold_ms": None, "source_id": self.imported.source_id, "timestamp_interval_ms": 60_000,
            "visible_timestamps": True,
        }
        self.assertEqual(422, self._request("POST", "/api/exports/transcript", body=json.dumps(request).encode(), headers=headers)[0])
        request["authorize_local_export"] = True
        status, _, raw = self._request("POST", "/api/exports/transcript", body=json.dumps(request).encode(), headers=headers)
        result = json.loads(raw)
        self.assertEqual(201, status)
        self.assertEqual("include_machine_suggestions", result["evidence_layer"])
        self.assertEqual("lesson-transcript.txt", result["filename"])
        self.assertNotIn(str(self.project), raw.decode())
        exported = self.project / "exports" / "lesson-transcript.txt"
        self.assertIn("[UNREVIEWED MACHINE SUGGESTION]", exported.read_text(encoding="utf-8"))
        self.assertEqual(0o600, stat.S_IMODE(exported.stat().st_mode))
