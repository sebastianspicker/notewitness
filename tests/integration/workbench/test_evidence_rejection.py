from __future__ import annotations

from copy import deepcopy
import json

from notewitness.projects.store import ProjectStore

from tests.integration.workbench.support import WorkbenchServerTestCase


class WorkbenchEvidenceRejectionTests(WorkbenchServerTestCase):
    event_id = "event:machine-review-candidate"

    def _seed_machine_suggestion(self) -> tuple[str, dict[str, object]]:
        def append(payload: dict[str, object]) -> None:
            payload["targets"].append(  # type: ignore[union-attr]
                {
                    "id": "target:machine-review-candidate",
                    "source_id": self.imported.source_id,
                    "selector": {
                        "stream_id": "timeline",
                        "start_us": 12_000_000,
                        "duration_us": 5_000_000,
                        "spatial": None,
                    },
                    "musical_selector": None,
                    "alignment_state": "unknown",
                }
            )
            payload["generators"].append(  # type: ignore[union-attr]
                {
                    "id": "generator:machine-review-fixture",
                    "kind": "machine",
                    "name": "Local review fixture",
                    "version": "1",
                    "model": "fixture-model",
                    "weight_hash_state": "fixture-only",
                }
            )
            payload["events"].append(  # type: ignore[union-attr]
                {
                    "id": self.event_id,
                    "type": "speech",
                    "scope": "evidence",
                    "actor_id": "actor:researcher",
                    "target_ids": ["target:machine-review-candidate"],
                    "body": {
                        "format": "text",
                        "value": "Release the final note.",
                    },
                    "alternatives": [],
                    "generator_id": "generator:machine-review-fixture",
                    "rights_id": self.imported.rights_id,
                    "layer": "normalized_hypothesis",
                    "confidence": {"kind": "fixture"},
                    "review_status": "machine_suggested",
                }
            )

        snapshot = ProjectStore(self.project).mutate(append)
        event = next(
            item for item in snapshot.payload["events"] if item["id"] == self.event_id
        )
        return snapshot.sha256, deepcopy(event)

    def _headers(self, csrf_token: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Origin": self.server.origin,
            "X-NoteWitness-CSRF": csrf_token,
        }

    def _rejection_body(self, sha256: str) -> bytes:
        return json.dumps(
            {
                "event_id": self.event_id,
                "author_id": "actor:researcher",
                "reason": "Rejected after checking the exact source span.",
                "project_sha256": sha256,
            }
        ).encode()

    def test_rejection_is_append_only_terminal_and_removed_from_queue(self) -> None:
        before_sha, original_event = self._seed_machine_suggestion()
        workbench = json.loads(self._request("GET", "/api/workbench")[2])
        headers = self._headers(workbench["csrf_token"])

        status, _, raw = self._request(
            "POST",
            "/api/review/reject",
            body=self._rejection_body(before_sha),
            headers=headers,
        )

        self.assertEqual(201, status)
        result = json.loads(raw)
        self.assertEqual([], result["record_ids"])
        self.assertEqual(1, len(result["revision_ids"]))
        self.assertNotEqual(before_sha, result["project_sha256"])
        after = ProjectStore(self.project).load()
        self.assertEqual(
            original_event,
            next(item for item in after.payload["events"] if item["id"] == self.event_id),
        )
        revision = next(
            item
            for item in after.payload["revisions"]
            if item["id"] == result["revision_ids"][0]
        )
        self.assertEqual("reject", revision["operation"])
        self.assertEqual(self.event_id, revision["record_id"])
        projected = json.loads(self._request("GET", "/api/workbench")[2])["lesson"]
        self.assertNotIn(
            self.event_id,
            [item["event_id"] for item in projected["transcript_suggestions"]],
        )
        revision_link = next(
            item
            for item in projected["source_graph"]["revisions"]
            if item["record_id"] == self.event_id
        )
        self.assertEqual(result["revision_ids"], revision_link["revision_ids"])

        repeat_status, _, _ = self._request(
            "POST",
            "/api/review/reject",
            body=self._rejection_body(after.sha256),
            headers=headers,
        )
        self.assertEqual(422, repeat_status)
        accept_status, _, _ = self._request(
            "POST",
            "/api/review/accept",
            body=json.dumps(
                {
                    "event_id": self.event_id,
                    "actor_id": "actor:researcher",
                    "author_id": "actor:researcher",
                    "reason": "Attempted acceptance after rejection.",
                    "project_sha256": after.sha256,
                }
            ).encode(),
            headers=headers,
        )
        self.assertEqual(422, accept_status)
        self.assertEqual(after.sha256, ProjectStore(self.project).load().sha256)

    def test_rejection_route_fails_closed_before_mutation(self) -> None:
        before_sha, _ = self._seed_machine_suggestion()
        workbench = json.loads(self._request("GET", "/api/workbench")[2])
        headers = self._headers(workbench["csrf_token"])
        body = self._rejection_body(before_sha)

        self.assertEqual(
            401,
            self._request(
                "POST",
                "/api/review/reject",
                body=body,
                headers=headers,
                authenticated=False,
            )[0],
        )
        self.assertEqual(
            403,
            self._request(
                "POST",
                "/api/review/reject",
                body=body,
                headers={key: value for key, value in headers.items() if key != "X-NoteWitness-CSRF"},
            )[0],
        )
        extra_field = json.loads(body)
        extra_field["actor_id"] = "actor:researcher"
        self.assertEqual(
            422,
            self._request(
                "POST",
                "/api/review/reject",
                body=json.dumps(extra_field).encode(),
                headers=headers,
            )[0],
        )
        stale = json.loads(body)
        stale["project_sha256"] = "0" * 64
        self.assertEqual(
            409,
            self._request(
                "POST",
                "/api/review/reject",
                body=json.dumps(stale).encode(),
                headers=headers,
            )[0],
        )
        self.assertEqual(before_sha, ProjectStore(self.project).load().sha256)
