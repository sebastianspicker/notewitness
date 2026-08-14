"""Shared live-server fixture for focused workbench HTTP contract tests."""

from __future__ import annotations

from http.client import HTTPConnection, HTTPResponse
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from urllib.parse import urlsplit

from notewitness.application.transcript_review_service import add_project_actor
from notewitness.application.workbench_processing import WorkbenchJobKind
from notewitness.media_ingest import ingest_media
from notewitness.presentation.workbench_server import LocalWorkbenchServer
from notewitness.project import initialize_project


class WorkbenchServerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        parent = Path(self.temporary.name).resolve()
        parent.chmod(0o700)
        self.project = parent / "study"
        initialize_project(self.project)
        media = parent / "lesson.wav"
        media.write_bytes(b"synthetic playback media")
        media.chmod(0o600)
        self.imported = ingest_media(self.project, media, create_restricted_rights=True)
        add_project_actor(str(self.project), actor_id="actor:researcher", role="researcher")
        self.server = LocalWorkbenchServer(self.project, processing_executor=_ImmediateExecutor())
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()
        self.session_cookie = ""
        self.launch_path = urlsplit(self.server.launch_url).path
        status, headers, _ = self._request("GET", self.launch_path, authenticated=False)
        self.assertEqual(303, status)
        self.assertEqual("/", headers["Location"])
        self.session_cookie = headers["Set-Cookie"].split(";", 1)[0]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        authenticated: bool = True,
    ) -> tuple[int, "MappingHeaders", bytes]:
        request_headers = dict(headers or {})
        if authenticated and self.session_cookie:
            request_headers.setdefault("Cookie", self.session_cookie)
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request(method, path, body=body, headers=request_headers)
        response: HTTPResponse = connection.getresponse()
        result = response.status, MappingHeaders(response.getheaders()), response.read()
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
            WorkbenchJobKind.TRANSCRIPTION, WorkbenchJobKind.COMPLETE
        }:
            mark_step_completed("transcription")
        if "analysis" not in completed_steps and kind in {
            WorkbenchJobKind.ANALYSIS, WorkbenchJobKind.COMPLETE
        }:
            mark_step_completed("analysis")
