from __future__ import annotations

import json
from unittest.mock import patch

from notewitness.application.workbench import WorkbenchMutation
from notewitness.project_store import ProjectStore

from tests.workbench_server_test_support import WorkbenchServerTestCase


class WorkbenchServerMutationTests(WorkbenchServerTestCase):
    def test_csrf_tuner_metronome_bookmark_and_capture_endpoints(self) -> None:
        snapshot = json.loads(self._request("GET", "/api/workbench")[2])
        token = snapshot["csrf_token"]
        action_headers = {
            "Content-Type": "application/json", "Origin": self.server.origin,
            "X-NoteWitness-CSRF": token,
        }
        status, _, _ = self._request(
            "POST", "/api/tuner", body=json.dumps({"frequency_hz": 440.0}).encode(),
            headers={"Content-Type": "application/json", "Origin": self.server.origin, "X-NoteWitness-CSRF": "wrong"},
        )
        self.assertEqual(403, status)
        status, _, raw = self._request("POST", "/api/tuner", body=json.dumps({"frequency_hz": 440.0}).encode(), headers=action_headers)
        reading = json.loads(raw)
        self.assertEqual(200, status)
        self.assertEqual(("A", 4), (reading["note_name"], reading["octave"]))
        status, _, raw = self._request(
            "POST", "/api/metronome", body=json.dumps({"bars": 1, "beats_per_bar": 4, "bpm": 120, "subdivisions": 1}).encode(), headers=action_headers
        )
        self.assertEqual(200, status)
        self.assertEqual(4, len(json.loads(raw)["ticks"]))
        status, _, raw = self._request(
            "POST", "/api/bookmarks", body=json.dumps({
                "author_id": "actor:researcher", "duration_us": 0, "label": "Exact attack",
                "project_sha256": snapshot["project"]["sha256"], "source_id": self.imported.source_id,
                "start_us": 125_000,
            }).encode(), headers=action_headers,
        )
        self.assertEqual(201, status)
        self.assertEqual(2, len(json.loads(raw)["record_ids"]))
        self.assertEqual(1, len(json.loads(self._request("GET", "/api/workbench")[2])["lesson"]["bookmarks"]))
        capture = b"\x1a\x45\xdf\xa3" + (b"\x00" * 20)
        capture_headers = {
            "Content-Type": "audio/webm", "Origin": self.server.origin,
            "X-Capture-Author": "actor:researcher", "X-Capture-Duration-Ms": "1200",
            "X-Capture-Name": "browser-take.webm", "X-Capture-Started-At": "2026-07-18T12:00:00Z",
            "X-NoteWitness-CSRF": token,
        }
        status, _, raw = self._request("POST", "/api/captures", body=capture, headers=capture_headers)
        imported = json.loads(raw)
        self.assertEqual(201, status)
        self.assertEqual(len(capture), imported["byte_count"])
        graph = ProjectStore(self.project).load().payload
        capture_event = next(item for item in graph["events"] if item["type"] == "local:capture")
        self.assertEqual("actor:researcher", capture_event["actor_id"])
        self.assertEqual(1200, capture_event["body"]["value"]["duration_ms"])
        self.assertEqual(imported["source_id"], capture_event["body"]["value"]["source_id"])
        self.assertEqual(422, self._request("POST", "/api/captures", body=b"not a webm container", headers=capture_headers)[0])
        self.assertEqual([], list((self.project / "runs").glob("capture-*")))
        current_sha = imported["project_sha256"]
        with patch("notewitness.presentation.workbench_server.revise_evidence_annotation", return_value=WorkbenchMutation(("event:revised",), ("revision:replace",), "a" * 64)) as revise:
            status, _, _ = self._request("POST", "/api/review/revise", body=json.dumps({
                "actor_id": "actor:researcher", "author_id": "actor:researcher", "event_id": "event:accepted",
                "project_sha256": current_sha, "reason": "Corrected locally", "replacement_text": "Play more lightly.",
            }).encode(), headers=action_headers)
        self.assertEqual(201, status)
        revise.assert_called_once()
        with patch("notewitness.presentation.workbench_server.set_practice_task_completed", return_value=WorkbenchMutation(("event:practice",), ("revision:practice",), "b" * 64)) as practice:
            status, _, _ = self._request("POST", "/api/practice", body=json.dumps({
                "author_id": "actor:researcher", "completed": True, "project_sha256": current_sha, "task_id": "task:practice",
            }).encode(), headers=action_headers)
        self.assertEqual(201, status)
        practice.assert_called_once()

    def test_relation_review_endpoints_require_trusted_append_only_mutations(self) -> None:
        snapshot = json.loads(self._request("GET", "/api/workbench")[2])
        headers = {"Content-Type": "application/json", "Origin": self.server.origin, "X-NoteWitness-CSRF": snapshot["csrf_token"]}
        request = {"author_id": "actor:researcher", "project_sha256": snapshot["project"]["sha256"], "reason": "Reviewed local transcript evidence.", "relation_id": "relation:pending"}
        with patch("notewitness.presentation.workbench_server.accept_relation_suggestion", return_value=WorkbenchMutation(("relation:accepted",), ("revision:supersede", "revision:adjudicate"), "a" * 64)) as accept:
            status, _, _ = self._request("POST", "/api/review/relations/accept", body=json.dumps(request).encode(), headers=headers)
        self.assertEqual(201, status)
        accept.assert_called_once()
        with patch("notewitness.presentation.workbench_server.reject_relation_suggestion", return_value=WorkbenchMutation((), ("revision:reject",), "b" * 64)) as reject:
            status, _, _ = self._request("POST", "/api/review/relations/reject", body=json.dumps(request).encode(), headers=headers)
        self.assertEqual(201, status)
        reject.assert_called_once()
