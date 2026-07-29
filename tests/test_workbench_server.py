from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from http.client import HTTPConnection, HTTPResponse
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
import stat
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from notewitness.application.transcript_review_service import add_project_actor
from notewitness.application.workbench import WorkbenchMutation
from notewitness.application.workbench_processing import (
    WorkbenchJobKind,
    WorkbenchProcessingError,
)
from notewitness.media_ingest import ingest_media
from notewitness.presentation.workbench_server import (
    LocalWorkbenchServer,
    serve_workbench,
)
from notewitness.project import initialize_project
from notewitness.project_store import ProjectStore


class WorkbenchServerInitializationTests(unittest.TestCase):
    def test_launcher_keeps_token_out_of_output_when_opening_browser(self) -> None:
        server = Mock()
        server.origin = "http://127.0.0.1:8765"
        server.launch_url = "http://127.0.0.1:8765/launch/private-token"
        stdout = io.StringIO()

        with (
            patch(
                "notewitness.presentation.workbench_server.LocalWorkbenchServer",
                return_value=server,
            ),
            patch("notewitness.presentation.workbench_server.webbrowser.open") as opened,
            redirect_stdout(stdout),
        ):
            opened.return_value = True
            serve_workbench("/private/project", open_browser=True)

        opened.assert_called_once_with(server.launch_url, new=2, autoraise=True)
        self.assertNotIn("private-token", stdout.getvalue())
        self.assertEqual(
            {"network_mode": "loopback_only", "url": "http://127.0.0.1:8765/"},
            json.loads(stdout.getvalue()),
        )
        server.serve_forever.assert_called_once_with(poll_interval=0.25)
        server.server_close.assert_called_once_with()

    def test_launcher_prints_single_use_url_when_browser_does_not_open(self) -> None:
        server = Mock()
        server.origin = "http://127.0.0.1:8765"
        server.launch_url = "http://127.0.0.1:8765/launch/private-token"
        stdout = io.StringIO()

        with (
            patch(
                "notewitness.presentation.workbench_server.LocalWorkbenchServer",
                return_value=server,
            ),
            patch(
                "notewitness.presentation.workbench_server.webbrowser.open",
                return_value=False,
            ),
            redirect_stdout(stdout),
        ):
            serve_workbench("/private/project", open_browser=True)

        self.assertEqual(
            {
                "launch_url": server.launch_url,
                "network_mode": "loopback_only",
                "notice": "Browser launch failed; this single-use URL grants access to the private workbench.",
            },
            json.loads(stdout.getvalue()),
        )
        server.serve_forever.assert_called_once_with(poll_interval=0.25)
        server.server_close.assert_called_once_with()

    def test_bind_failure_preserves_the_original_error(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            parent.chmod(0o700)
            project = parent / "study"
            initialize_project(project)

            with patch.object(
                LocalWorkbenchServer,
                "server_bind",
                side_effect=PermissionError("bind denied"),
            ):
                with self.assertRaisesRegex(PermissionError, "bind denied"):
                    LocalWorkbenchServer(project)


class WorkbenchServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        parent = Path(self.temporary.name).resolve()
        parent.chmod(0o700)
        self.project = parent / "study"
        initialize_project(self.project)
        media = parent / "lesson.wav"
        media.write_bytes(b"synthetic playback media")
        media.chmod(0o600)
        self.imported = ingest_media(
            self.project,
            media,
            create_restricted_rights=True,
        )
        add_project_actor(
            str(self.project),
            actor_id="actor:researcher",
            role="researcher",
        )
        self.server = LocalWorkbenchServer(
            self.project,
            processing_executor=_ImmediateExecutor(),
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.thread.start()
        self.session_cookie = ""
        self.launch_path = urlsplit(self.server.launch_url).path
        status, headers, _ = self._request(
            "GET",
            self.launch_path,
            authenticated=False,
        )
        self.assertEqual(303, status)
        self.assertEqual("/", headers["Location"])
        self.session_cookie = headers["Set-Cookie"].split(";", 1)[0]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def test_snapshot_assets_range_playback_and_security_headers(self) -> None:
        status, headers, index = self._request("GET", "/", authenticated=False)
        self.assertEqual(200, status)
        self.assertIn(b"NoteWitness: local evidence workbench", index)
        self.assertIn(b'/assets/notewitness-mark.svg', index)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertIn(
            "script-src 'self'; style-src 'self' 'unsafe-inline'",
            headers["Content-Security-Policy"],
        )
        self.assertEqual("microphone=(self), camera=()", headers["Permissions-Policy"])

        status, _, script = self._request("GET", "/assets/app.js")
        self.assertEqual(200, status)
        status, _, actions = self._request("GET", "/assets/js/actions.mjs")
        self.assertEqual(200, status)
        status, _, processing = self._request("GET", "/assets/js/processing.mjs")
        self.assertEqual(200, status)
        status, _, api_module = self._request("GET", "/assets/js/api.mjs")
        self.assertEqual(200, status)
        status, _, playback = self._request("GET", "/assets/js/playback.mjs")
        self.assertEqual(200, status)
        client = script + actions + processing + api_module + playback
        self.assertIn(b"actor_id: actorId", actions)
        self.assertIn(b"author_id: author.id", actions)
        self.assertIn(b'/api/imports', actions)
        self.assertIn(b'/api/jobs', processing)
        submit_dialog = actions[actions.index(b"async function submitDialog"):]
        self.assertLess(
            submit_dialog.index(b'if (dialog.mode === "reviewer-setup")'),
            submit_dialog.index(b"const author = requireHumanActor();"),
        )
        self.assertNotIn(b"window.prompt", client)
        self.assertNotIn(b"window.alert", client)

        status, headers, mark = self._request(
            "GET", "/assets/notewitness-mark.svg"
        )
        self.assertEqual(200, status)
        self.assertEqual("image/svg+xml", headers["Content-Type"])
        self.assertIn(b"<title>NoteWitness</title>", mark)

        status, _, barrel = self._request("GET", "/assets/workbench_ui.mjs")
        self.assertEqual(200, status)
        self.assertIn(b"/assets/ui/utils.mjs", barrel)
        self.assertIn(b"/assets/ui/shell.mjs", barrel)
        ui_parts = [barrel]
        for module_path in (
            "/assets/ui/utils.mjs",
            "/assets/ui/shell.mjs",
            "/assets/ui/timeline.mjs",
            "/assets/ui/panels.mjs",
            "/assets/ui/processing.mjs",
            "/assets/ui/context.mjs",
            "/assets/ui/transport.mjs",
        ):
            status, _, body = self._request("GET", module_path)
            self.assertEqual(200, status, module_path)
            ui_parts.append(body)
        ui = b"\n".join(ui_parts)
        self.assertIn(b'["&", "&amp;"]', ui)
        self.assertIn(b'["<", "&lt;"]', ui)
        self.assertIn(b"data-author", ui)
        self.assertIn(b"Choose reviewer", ui)
        self.assertIn(b"Choose project actor", ui)
        self.assertIn(b"Lesson at a glance", ui)
        self.assertIn(b"Review queue", ui)
        self.assertIn(b"Full transcript", ui)
        self.assertIn(b"Lesson notes", ui)
        self.assertIn(b"human_evidence_eligible", ui)
        self.assertIn(b'const canRevise = typeof item.body_value === "string"', ui)
        self.assertIn(b'${canRevise ? `<button class="secondary-button" data-revise=', ui)
        self.assertIn(b"Set up reviewer", ui)
        self.assertIn(b"/api/actors", actions)

        status, _, raw = self._request("GET", "/api/workbench")
        snapshot = json.loads(raw)
        self.assertEqual(200, status)
        self.assertEqual("offline", snapshot["project"]["network_mode"])
        self.assertEqual(self.imported.source_id, snapshot["media"][0]["source_id"])
        self.assertEqual("Lesson recording 1", snapshot["media"][0]["display_name"])
        self.assertTrue(snapshot["capabilities"]["tuner"])

        action_headers = {
            "Content-Type": "application/json",
            "Origin": self.server.origin,
            "X-NoteWitness-CSRF": snapshot["csrf_token"],
        }
        status, _, raw = self._request(
            "POST",
            "/api/actors",
            body=json.dumps(
                {
                    "actor_id": "actor:local-test-reviewer",
                    "role": "teacher",
                    "project_sha256": snapshot["project"]["sha256"],
                }
            ).encode(),
            headers=action_headers,
        )
        self.assertEqual(201, status)
        self.assertEqual(64, len(json.loads(raw)["project_sha256"]))
        self.assertTrue(
            any(
                item["id"] == "actor:local-test-reviewer"
                for item in json.loads(self._request("GET", "/api/workbench")[2])["actors"]
            )
        )

        status, _, raw = self._request("GET", "/api/jobs")
        processing = json.loads(raw)
        self.assertEqual(200, status)
        self.assertTrue(processing["runtime"]["transcription_ready"])

        status, headers, raw = self._request(
            "GET",
            snapshot["media"][0]["url"],
            headers={"Range": "bytes=0-8"},
        )
        self.assertEqual(206, status)
        self.assertEqual("bytes 0-8/24", headers["Content-Range"])
        self.assertEqual(b"synthetic", raw)

        media_path = self.project / self.imported.relative_path
        media_path.write_bytes(b"tampered playback media!")
        media_path.chmod(0o600)
        status, _, _ = self._request("GET", snapshot["media"][0]["url"])
        self.assertEqual(404, status)

        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        connection.putrequest("GET", "/", skip_host=True)
        connection.putheader("Host", "attacker.invalid")
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(421, response.status)
        response.read()
        connection.close()

    def test_session_authentication_gates_private_reads_and_mutations(self) -> None:
        status, _, _ = self._request(
            "GET",
            "/api/workbench",
            authenticated=False,
        )
        self.assertEqual(401, status)

        status, _, raw = self._request("GET", "/api/workbench")
        self.assertEqual(200, status)
        snapshot = json.loads(raw)
        self.assertNotIn("session", snapshot)
        self.assertNotIn("launch", snapshot)

        status, _, _ = self._request(
            "GET",
            snapshot["media"][0]["url"],
            authenticated=False,
        )
        self.assertEqual(401, status)

        before = ProjectStore(self.project).load().sha256
        status, _, _ = self._request(
            "POST",
            "/api/bookmarks",
            body=json.dumps(
                {
                    "author_id": "actor:researcher",
                    "duration_us": 0,
                    "label": "Unauthorized bookmark",
                    "project_sha256": before,
                    "source_id": self.imported.source_id,
                    "start_us": 125_000,
                }
            ).encode(),
            headers={
                "Content-Type": "application/json",
                "Origin": self.server.origin,
                "X-NoteWitness-CSRF": snapshot["csrf_token"],
            },
            authenticated=False,
        )
        self.assertEqual(401, status)
        self.assertEqual(before, ProjectStore(self.project).load().sha256)

        status, _, _ = self._request(
            "GET",
            "/api/jobs",
            headers={"Cookie": "notewitness_session=invalid"},
            authenticated=False,
        )
        self.assertEqual(401, status)

        logged = io.StringIO()
        with redirect_stderr(logged):
            status, _, _ = self._request(
                "GET",
                self.launch_path,
                authenticated=False,
            )
        self.assertEqual(401, status)
        self.assertNotIn(self.launch_path.rsplit("/", 1)[-1], logged.getvalue())
        self.assertIn("GET /launch/:token 401", logged.getvalue())

    def test_import_job_status_and_privacy_safe_request_logging(self) -> None:
        _, _, raw = self._request("GET", "/api/workbench")
        snapshot = json.loads(raw)
        token = snapshot["csrf_token"]
        wav = b"RIFF" + (36).to_bytes(4, "little") + b"WAVE" + (b"\x00" * 32)
        status, _, raw = self._request(
            "POST",
            "/api/imports",
            body=wav,
            headers={
                "Content-Type": "audio/wav",
                "Origin": self.server.origin,
                "X-Media-Name": "second-lesson.wav",
                "X-NoteWitness-CSRF": token,
            },
        )
        imported = json.loads(raw)
        self.assertEqual(201, status)
        self.assertFalse(imported["network_used"])
        self.assertEqual(2, len(ProjectStore(self.project).load().payload["sources"]))

        status, _, raw = self._request(
            "POST",
            "/api/jobs",
            body=json.dumps(
                {"kind": "complete", "source_id": imported["source_id"]}
            ).encode(),
            headers={
                "Content-Type": "application/json",
                "Origin": self.server.origin,
                "X-NoteWitness-CSRF": token,
            },
        )
        job = json.loads(raw)
        self.assertEqual(202, status)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            _, _, jobs_raw = self._request("GET", "/api/jobs")
            jobs = json.loads(jobs_raw)["jobs"]
            current = next(item for item in jobs if item["job_id"] == job["job_id"])
            if current["state"] == "completed":
                break
            time.sleep(0.01)
        self.assertEqual("completed", current["state"])
        self.assertEqual(["analysis", "transcription"], current["completed_steps"])

        sentinel = "source:participant-alice-1980"

        def rename_source(payload: dict[str, object]) -> None:
            sources = payload["sources"]
            assert isinstance(sources, list)
            for source in sources:
                if isinstance(source, dict) and source.get("id") == imported["source_id"]:
                    source["id"] = sentinel

        ProjectStore(self.project).mutate(rename_source)
        media_url = f"/api/media/{sentinel.replace(':', '%3A')}"
        logged = io.StringIO()
        with redirect_stderr(logged):
            status, _, _ = self._request("GET", media_url)
        self.assertEqual(200, status)
        self.assertNotIn(sentinel, logged.getvalue())
        self.assertNotIn("participant-alice", logged.getvalue())
        self.assertIn("GET /api/media/:source 200", logged.getvalue())

    def test_csrf_tuner_metronome_bookmark_and_capture_endpoints(self) -> None:
        _, _, raw = self._request("GET", "/api/workbench")
        snapshot = json.loads(raw)
        token = snapshot["csrf_token"]
        action_headers = {
            "Content-Type": "application/json",
            "Origin": self.server.origin,
            "X-NoteWitness-CSRF": token,
        }

        legacy_header = "X-" + "Music" + "Transcript-CSRF"
        status, _, _ = self._request(
            "POST",
            "/api/tuner",
            body=json.dumps({"frequency_hz": 440.0}).encode(),
            headers={
                "Content-Type": "application/json",
                "Origin": self.server.origin,
                legacy_header: token,
            },
        )
        self.assertEqual(403, status)

        status, _, _ = self._request(
            "POST",
            "/api/tuner",
            body=json.dumps({"frequency_hz": 440.0}).encode(),
            headers={
                "Content-Type": "application/json",
                "Origin": self.server.origin,
                "X-NoteWitness-CSRF": "wrong",
            },
        )
        self.assertEqual(403, status)

        status, _, raw = self._request(
            "POST",
            "/api/tuner",
            body=json.dumps({"frequency_hz": 440.0}).encode(),
            headers=action_headers,
        )
        reading = json.loads(raw)
        self.assertEqual(200, status)
        self.assertEqual("A", reading["note_name"])
        self.assertEqual(4, reading["octave"])

        status, _, raw = self._request(
            "POST",
            "/api/metronome",
            body=json.dumps(
                {
                    "bars": 1,
                    "beats_per_bar": 4,
                    "bpm": 120,
                    "subdivisions": 1,
                }
            ).encode(),
            headers=action_headers,
        )
        self.assertEqual(200, status)
        self.assertEqual(4, len(json.loads(raw)["ticks"]))

        status, _, raw = self._request(
            "POST",
            "/api/bookmarks",
            body=json.dumps(
                {
                    "author_id": "actor:researcher",
                    "duration_us": 0,
                    "label": "Exact attack",
                    "project_sha256": snapshot["project"]["sha256"],
                    "source_id": self.imported.source_id,
                    "start_us": 125_000,
                }
            ).encode(),
            headers=action_headers,
        )
        bookmark = json.loads(raw)
        self.assertEqual(201, status)
        self.assertEqual(2, len(bookmark["record_ids"]))
        self.assertEqual(
            1,
            len(json.loads(self._request("GET", "/api/workbench")[2])["lesson"]["bookmarks"]),
        )

        capture = b"\x1a\x45\xdf\xa3" + (b"\x00" * 20)
        status, _, raw = self._request(
            "POST",
            "/api/captures",
            body=capture,
            headers={
                "Content-Type": "audio/webm",
                "Origin": self.server.origin,
                "X-Capture-Author": "actor:researcher",
                "X-Capture-Duration-Ms": "1200",
                "X-Capture-Name": "browser-take.webm",
                "X-Capture-Started-At": "2026-07-18T12:00:00Z",
                "X-NoteWitness-CSRF": token,
            },
        )
        imported = json.loads(raw)
        self.assertEqual(201, status)
        self.assertEqual(len(capture), imported["byte_count"])
        graph = ProjectStore(self.project).load().payload
        self.assertEqual(2, len(graph["sources"]))
        capture_event = next(
            item for item in graph["events"] if item["type"] == "local:capture"
        )
        self.assertEqual("actor:researcher", capture_event["actor_id"])
        self.assertEqual(1200, capture_event["body"]["value"]["duration_ms"])
        self.assertEqual(imported["source_id"], capture_event["body"]["value"]["source_id"])

        status, _, _ = self._request(
            "POST",
            "/api/captures",
            body=b"not a webm container",
            headers={
                "Content-Type": "audio/webm",
                "Origin": self.server.origin,
                "X-Capture-Author": "actor:researcher",
                "X-Capture-Duration-Ms": "1200",
                "X-Capture-Name": "invalid.webm",
                "X-Capture-Started-At": "2026-07-18T12:00:00Z",
                "X-NoteWitness-CSRF": token,
            },
        )
        self.assertEqual(422, status)
        self.assertEqual(2, len(ProjectStore(self.project).load().payload["sources"]))
        self.assertEqual([], list((self.project / "runs").glob("capture-*")))

        current_sha = imported["project_sha256"]
        with patch(
            "notewitness.presentation.workbench_server.revise_evidence_annotation",
            return_value=WorkbenchMutation(
                ("event:revised",),
                ("revision:replace",),
                "a" * 64,
            ),
        ) as revise:
            status, _, _ = self._request(
                "POST",
                "/api/review/revise",
                body=json.dumps(
                    {
                        "actor_id": "actor:researcher",
                        "author_id": "actor:researcher",
                        "event_id": "event:accepted",
                        "project_sha256": current_sha,
                        "reason": "Corrected locally",
                        "replacement_text": "Play more lightly.",
                    }
                ).encode(),
                headers=action_headers,
            )
        self.assertEqual(201, status)
        revise.assert_called_once()

        with patch(
            "notewitness.presentation.workbench_server.set_practice_task_completed",
            return_value=WorkbenchMutation(
                ("event:practice",),
                ("revision:practice",),
                "b" * 64,
            ),
        ) as practice:
            status, _, _ = self._request(
                "POST",
                "/api/practice",
                body=json.dumps(
                    {
                        "author_id": "actor:researcher",
                        "completed": True,
                        "project_sha256": current_sha,
                        "task_id": "task:practice",
                    }
                ).encode(),
                headers=action_headers,
            )
        self.assertEqual(201, status)
        practice.assert_called_once()

    def test_relation_review_endpoints_require_trusted_append_only_mutations(self) -> None:
        snapshot = json.loads(self._request("GET", "/api/workbench")[2])
        headers = {
            "Content-Type": "application/json",
            "Origin": self.server.origin,
            "X-NoteWitness-CSRF": snapshot["csrf_token"],
        }
        request = {
            "author_id": "actor:researcher",
            "project_sha256": snapshot["project"]["sha256"],
            "reason": "Reviewed local transcript evidence.",
            "relation_id": "relation:pending",
        }
        with patch(
            "notewitness.presentation.workbench_server.accept_relation_suggestion",
            return_value=WorkbenchMutation(
                ("relation:accepted",),
                ("revision:supersede", "revision:adjudicate"),
                "a" * 64,
            ),
        ) as accept:
            status, _, _ = self._request(
                "POST", "/api/review/relations/accept", body=json.dumps(request).encode(), headers=headers
            )
        self.assertEqual(201, status)
        accept.assert_called_once()
        with patch(
            "notewitness.presentation.workbench_server.reject_relation_suggestion",
            return_value=WorkbenchMutation((), ("revision:reject",), "b" * 64),
        ) as reject:
            status, _, _ = self._request(
                "POST", "/api/review/relations/reject", body=json.dumps(request).encode(), headers=headers
            )
        self.assertEqual(201, status)
        reject.assert_called_once()

    def test_music_export_endpoint_is_explicit_private_and_path_safe(self) -> None:
        store = ProjectStore(self.project)

        def add_note(payload: dict[str, object]) -> None:
            payload["generators"].append(  # type: ignore[index,union-attr]
                {
                    "id": "generator:notes",
                    "kind": "machine",
                    "name": "fixture notes",
                    "version": "1",
                    "model": "fixture",
                    "weight_hash_state": "sha256:" + "a" * 64,
                }
            )
            payload["targets"].append(  # type: ignore[index,union-attr]
                {
                    "id": "target:note-one",
                    "source_id": self.imported.source_id,
                    "selector": {
                        "stream_id": "audio",
                        "start_us": 100_000,
                        "duration_us": 500_000,
                        "spatial": None,
                    },
                    "musical_selector": None,
                    "alignment_state": "unknown",
                }
            )
            payload["events"].append(  # type: ignore[index,union-attr]
                {
                    "id": "event:note-one",
                    "type": "local:note",
                    "scope": "evidence",
                    "actor_id": "actor:researcher",
                    "target_ids": ["target:note-one"],
                    "body": {
                        "format": "application/vnd.notewitness.note+json",
                        "value": {
                            "midi_pitch": 64.0,
                            "source_track_id": "instrument-track-01",
                        },
                    },
                    "alternatives": [],
                    "generator_id": "generator:notes",
                    "rights_id": self.imported.rights_id,
                    "layer": "normalized_hypothesis",
                    "confidence": {"kind": "adapter_reported", "value": 0.8},
                    "review_status": "machine_suggested",
                }
            )

        store.mutate(add_note)
        snapshot = json.loads(self._request("GET", "/api/workbench")[2])
        headers = {
            "Content-Type": "application/json",
            "Origin": self.server.origin,
            "X-NoteWitness-CSRF": snapshot["csrf_token"],
        }
        request = {
            "format": "midi",
            "filename": "lesson-notes.mid",
            "source_id": self.imported.source_id,
            "authorize_local_export": False,
            "acknowledge_export_losses": True,
        }
        status, _, _ = self._request(
            "POST",
            "/api/exports/music",
            body=json.dumps(request).encode(),
            headers=headers,
        )
        self.assertEqual(422, status)

        request["authorize_local_export"] = True
        status, _, raw = self._request(
            "POST",
            "/api/exports/music",
            body=json.dumps(request).encode(),
            headers=headers,
        )
        result = json.loads(raw)
        self.assertEqual(201, status)
        self.assertEqual("lesson-notes.mid", result["filename"])
        self.assertEqual([self.imported.source_id], result["source_ids"])
        self.assertNotIn(str(self.project), raw.decode())
        exported = self.project / "exports" / "lesson-notes.mid"
        self.assertTrue(exported.is_file())
        self.assertEqual(0o600, stat.S_IMODE(exported.stat().st_mode))

    def test_transcript_export_endpoint_keeps_machine_evidence_explicit(self) -> None:
        store = ProjectStore(self.project)

        def add_speech(payload: dict[str, object]) -> None:
            payload["generators"].append({  # type: ignore[index,union-attr]
                "id": "generator:speech-export",
                "kind": "machine",
                "name": "fixture speech",
                "version": "1",
                "model": "fixture",
                "weight_hash_state": "sha256:" + "b" * 64,
            })
            payload["targets"].append({  # type: ignore[index,union-attr]
                "id": "target:speech-export",
                "source_id": self.imported.source_id,
                "selector": {
                    "stream_id": "audio",
                    "start_us": 0,
                    "duration_us": 500_000,
                    "spatial": None,
                },
                "musical_selector": None,
                "alignment_state": "not_applicable",
            })
            payload["events"].append({  # type: ignore[index,union-attr]
                "id": "event:speech-export",
                "type": "speech",
                "scope": "evidence",
                "actor_id": "actor:researcher",
                "target_ids": ["target:speech-export"],
                "body": {"format": "text", "value": "Repeat the cadence."},
                "alternatives": [],
                "generator_id": "generator:speech-export",
                "rights_id": self.imported.rights_id,
                "layer": "normalized_hypothesis",
                "confidence": {"kind": "model_probability", "value": 0.8},
                "review_status": "machine_suggested",
            })

        store.mutate(add_speech)
        snapshot = json.loads(self._request("GET", "/api/workbench")[2])
        headers = {
            "Content-Type": "application/json",
            "Origin": self.server.origin,
            "X-NoteWitness-CSRF": snapshot["csrf_token"],
        }
        request = {
            "acknowledge_export_losses": True,
            "authorize_local_export": False,
            "evidence_layer": "include_machine_suggestions",
            "filename": "lesson-transcript.txt",
            "format": "text",
            "pause_threshold_ms": None,
            "source_id": self.imported.source_id,
            "timestamp_interval_ms": 60_000,
            "visible_timestamps": True,
        }
        status, _, _ = self._request(
            "POST",
            "/api/exports/transcript",
            body=json.dumps(request).encode(),
            headers=headers,
        )
        self.assertEqual(422, status)

        request["authorize_local_export"] = True
        status, _, raw = self._request(
            "POST",
            "/api/exports/transcript",
            body=json.dumps(request).encode(),
            headers=headers,
        )
        result = json.loads(raw)
        self.assertEqual(201, status)
        self.assertEqual("include_machine_suggestions", result["evidence_layer"])
        self.assertEqual("lesson-transcript.txt", result["filename"])
        self.assertNotIn(str(self.project), raw.decode())
        exported = self.project / "exports" / "lesson-transcript.txt"
        self.assertIn(
            "[UNREVIEWED MACHINE SUGGESTION]",
            exported.read_text(encoding="utf-8"),
        )
        self.assertEqual(0o600, stat.S_IMODE(exported.stat().st_mode))

    def test_server_close_keeps_processing_shutdown_retryable_after_timeout(self) -> None:
        class _FlakyProcessing:
            def __init__(self) -> None:
                self.calls = 0

            def close(self) -> None:
                self.calls += 1
                if self.calls == 1:
                    raise WorkbenchProcessingError("workbench_processing_shutdown_timeout")

        processing = _FlakyProcessing()
        self.server.processing.close()
        self.server.processing = processing  # type: ignore[assignment]

        with self.assertRaisesRegex(
            WorkbenchProcessingError,
            "workbench_processing_shutdown_timeout",
        ):
            self.server.server_close()
        self.assertFalse(self.server._processing_closed)

        self.server.server_close()
        self.assertTrue(self.server._processing_closed)
        self.assertEqual(2, processing.calls)

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        authenticated: bool = True,
    ) -> tuple[int, MappingHeaders, bytes]:
        request_headers = dict(headers or {})
        if authenticated and self.session_cookie:
            request_headers.setdefault("Cookie", self.session_cookie)
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request(method, path, body=body, headers=request_headers)
        response: HTTPResponse = connection.getresponse()
        response_body = response.read()
        result = response.status, MappingHeaders(response.getheaders()), response_body
        connection.close()
        return result


class MappingHeaders(dict[str, str]):
    def __init__(self, pairs: list[tuple[str, str]]) -> None:
        super().__init__(pairs)


class _ImmediateExecutor:
    def status(self) -> dict[str, object]:
        return {
            "analysis_ready": True,
            "complete_ready": True,
            "configured": True,
            "missing_complete_modalities": [],
            "modalities": {
                "activity_segmentation": True,
                "anonymous_diarization": True,
                "instrument_detection": True,
                "instrument_diarization": True,
                "note_transcription": True,
                "speech_transcription": True,
            },
            "network_used": False,
            "transcription_ready": True,
        }

    def execute(
        self,
        kind: WorkbenchJobKind,
        source_id: str,
        *,
        job_id: str,
        attempt: int,
        cancellation_requested: object,
        report_progress: object,
        completed_steps: frozenset[str],
        mark_step_completed: object,
    ) -> None:
        assert callable(cancellation_requested)
        assert callable(report_progress)
        assert callable(mark_step_completed)
        assert job_id.startswith("job:workbench-")
        assert attempt >= 1
        report_progress(50, "Running local fixture")
        if "transcription" not in completed_steps and kind in {
            WorkbenchJobKind.TRANSCRIPTION,
            WorkbenchJobKind.COMPLETE,
        }:
            mark_step_completed("transcription")
        if "analysis" not in completed_steps and kind in {
            WorkbenchJobKind.ANALYSIS,
            WorkbenchJobKind.COMPLETE,
        }:
            mark_step_completed("analysis")


if __name__ == "__main__":
    unittest.main()
