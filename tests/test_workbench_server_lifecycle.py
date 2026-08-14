from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from notewitness.application.workbench_processing import WorkbenchProcessingError
from notewitness.presentation.workbench_server import LocalWorkbenchServer, serve_workbench
from notewitness.project import initialize_project

from tests.workbench_server_test_support import WorkbenchServerTestCase


class WorkbenchServerInitializationTests(unittest.TestCase):
    def test_launcher_keeps_token_out_of_output_when_opening_browser(self) -> None:
        server = Mock()
        server.origin = "http://127.0.0.1:8765"
        server.launch_url = "http://127.0.0.1:8765/launch/private-token"
        stdout = io.StringIO()
        with (
            patch("notewitness.presentation.workbench_server.LocalWorkbenchServer", return_value=server),
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
            patch("notewitness.presentation.workbench_server.LocalWorkbenchServer", return_value=server),
            patch("notewitness.presentation.workbench_server.webbrowser.open", return_value=False),
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
            with patch.object(LocalWorkbenchServer, "server_bind", side_effect=PermissionError("bind denied")):
                with self.assertRaisesRegex(PermissionError, "bind denied"):
                    LocalWorkbenchServer(project)


class WorkbenchServerLifecycleTests(WorkbenchServerTestCase):
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
        with self.assertRaisesRegex(WorkbenchProcessingError, "workbench_processing_shutdown_timeout"):
            self.server.server_close()
        self.assertFalse(self.server._processing_closed)
        self.server.server_close()
        self.assertTrue(self.server._processing_closed)
        self.assertEqual(2, processing.calls)
