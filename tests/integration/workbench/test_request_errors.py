from __future__ import annotations

import json

from tests.integration.workbench.support import WorkbenchServerTestCase


class WorkbenchRequestErrorTests(WorkbenchServerTestCase):
    def _post(self, path: str, body: bytes, headers: dict[str, str]) -> tuple[int, dict[str, str]]:
        token = json.loads(self._request("GET", "/api/workbench")[2])["csrf_token"]
        status, _, raw = self._request(
            "POST",
            path,
            body=body,
            headers={"Origin": self.server.origin, "X-NoteWitness-CSRF": token, **headers},
        )
        return status, json.loads(raw)

    def test_malformed_json_body_is_unprocessable(self) -> None:
        self.assertEqual(
            (422, {"error": "Request body is not valid JSON."}),
            self._post("/api/bookmarks", b"{not json", {"Content-Type": "application/json"}),
        )
        self.assertEqual(
            (422, {"error": "Request JSON must be an object."}),
            self._post("/api/bookmarks", b"[]", {"Content-Type": "application/json"}),
        )
        self.assertEqual(
            (422, {"error": "JSON endpoints require application/json."}),
            self._post("/api/bookmarks", b"{}", {"Content-Type": "text/plain"}),
        )

    def test_mismatched_import_container_is_unprocessable(self) -> None:
        self.assertEqual(
            (422, {"error": "Imported bytes do not match the selected media container."}),
            self._post(
                "/api/imports",
                b"not a wav container",
                {"Content-Type": "audio/wav", "X-Media-Name": "broken.wav"},
            ),
        )
        self.assertEqual(
            (422, {"error": "Imported media name and declared type disagree."}),
            self._post(
                "/api/imports",
                b"RIFF",
                {"Content-Type": "audio/wav", "X-Media-Name": "broken.mp3"},
            ),
        )


class WorkbenchActorErrorTests(WorkbenchServerTestCase):
    def test_duplicate_actor_is_unprocessable_not_a_dropped_connection(self) -> None:
        snapshot = json.loads(self._request("GET", "/api/workbench")[2])
        headers = {
            "Content-Type": "application/json",
            "Origin": self.server.origin,
            "X-NoteWitness-CSRF": snapshot["csrf_token"],
        }
        request = {
            "actor_id": "actor:duplicate-teacher",
            "role": "teacher",
            "visibility": "project",
            "instrument_role": None,
            "project_sha256": snapshot["project"]["sha256"],
        }
        status, _, raw = self._request("POST", "/api/actors", body=json.dumps(request).encode(), headers=headers)
        self.assertEqual(201, status)
        request["project_sha256"] = json.loads(raw)["project_sha256"]
        status, _, raw = self._request("POST", "/api/actors", body=json.dumps(request).encode(), headers=headers)
        self.assertEqual(422, status)
        self.assertIn("error", json.loads(raw))
