"""Shared live-server fixture for focused workbench HTTP contract tests."""

from __future__ import annotations

from http.client import HTTPConnection, HTTPResponse
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from urllib.parse import urlsplit

from notewitness.analysis.local_tools.contracts import LocalToolCancelled
from notewitness.analysis.local_tools.discovery import discover_local_tool
from notewitness.analysis.local_tools.runner import BoundedLocalToolRunner
from notewitness.lessons.actors import add_project_actor
from notewitness.workbench.jobs import WorkbenchJobKind, WorkbenchJobState
from notewitness.workbench.processing import WorkbenchProcessingService
from notewitness.projects.media import ingest_media
from notewitness.workbench.server import LocalWorkbenchServer
from notewitness.projects.initialize import initialize_project


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
        self.session_token = ""
        self.launch_path = urlsplit(self.server.launch_url).path
        status, headers, _ = self._request("GET", self.launch_path, authenticated=False)
        self.assertEqual(303, status)
        location = urlsplit(headers["Location"])
        self.assertEqual("/", location.path)
        self.assertTrue(location.fragment.startswith("session="))
        self.assertNotIn("Set-Cookie", headers)
        self.session_token = location.fragment.removeprefix("session=")

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
        if authenticated and self.session_token:
            request_headers.setdefault("X-NoteWitness-Session", self.session_token)
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


class ControlledExecutor:
    def __init__(
        self,
        *,
        fail_first: bool = False,
        block: bool = False,
        complete_ready: bool = True,
    ) -> None:
        self.fail_first = fail_first
        self.block = block
        self.complete_ready = complete_ready
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls: list[tuple[WorkbenchJobKind, str, frozenset[str]]] = []

    def status(self) -> dict[str, object]:
        return {
            "analysis_ready": True, "complete_ready": self.complete_ready,
            "configured": True, "network_used": False, "transcription_ready": True,
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
        self.calls.append((kind, source_id, completed_steps))
        report_progress(20, "Running deterministic local fixture")
        self.entered.set()
        if self.block:
            deadline = time.monotonic() + 5
            while not self.release.wait(0.02):
                if cancellation_requested():
                    raise RuntimeError("fixture_cancelled")
                if time.monotonic() >= deadline:
                    raise RuntimeError("fixture_timeout")
        if self.fail_first and len(self.calls) == 1:
            mark_step_completed("transcription")
            raise RuntimeError("fixture_failure")
        if "transcription" not in completed_steps:
            mark_step_completed("transcription")
        if kind in {WorkbenchJobKind.ANALYSIS, WorkbenchJobKind.COMPLETE}:
            mark_step_completed("analysis")
        report_progress(99, "Fixture complete")


class TermIgnoringToolExecutor:
    """Exercise shutdown against the bounded local-tool process lifecycle."""

    def __init__(self, working_directory: Path) -> None:
        self.working_directory = working_directory
        self.entered = threading.Event()
        self.child_pid_path = working_directory / "term-ignoring-tool.pid"

    def status(self) -> dict[str, object]:
        return {
            "analysis_ready": True, "complete_ready": True, "configured": True,
            "network_used": False, "transcription_ready": True,
        }

    def execute(self, kind: WorkbenchJobKind, source_id: str, *, job_id: str,
                attempt: int, cancellation_requested: object, report_progress: object,
                completed_steps: frozenset[str], mark_step_completed: object) -> None:
        assert callable(cancellation_requested)
        assert callable(report_progress)
        tool = discover_local_tool("python3", "/usr/bin/python3")
        script = (
            "import os, pathlib, signal, sys, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')\n"
            "time.sleep(60)\n"
        )
        self.entered.set()
        try:
            BoundedLocalToolRunner(tool).run(
                ("-c", script, os.fspath(self.child_pid_path)),
                working_directory=self.working_directory,
                timeout_seconds=60,
                deny_network=False,
                cancellation_requested=cancellation_requested,
            )
        except LocalToolCancelled:
            raise


class UncooperativeExecutor:
    """A worker fixture that cannot complete until the test explicitly releases it."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def status(self) -> dict[str, object]:
        return {
            "analysis_ready": True, "complete_ready": True, "configured": True,
            "network_used": False, "transcription_ready": True,
        }

    def execute(self, *args: object, **kwargs: object) -> None:
        self.entered.set()
        self.release.wait()


class WorkbenchProcessingTestCase:
    def setUp(self) -> None:
        from tempfile import TemporaryDirectory
        from notewitness.projects.media import ingest_media
        from notewitness.projects.initialize import initialize_project

        self.temporary = TemporaryDirectory()
        parent = Path(self.temporary.name).resolve()
        parent.chmod(0o700)
        self.project = parent / "study"
        initialize_project(self.project)
        media = parent / "lesson.wav"
        media.write_bytes(b"private fixture media")
        media.chmod(0o600)
        self.imported = ingest_media(self.project, media, create_restricted_rights=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()


def wait_for_state(
    service: WorkbenchProcessingService,
    job_id: str,
    states: set[WorkbenchJobState],
) -> object:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = service.store.get(job_id)
        if job is not None and job.state in states:
            return job
        time.sleep(0.01)
    current = service.store.get(job_id)
    raise AssertionError(f"job did not reach {states}; current={current}")


def wait_for_path(path: Path) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.01)
    raise AssertionError(f"tool did not create {path}")


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
