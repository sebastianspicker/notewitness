"""Bounded process communication and process-group cleanup."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time
from typing import TYPE_CHECKING, Callable, Mapping

from notewitness._local_tool_contracts import (
    MAX_TOOL_OUTPUT_BYTES,
    LocalToolCancelled,
    LocalToolError,
    LocalToolFailure,
    LocalToolOutputLimit,
    LocalToolProcessLeak,
    LocalToolResult,
    LocalToolTimeout,
)

if TYPE_CHECKING:
    from pathlib import Path

    from notewitness._local_tool_discovery import LocalTool


def execute_bounded(
    *,
    tool: LocalTool,
    launcher_command: tuple[str, ...],
    workdir: Path,
    environment: Mapping[str, str],
    timeout_seconds: int,
    network_isolated: bool,
    cancellation_requested: Callable[[], bool] | None,
    popen_factory: Callable[..., subprocess.Popen[bytes]],
) -> LocalToolResult:
    """Spawn an approved command and enforce its complete process lifecycle."""

    tool.require_unchanged()
    started = time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    try:
        process = popen_factory(
            launcher_command,
            cwd=workdir,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
            close_fds=True,
        )
        try:
            stdout_raw, stderr_raw = communicate_bounded(
                process,
                timeout_seconds=timeout_seconds,
                started=started,
                cancellation_requested=cancellation_requested,
            )
            return_code = process.wait(timeout=1)
        except BaseException:
            terminate_remaining_process_group(process.pid)
            raise
        if terminate_remaining_process_group(process.pid):
            raise LocalToolProcessLeak(
                f"Local tool {tool.name!r} left background processes running."
            )
        stdout = stdout_raw.decode("utf-8", errors="replace")
        stderr = stderr_raw.decode("utf-8", errors="replace")
        if return_code != 0:
            raise LocalToolFailure(tool.name, return_code, stdout=stdout, stderr=stderr)
        return LocalToolResult(
            tool_name=tool.name,
            return_code=return_code,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=False,
            stderr_truncated=False,
            duration_ms=max(0, round((time.monotonic() - started) * 1_000)),
            network_isolated=network_isolated,
        )
    finally:
        if process is not None:
            tool.require_unchanged()


def communicate_bounded(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: int,
    started: float,
    cancellation_requested: Callable[[], bool] | None,
) -> tuple[bytes, bytes]:
    if process.stdout is None or process.stderr is None:
        terminate_process_group(process)
        raise LocalToolError("Local tool output pipes were not created.")
    selector = selectors.DefaultSelector()
    streams = {
        process.stdout.fileno(): ("stdout", bytearray()),
        process.stderr.fileno(): ("stderr", bytearray()),
    }
    for descriptor in streams:
        os.set_blocking(descriptor, False)
        selector.register(descriptor, selectors.EVENT_READ)
    deadline = started + timeout_seconds
    next_cancellation_probe = started
    try:
        while selector.get_map() or process.poll() is None:
            now, next_cancellation_probe = check_execution_limits(
                process,
                timeout_seconds=timeout_seconds,
                deadline=deadline,
                next_cancellation_probe=next_cancellation_probe,
                cancellation_requested=cancellation_requested,
            )
            drain_output_or_wait(
                process,
                selector,
                streams,
                process_poll_interval(
                    deadline=deadline,
                    now=now,
                    next_cancellation_probe=next_cancellation_probe,
                    cancellation_requested=cancellation_requested,
                ),
            )
        if cancellation_requested is not None and cancellation_requested():
            terminate_process_group(process)
            raise LocalToolCancelled("Local tool execution was cancelled.")
        try:
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            terminate_process_group(process)
            raise LocalToolTimeout(
                f"Local tool exceeded {timeout_seconds} seconds."
            ) from exc
        return bytes(streams[process.stdout.fileno()][1]), bytes(
            streams[process.stderr.fileno()][1]
        )
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()


def check_execution_limits(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: int,
    deadline: float,
    next_cancellation_probe: float,
    cancellation_requested: Callable[[], bool] | None,
) -> tuple[float, float]:
    now = time.monotonic()
    if cancellation_requested is not None and now >= next_cancellation_probe:
        if cancellation_requested():
            terminate_process_group(process)
            raise LocalToolCancelled("Local tool execution was cancelled.")
        next_cancellation_probe = now + 0.25
    if deadline - now <= 0:
        terminate_process_group(process)
        raise LocalToolTimeout(f"Local tool exceeded {timeout_seconds} seconds.")
    return now, next_cancellation_probe


def process_poll_interval(
    *,
    deadline: float,
    now: float,
    next_cancellation_probe: float,
    cancellation_requested: Callable[[], bool] | None,
) -> float:
    wait_seconds = min(0.25, deadline - now)
    if cancellation_requested is not None:
        return min(wait_seconds, max(0.0, next_cancellation_probe - now))
    return wait_seconds


def drain_output_or_wait(
    process: subprocess.Popen[bytes],
    selector: selectors.BaseSelector,
    streams: dict[int, tuple[str, bytearray]],
    wait_seconds: float,
) -> None:
    if not selector.get_map():
        try:
            process.wait(timeout=max(0.001, wait_seconds))
        except subprocess.TimeoutExpired:
            pass
        return
    for key, _ in selector.select(timeout=wait_seconds):
        stream_name, buffer = streams[key.fd]
        chunk = os.read(key.fd, 64 * 1024)
        if not chunk:
            selector.unregister(key.fd)
            continue
        buffer.extend(chunk)
        if len(buffer) > MAX_TOOL_OUTPUT_BYTES:
            terminate_process_group(process)
            raise LocalToolOutputLimit(
                f"Local tool {stream_name} exceeded {MAX_TOOL_OUTPUT_BYTES} bytes."
            )


def terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait(timeout=5)


def terminate_remaining_process_group(group_id: int) -> bool:
    try:
        os.killpg(group_id, signal.SIGTERM)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        try:
            os.killpg(group_id, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return True
        time.sleep(0.02)
    try:
        os.killpg(group_id, signal.SIGKILL)
    except (PermissionError, ProcessLookupError):
        return True
    return True
