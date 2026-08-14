from __future__ import annotations

import os
from pathlib import Path
import threading
import time

from notewitness.application.workbench_processing import (
    WorkbenchJobKind,
    WorkbenchJobState,
    WorkbenchProcessingService,
)
from notewitness.local_tools import (
    BoundedLocalToolRunner,
    LocalToolCancelled,
    discover_local_tool,
)


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
        from notewitness.media_ingest import ingest_media
        from notewitness.project import initialize_project

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
