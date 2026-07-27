from __future__ import annotations

import os
from pathlib import Path
import platform
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest import mock

from notewitness.local_tools import (
    BoundedLocalToolRunner,
    LocalToolCancelled,
    LocalToolError,
    LocalToolIdentityChanged,
    LocalToolOutputLimit,
    LocalToolProcessLeak,
    LocalToolUnavailable,
    MAX_TOOL_OUTPUT_BYTES,
    discover_local_tool,
)


class LocalToolTests(unittest.TestCase):
    def test_discovery_requires_an_executable_absolute_path(self) -> None:
        with self.assertRaisesRegex(LocalToolUnavailable, "absolute"):
            discover_local_tool("python3", "relative/python3")
        with self.assertRaises(LocalToolUnavailable):
            discover_local_tool("missing", "/definitely/missing/tool")

    def test_discovery_binds_private_executable_bytes_and_stat_identity(self) -> None:
        with TemporaryDirectory() as temporary:
            executable = Path(temporary).resolve() / "tool"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o700)
            tool = discover_local_tool("tool", executable)

            self.assertGreater(tool.identity.size_bytes, 0)
            self.assertEqual(64, len(tool.identity.sha256))
            tool.require_unchanged()

            executable.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            executable.chmod(0o700)
            with self.assertRaisesRegex(LocalToolIdentityChanged, "changed"):
                tool.require_unchanged()

    def test_discovery_rejects_group_or_world_writable_executable(self) -> None:
        with TemporaryDirectory() as temporary:
            executable = Path(temporary).resolve() / "tool"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o722)

            with self.assertRaisesRegex(LocalToolUnavailable, "writable"):
                discover_local_tool("tool", executable)

    def test_runner_uses_no_shell_and_bounds_environment(self) -> None:
        with TemporaryDirectory() as temporary:
            workdir = Path(temporary).resolve()
            workdir.chmod(0o700)
            tool = discover_local_tool("python3", "/usr/bin/python3")

            result = BoundedLocalToolRunner(tool).run(
                (
                    "-c",
                    "import os; print(os.getenv('PYTHONNOUSERSITE')); "
                    "print(os.getenv('UNAPPROVED_VALUE')); "
                    "print(os.getenv('TMPDIR')); print(os.getenv('PATH'))",
                ),
                working_directory=workdir,
                timeout_seconds=10,
                deny_network=False,
            )

        output = result.stdout.splitlines()
        self.assertEqual(["1", "None"], output[:2])
        self.assertEqual(str(workdir), output[2])
        self.assertEqual("/usr/bin:/bin", output[3])
        self.assertFalse(result.network_isolated)

    def test_runner_spawns_fixed_helper_without_child_setup_callback(self) -> None:
        with TemporaryDirectory() as temporary:
            workdir = Path(temporary).resolve()
            workdir.chmod(0o700)
            tool = discover_local_tool("python3", "/usr/bin/python3")
            calls: list[dict[str, object]] = []
            original_popen = subprocess.Popen

            def capture_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
                calls.append(dict(kwargs))
                return original_popen(*args, **kwargs)  # type: ignore[arg-type]

            with mock.patch(
                "notewitness.local_tools.subprocess.Popen",
                side_effect=capture_popen,
            ):
                result = BoundedLocalToolRunner(tool).run(
                    ("-c", "print('helper-ran')"),
                    working_directory=workdir,
                    timeout_seconds=10,
                    deny_network=False,
                )

        self.assertEqual("helper-ran", result.stdout.strip())
        self.assertEqual(1, len(calls))
        self.assertNotIn("preexec_fn", calls[0])

    def test_runner_concurrent_cancellations_complete_without_hanging(self) -> None:
        with TemporaryDirectory() as temporary:
            workdir = Path(temporary).resolve()
            workdir.chmod(0o700)
            tool = discover_local_tool("python3", "/usr/bin/python3")
            cancellation_events = [threading.Event() for _ in range(3)]
            failures: list[BaseException] = []
            failures_lock = threading.Lock()

            def run_until_cancelled(event: threading.Event) -> None:
                try:
                    BoundedLocalToolRunner(tool).run(
                        ("-c", "import time; time.sleep(30)"),
                        working_directory=workdir,
                        timeout_seconds=60,
                        deny_network=False,
                        cancellation_requested=event.is_set,
                    )
                except BaseException as exc:
                    with failures_lock:
                        failures.append(exc)

            threads = [
                threading.Thread(target=run_until_cancelled, args=(event,))
                for event in cancellation_events
            ]
            for thread in threads:
                thread.start()
            time.sleep(0.4)
            for event in cancellation_events:
                event.set()
            for thread in threads:
                thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(3, len(failures))
        self.assertTrue(all(isinstance(error, LocalToolCancelled) for error in failures))

    def test_runner_terminates_on_stdout_or_stderr_quota(self) -> None:
        with TemporaryDirectory() as temporary:
            workdir = Path(temporary).resolve()
            workdir.chmod(0o700)
            tool = discover_local_tool("python3", "/usr/bin/python3")
            for descriptor in (1, 2):
                with self.subTest(descriptor=descriptor):
                    script = (
                        "import os\n"
                        f"payload = b'x' * {MAX_TOOL_OUTPUT_BYTES + 1}\n"
                        f"while payload:\n written = os.write({descriptor}, payload)\n"
                        " payload = payload[written:]\n"
                    )
                    with self.assertRaises(LocalToolOutputLimit):
                        BoundedLocalToolRunner(tool).run(
                            ("-c", script),
                            working_directory=workdir,
                            timeout_seconds=10,
                            deny_network=False,
                        )

    def test_runner_kills_successful_launcher_background_process(self) -> None:
        with TemporaryDirectory() as temporary:
            workdir = Path(temporary).resolve()
            workdir.chmod(0o700)
            tool = discover_local_tool("python3", "/usr/bin/python3")
            child_script = "import time; time.sleep(30)"
            script = (
                "import subprocess\n"
                "subprocess.Popen("
                f"[{sys.executable!r}, '-c', {child_script!r}], "
                "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            )

            with self.assertRaises(LocalToolProcessLeak):
                BoundedLocalToolRunner(tool).run(
                    ("-c", script),
                    working_directory=workdir,
                    timeout_seconds=10,
                    deny_network=False,
                )

    def test_runner_cancellation_terminates_the_process_group(self) -> None:
        with TemporaryDirectory() as temporary:
            workdir = Path(temporary).resolve()
            workdir.chmod(0o700)
            child_pid_path = workdir / "child.pid"
            tool = discover_local_tool("python3", "/usr/bin/python3")
            child_script = (
                "import signal, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "time.sleep(30)"
            )
            script = (
                "import pathlib, subprocess, sys, time\n"
                "child = subprocess.Popen("
                f"[{sys.executable!r}, '-c', {child_script!r}])\n"
                "pathlib.Path('child.pid').write_text(str(child.pid))\n"
                "time.sleep(30)\n"
            )
            started = time.monotonic()

            with self.assertRaises(LocalToolCancelled):
                BoundedLocalToolRunner(tool).run(
                    ("-c", script),
                    working_directory=workdir,
                    timeout_seconds=10,
                    deny_network=False,
                    cancellation_requested=child_pid_path.exists,
                )

            self.assertLess(time.monotonic() - started, 4)
            child_pid = int(child_pid_path.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 1
            while _process_exists(child_pid) and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertFalse(_process_exists(child_pid))

    def test_runner_keeps_polling_after_output_pipes_close(self) -> None:
        with TemporaryDirectory() as temporary:
            workdir = Path(temporary).resolve()
            workdir.chmod(0o700)
            tool = discover_local_tool("python3", "/usr/bin/python3")
            cancel_at = time.monotonic() + 0.3
            started = time.monotonic()

            with self.assertRaises(LocalToolCancelled):
                BoundedLocalToolRunner(tool).run(
                    (
                        "-c",
                        "import os, time; os.close(1); os.close(2); time.sleep(30)",
                    ),
                    working_directory=workdir,
                    timeout_seconds=10,
                    deny_network=False,
                    cancellation_requested=lambda: time.monotonic() >= cancel_at,
                )

            self.assertLess(time.monotonic() - started, 4)

    @unittest.skipUnless(platform.system() == "Darwin", "macOS isolation contract")
    def test_runner_denies_network_even_when_child_catches_the_error(self) -> None:
        with TemporaryDirectory() as temporary:
            workdir = Path(temporary).resolve()
            workdir.chmod(0o700)
            tool = discover_local_tool("python3", "/usr/bin/python3")
            script = (
                "import socket\n"
                "try:\n"
                " socket.socket().connect(('127.0.0.1', 9))\n"
                "except OSError:\n"
                " print('network-denied')\n"
            )

            result = BoundedLocalToolRunner(tool).run(
                ("-c", script),
                working_directory=workdir,
                timeout_seconds=10,
                deny_network=True,
            )

        self.assertEqual("network-denied", result.stdout.strip())
        self.assertTrue(result.network_isolated)

    def test_runner_rejects_non_private_working_directory(self) -> None:
        with TemporaryDirectory() as temporary:
            workdir = Path(temporary).resolve()
            workdir.chmod(0o755)
            tool = discover_local_tool("python3", "/usr/bin/python3")
            with self.assertRaisesRegex(LocalToolError, "deny group"):
                BoundedLocalToolRunner(tool).run(
                    ("-c", "print('never runs')"),
                    working_directory=workdir,
                    timeout_seconds=10,
                    deny_network=False,
                )

    def test_runner_rejects_replaced_executable_after_successful_output(self) -> None:
        with TemporaryDirectory() as temporary:
            workdir = Path(temporary).resolve()
            workdir.chmod(0o700)
            executable = workdir / "approved-tool"
            executable.write_text(
                "#!/usr/bin/python3\n"
                "import pathlib\n"
                "pathlib.Path(__file__).write_text('#!/bin/sh\\nexit 0\\n')\n"
                "print('untrusted output')\n",
                encoding="utf-8",
            )
            executable.chmod(0o700)
            tool = discover_local_tool("approved-tool", executable)

            with self.assertRaises(LocalToolIdentityChanged):
                BoundedLocalToolRunner(tool).run(
                    (),
                    working_directory=workdir,
                    timeout_seconds=10,
                    deny_network=False,
                )

    def test_runner_rejects_replaced_executable_on_cancellation(self) -> None:
        with TemporaryDirectory() as temporary:
            workdir = Path(temporary).resolve()
            workdir.chmod(0o700)
            executable = workdir / "approved-tool"
            changed_marker = workdir / "changed"
            executable.write_text(
                "#!/usr/bin/python3\n"
                "import pathlib, time\n"
                "pathlib.Path(__file__).write_text('#!/bin/sh\\nexit 0\\n')\n"
                "pathlib.Path('changed').write_text('yes')\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            executable.chmod(0o700)
            tool = discover_local_tool("approved-tool", executable)

            with self.assertRaises(LocalToolIdentityChanged):
                BoundedLocalToolRunner(tool).run(
                    (),
                    working_directory=workdir,
                    timeout_seconds=10,
                    deny_network=False,
                    cancellation_requested=changed_marker.exists,
                )
def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


if __name__ == "__main__":
    unittest.main()
