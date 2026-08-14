from __future__ import annotations

from contextlib import redirect_stderr
from http.client import HTTPConnection
import io
import json
import time

from notewitness.project_store import ProjectStore

from tests.workbench_server_test_support import WorkbenchServerTestCase


class WorkbenchServerAccessTests(WorkbenchServerTestCase):
    def test_snapshot_assets_range_playback_and_security_headers(self) -> None:
        status, headers, index = self._request("GET", "/", authenticated=False)
        self.assertEqual(200, status)
        self.assertIn(b"NoteWitness: local evidence workbench", index)
        self.assertIn(b'/assets/notewitness-mark.svg', index)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertIn("script-src 'self'; style-src 'self' 'unsafe-inline'", headers["Content-Security-Policy"])
        self.assertEqual("microphone=(self), camera=()", headers["Permissions-Policy"])
        assets = (
            "/assets/app.js", "/assets/js/actions.mjs", "/assets/js/review_actions.mjs",
            "/assets/js/capture_actions.mjs", "/assets/js/processing.mjs", "/assets/js/api.mjs",
            "/assets/js/playback.mjs", "/assets/workbench_ui.mjs", "/assets/notewitness-mark.svg",
            "/assets/ui/utils.mjs", "/assets/ui/value_utils.mjs", "/assets/ui/filter_utils.mjs",
            "/assets/ui/render_utils.mjs", "/assets/ui/timeline_utils.mjs", "/assets/ui/shell.mjs",
            "/assets/ui/timeline.mjs", "/assets/ui/panels.mjs", "/assets/ui/processing.mjs",
            "/assets/ui/context.mjs", "/assets/ui/transport.mjs",
        )
        bodies: dict[str, bytes] = {}
        for asset in assets:
            status, asset_headers, body = self._request("GET", asset)
            self.assertEqual(200, status, asset)
            bodies[asset] = body
            if asset.endswith("mark.svg"):
                self.assertEqual("image/svg+xml", asset_headers["Content-Type"])
        client = b"".join(bodies.values())
        self.assertIn(b"actor_id: actorId", bodies["/assets/js/review_actions.mjs"])
        self.assertIn(b"author_id: author.id", bodies["/assets/js/review_actions.mjs"])
        self.assertIn(b"/api/imports", bodies["/assets/js/capture_actions.mjs"])
        self.assertIn(b"/api/jobs", bodies["/assets/js/processing.mjs"])
        self.assertIn(b"<title>NoteWitness</title>", bodies["/assets/notewitness-mark.svg"])
        self.assertIn(b"Choose reviewer", client)
        self.assertIn(b"Set up reviewer", client)
        self.assertNotIn(b"window.prompt", client)
        self.assertNotIn(b"window.alert", client)
        snapshot = json.loads(self._request("GET", "/api/workbench")[2])
        self.assertEqual("offline", snapshot["project"]["network_mode"])
        self.assertEqual(self.imported.source_id, snapshot["media"][0]["source_id"])
        self.assertTrue(snapshot["capabilities"]["tuner"])
        action_headers = {
            "Content-Type": "application/json", "Origin": self.server.origin,
            "X-NoteWitness-CSRF": snapshot["csrf_token"],
        }
        status, _, raw = self._request(
            "POST", "/api/actors", body=json.dumps({
                "actor_id": "actor:local-test-reviewer", "role": "teacher",
                "project_sha256": snapshot["project"]["sha256"],
            }).encode(), headers=action_headers,
        )
        self.assertEqual(201, status)
        self.assertEqual(64, len(json.loads(raw)["project_sha256"]))
        self.assertTrue(any(item["id"] == "actor:local-test-reviewer" for item in json.loads(self._request("GET", "/api/workbench")[2])["actors"]))
        self.assertTrue(json.loads(self._request("GET", "/api/jobs")[2])["runtime"]["transcription_ready"])
        status, headers, raw = self._request("GET", snapshot["media"][0]["url"], headers={"Range": "bytes=0-8"})
        self.assertEqual(206, status)
        self.assertEqual("bytes 0-8/24", headers["Content-Range"])
        self.assertEqual(b"synthetic", raw)
        media_path = self.project / self.imported.relative_path
        media_path.write_bytes(b"tampered playback media!")
        media_path.chmod(0o600)
        self.assertEqual(404, self._request("GET", snapshot["media"][0]["url"])[0])
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        connection.putrequest("GET", "/", skip_host=True)
        connection.putheader("Host", "attacker.invalid")
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(421, response.status)
        response.read()
        connection.close()

    def test_session_authentication_gates_private_reads_and_mutations(self) -> None:
        self.assertEqual(401, self._request("GET", "/api/workbench", authenticated=False)[0])
        snapshot = json.loads(self._request("GET", "/api/workbench")[2])
        self.assertNotIn("session", snapshot)
        self.assertNotIn("launch", snapshot)
        self.assertEqual(401, self._request("GET", snapshot["media"][0]["url"], authenticated=False)[0])
        before = ProjectStore(self.project).load().sha256
        status, _, _ = self._request(
            "POST", "/api/bookmarks", body=json.dumps({
                "author_id": "actor:researcher", "duration_us": 0, "label": "Unauthorized bookmark",
                "project_sha256": before, "source_id": self.imported.source_id, "start_us": 125_000,
            }).encode(), headers={
                "Content-Type": "application/json", "Origin": self.server.origin,
                "X-NoteWitness-CSRF": snapshot["csrf_token"],
            }, authenticated=False,
        )
        self.assertEqual(401, status)
        self.assertEqual(before, ProjectStore(self.project).load().sha256)
        self.assertEqual(401, self._request("GET", "/api/jobs", headers={"Cookie": "notewitness_session=invalid"}, authenticated=False)[0])
        logged = io.StringIO()
        with redirect_stderr(logged):
            self.assertEqual(401, self._request("GET", self.launch_path, authenticated=False)[0])
        self.assertNotIn(self.launch_path.rsplit("/", 1)[-1], logged.getvalue())
        self.assertIn("GET /launch/:token 401", logged.getvalue())

    def test_import_job_status_and_privacy_safe_request_logging(self) -> None:
        snapshot = json.loads(self._request("GET", "/api/workbench")[2])
        wav = b"RIFF" + (36).to_bytes(4, "little") + b"WAVE" + (b"\x00" * 32)
        status, _, raw = self._request(
            "POST", "/api/imports", body=wav, headers={
                "Content-Type": "audio/wav", "Origin": self.server.origin,
                "X-Media-Name": "second-lesson.wav", "X-NoteWitness-CSRF": snapshot["csrf_token"],
            },
        )
        imported = json.loads(raw)
        self.assertEqual(201, status)
        self.assertFalse(imported["network_used"])
        self.assertEqual(2, len(ProjectStore(self.project).load().payload["sources"]))
        status, _, raw = self._request(
            "POST", "/api/jobs", body=json.dumps({"kind": "complete", "source_id": imported["source_id"]}).encode(),
            headers={"Content-Type": "application/json", "Origin": self.server.origin, "X-NoteWitness-CSRF": snapshot["csrf_token"]},
        )
        job = json.loads(raw)
        self.assertEqual(202, status)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            jobs = json.loads(self._request("GET", "/api/jobs")[2])["jobs"]
            current = next(item for item in jobs if item["job_id"] == job["job_id"])
            if current["state"] == "completed":
                break
            time.sleep(0.01)
        self.assertEqual("completed", current["state"])
        self.assertEqual(["analysis", "transcription"], current["completed_steps"])
        sentinel = "source:participant-alice-1980"
        def rename_source(payload: dict[str, object]) -> None:
            for source in payload["sources"]:  # type: ignore[index,union-attr]
                if isinstance(source, dict) and source.get("id") == imported["source_id"]:
                    source["id"] = sentinel
        ProjectStore(self.project).mutate(rename_source)
        logged = io.StringIO()
        with redirect_stderr(logged):
            self.assertEqual(200, self._request("GET", f"/api/media/{sentinel.replace(':', '%3A')}")[0])
        self.assertNotIn(sentinel, logged.getvalue())
        self.assertNotIn("participant-alice", logged.getvalue())
        self.assertIn("GET /api/media/:source 200", logged.getvalue())
