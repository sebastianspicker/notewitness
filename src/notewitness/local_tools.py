"""Bounded execution of explicitly discovered local media/model tools."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import platform
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
from typing import Callable, Mapping, Sequence


MAX_TOOL_ARGUMENTS = 256
MAX_TOOL_ARGUMENT_CHARS = 4_096
MAX_TOOL_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_TOOL_FILE_BYTES = 512 * 1024 * 1024
MAX_TOOL_TIMEOUT_SECONDS = 12 * 60 * 60
_NETWORK_DENY_PROFILE = "(version 1) (allow default) (deny network*)"
_SAFE_ENVIRONMENT_KEYS = (
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
)


class LocalToolError(RuntimeError):
    """A local tool could not be safely discovered or executed."""


class LocalToolUnavailable(LocalToolError):
    pass


class LocalToolTimeout(LocalToolError):
    pass


class LocalToolCancelled(LocalToolError):
    """Execution stopped because its caller requested cancellation."""


class LocalToolOutputLimit(LocalToolError):
    pass


class LocalToolProcessLeak(LocalToolError):
    pass


class LocalToolIdentityChanged(LocalToolError):
    """The executable no longer matches the bytes approved at discovery."""


class LocalToolFailure(LocalToolError):
    def __init__(
        self,
        tool_name: str,
        return_code: int,
        *,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.tool_name = tool_name
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(
            f"Local tool {tool_name!r} failed with exit status {return_code}."
        )


class NetworkIsolationUnavailable(LocalToolError):
    pass


@dataclass(frozen=True, slots=True)
class LocalExecutableIdentity:
    """Byte and filesystem identity captured from one open executable."""

    sha256: str
    size_bytes: int
    device: int
    inode: int
    owner_uid: int
    mode: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True, slots=True)
class LocalTool:
    name: str
    executable: Path
    identity: LocalExecutableIdentity = field(init=False)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Local tools require a non-empty name.")
        resolved = _validated_executable(self.executable)
        identity = _executable_identity(resolved)
        object.__setattr__(self, "executable", resolved)
        object.__setattr__(self, "identity", identity)

    def require_unchanged(self) -> None:
        """Reject replacement or mutation since this tool was discovered."""

        try:
            current = _executable_identity(self.executable)
        except LocalToolUnavailable as exc:
            raise LocalToolIdentityChanged(
                f"Local tool {self.name!r} changed after discovery."
            ) from exc
        if current != self.identity:
            raise LocalToolIdentityChanged(
                f"Local tool {self.name!r} changed after discovery."
            )


@dataclass(frozen=True, slots=True)
class LocalToolResult:
    tool_name: str
    return_code: int
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    duration_ms: int
    network_isolated: bool


def discover_local_tool(name: str, explicit_path: str | Path | None = None) -> LocalTool:
    """Resolve one executable without running it or mutating the machine."""

    if not name or any(character in name for character in ("/", "\\", "\x00")):
        raise ValueError("Tool names must be plain executable names.")
    candidate: str | None
    if explicit_path is None:
        candidate = shutil.which(name)
    else:
        raw = Path(explicit_path)
        if not raw.is_absolute():
            raise LocalToolUnavailable("Explicit tool paths must be absolute.")
        candidate = os.fspath(raw)
    if candidate is None:
        raise LocalToolUnavailable(f"Required local tool {name!r} was not found.")
    return LocalTool(name=name, executable=Path(candidate))


class BoundedLocalToolRunner:
    """Execute one fixed binary with finite resources and no shell expansion."""

    def __init__(self, tool: LocalTool) -> None:
        self.tool = tool

    def run(
        self,
        arguments: Sequence[str],
        *,
        working_directory: str | Path,
        timeout_seconds: int,
        deny_network: bool = True,
        environment: Mapping[str, str] | None = None,
        executable_search_paths: Sequence[str | Path] = (),
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> LocalToolResult:
        args = _validated_arguments(arguments)
        timeout = _validated_timeout(timeout_seconds)
        workdir = _validated_private_directory(Path(working_directory))
        search_paths = _validated_search_paths(executable_search_paths)
        child_environment = _bounded_environment(
            environment,
            workdir=workdir,
            tool_directory=self.tool.executable.parent,
            search_paths=search_paths,
        )
        command = (os.fspath(self.tool.executable), *args)
        network_isolated = False
        if deny_network:
            command = _network_isolated_command(command)
            network_isolated = True
        launcher_command = _resource_limited_launcher_command(command, timeout)
        if cancellation_requested is not None and not callable(cancellation_requested):
            raise ValueError("cancellation_requested must be callable or None.")
        if cancellation_requested is not None and cancellation_requested():
            raise LocalToolCancelled(
                f"Local tool {self.tool.name!r} was cancelled before execution."
            )

        # The executable is startup-approved, rather than merely name-resolved.
        # Check immediately before spawn and again on every path after a child
        # could have run.  The latter makes a replacement a hard failure even
        # when the child otherwise exits successfully, fails, or is cancelled.
        self.tool.require_unchanged()
        started = time.monotonic()
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                launcher_command,
                cwd=workdir,
                env=child_environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
                close_fds=True,
            )
            try:
                stdout_raw, stderr_raw = _communicate_bounded(
                    process,
                    timeout_seconds=timeout,
                    started=started,
                    cancellation_requested=cancellation_requested,
                )
                return_code = process.wait(timeout=1)
            except BaseException:
                _terminate_remaining_process_group(process.pid)
                raise
            if _terminate_remaining_process_group(process.pid):
                raise LocalToolProcessLeak(
                    f"Local tool {self.tool.name!r} left background processes running."
                )

            stdout = stdout_raw.decode("utf-8", errors="replace")
            stderr = stderr_raw.decode("utf-8", errors="replace")

            duration_ms = max(0, round((time.monotonic() - started) * 1_000))
            if return_code != 0:
                raise LocalToolFailure(
                    self.tool.name,
                    return_code,
                    stdout=stdout,
                    stderr=stderr,
                )
            result = LocalToolResult(
                tool_name=self.tool.name,
                return_code=return_code,
                stdout=stdout,
                stderr=stderr,
                stdout_truncated=False,
                stderr_truncated=False,
                duration_ms=duration_ms,
                network_isolated=network_isolated,
            )
        finally:
            if process is not None:
                self.tool.require_unchanged()

        return result


def _validated_executable(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise LocalToolUnavailable("Local tool executable is unavailable.") from exc
    if not stat.S_ISREG(metadata.st_mode) or not os.access(resolved, os.X_OK):
        raise LocalToolUnavailable("Local tool path is not an executable regular file.")
    return resolved


def _executable_identity(path: Path) -> LocalExecutableIdentity:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise LocalToolUnavailable("Local tool executable is unavailable.") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise LocalToolUnavailable(
                "Local tool path is not an executable regular file."
            )
        if stat.S_IMODE(before.st_mode) & 0o022:
            raise LocalToolUnavailable(
                "Local tool executable must not be group or world writable."
            )
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise LocalToolUnavailable("Local tool executable is unavailable.") from exc
    finally:
        os.close(descriptor)
    before_identity = _stat_identity(before)
    if before_identity != _stat_identity(after):
        raise LocalToolUnavailable(
            "Local tool executable changed while its identity was captured."
        )
    return LocalExecutableIdentity(
        sha256=digest.hexdigest(),
        size_bytes=before.st_size,
        device=before.st_dev,
        inode=before.st_ino,
        owner_uid=before.st_uid,
        mode=stat.S_IMODE(before.st_mode),
        modified_ns=before.st_mtime_ns,
        changed_ns=before.st_ctime_ns,
    )


def _stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
        stat.S_IMODE(metadata.st_mode),
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _validated_arguments(arguments: Sequence[str]) -> tuple[str, ...]:
    if isinstance(arguments, (str, bytes)):
        raise ValueError("Local tool arguments must be a sequence of strings.")
    values = tuple(arguments)
    if len(values) > MAX_TOOL_ARGUMENTS:
        raise ValueError(f"Local tools accept at most {MAX_TOOL_ARGUMENTS} arguments.")
    if any(
        not isinstance(value, str)
        or "\x00" in value
        or len(value) > MAX_TOOL_ARGUMENT_CHARS
        for value in values
    ):
        raise ValueError("Local tool arguments must be bounded NUL-free strings.")
    return values


def _validated_timeout(value: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= MAX_TOOL_TIMEOUT_SECONDS
    ):
        raise ValueError(
            f"Tool timeout must be an integer in [1, {MAX_TOOL_TIMEOUT_SECONDS}]."
        )
    return value


def _validated_private_directory(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise LocalToolError("Tool working directory is unavailable.") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise LocalToolError(
            "Tool working directory must deny group and other access."
        )
    return resolved


def _bounded_environment(
    overrides: Mapping[str, str] | None,
    *,
    workdir: Path,
    tool_directory: Path,
    search_paths: tuple[Path, ...],
) -> dict[str, str]:
    environment = {
        key: value
        for key in _SAFE_ENVIRONMENT_KEYS
        if isinstance((value := os.environ.get(key)), str)
    }
    path_entries = _unique_paths(
        (*search_paths, tool_directory, Path("/usr/bin"), Path("/bin"))
    )
    environment["PATH"] = os.pathsep.join(os.fspath(path) for path in path_entries)
    environment["TMPDIR"] = os.fspath(workdir)
    environment["PYTHONNOUSERSITE"] = "1"
    if overrides is not None:
        for key, value in overrides.items():
            if key not in {"OMP_NUM_THREADS", "MKL_NUM_THREADS"}:
                raise ValueError(f"Environment override {key!r} is not allowed.")
            if not isinstance(value, str) or not value.isdecimal():
                raise ValueError("Thread environment overrides must be decimal strings.")
            environment[key] = value
    return environment


def _validated_search_paths(paths: Sequence[str | Path]) -> tuple[Path, ...]:
    if isinstance(paths, (str, bytes)):
        raise ValueError("Executable search paths must be a sequence of paths.")
    if len(paths) > 16:
        raise ValueError("At most 16 executable search paths may be supplied.")
    validated: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if not path.is_absolute() or path.is_symlink():
            raise ValueError("Executable search paths must be absolute directories.")
        try:
            resolved = path.resolve(strict=True)
            metadata = resolved.stat()
        except OSError as exc:
            raise ValueError("Executable search path is unavailable.") from exc
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise ValueError("Executable search paths must not be group/world writable.")
        validated.append(resolved)
    return tuple(validated)


def _unique_paths(paths: Sequence[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    for path in paths:
        if path not in result:
            result.append(path)
    return tuple(result)


def _network_isolated_command(command: tuple[str, ...]) -> tuple[str, ...]:
    if platform.system() != "Darwin":
        raise NetworkIsolationUnavailable(
            "Hard local-tool network isolation is implemented only for macOS."
        )
    sandbox = Path("/usr/bin/sandbox-exec")
    if not sandbox.is_file() or not os.access(sandbox, os.X_OK):
        raise NetworkIsolationUnavailable("macOS sandbox-exec is unavailable.")
    return (os.fspath(sandbox), "-p", _NETWORK_DENY_PROFILE, *command)


def _resource_limited_launcher_command(
    command: tuple[str, ...], timeout_seconds: int
) -> tuple[str, ...]:
    """Run the fixed package helper before execing the approved command.

    Python's subprocess documentation warns that child setup callbacks can
    deadlock when another thread is active.  The helper is a fixed package
    file, not an operator-supplied executable: it applies the resource limits
    in its own process and immediately execs this exact command.
    """

    try:
        launcher = Path(__file__).with_name("_local_tool_launcher.py").resolve(
            strict=True
        )
        metadata = launcher.stat()
    except OSError as exc:
        raise LocalToolError("Local tool launcher is unavailable.") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o022:
        raise LocalToolError("Local tool launcher is unavailable.")

    try:
        interpreter = Path(sys.executable).resolve(strict=True)
        interpreter_metadata = interpreter.stat()
    except OSError as exc:
        raise LocalToolError("Local Python launcher is unavailable.") from exc
    if (
        not stat.S_ISREG(interpreter_metadata.st_mode)
        or not os.access(interpreter, os.X_OK)
        or stat.S_IMODE(interpreter_metadata.st_mode) & 0o022
    ):
        raise LocalToolError("Local Python launcher is unavailable.")

    cpu_seconds = min(MAX_TOOL_TIMEOUT_SECONDS, max(2, timeout_seconds * 2))
    return (
        os.fspath(interpreter),
        os.fspath(launcher),
        "--cpu-seconds",
        str(cpu_seconds),
        "--",
        *command,
    )


def _communicate_bounded(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: int,
    started: float,
    cancellation_requested: Callable[[], bool] | None,
) -> tuple[bytes, bytes]:
    if process.stdout is None or process.stderr is None:
        _terminate_process_group(process)
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
            now = time.monotonic()
            if (
                cancellation_requested is not None
                and now >= next_cancellation_probe
            ):
                if cancellation_requested():
                    _terminate_process_group(process)
                    raise LocalToolCancelled("Local tool execution was cancelled.")
                next_cancellation_probe = now + 0.25
            remaining = deadline - now
            if remaining <= 0:
                _terminate_process_group(process)
                raise LocalToolTimeout(
                    f"Local tool exceeded {timeout_seconds} seconds."
                )
            wait_seconds = min(0.25, remaining)
            if cancellation_requested is not None:
                wait_seconds = min(
                    wait_seconds,
                    max(0.0, next_cancellation_probe - now),
                )
            if selector.get_map():
                for key, _ in selector.select(timeout=wait_seconds):
                    stream_name, buffer = streams[key.fd]
                    chunk = os.read(key.fd, 64 * 1024)
                    if not chunk:
                        selector.unregister(key.fd)
                        continue
                    buffer.extend(chunk)
                    if len(buffer) > MAX_TOOL_OUTPUT_BYTES:
                        _terminate_process_group(process)
                        raise LocalToolOutputLimit(
                            f"Local tool {stream_name} exceeded "
                            f"{MAX_TOOL_OUTPUT_BYTES} bytes."
                        )
            else:
                try:
                    process.wait(timeout=max(0.001, wait_seconds))
                except subprocess.TimeoutExpired:
                    pass
        if cancellation_requested is not None and cancellation_requested():
            _terminate_process_group(process)
            raise LocalToolCancelled("Local tool execution was cancelled.")
        remaining = max(0.001, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            _terminate_process_group(process)
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


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
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


def _terminate_remaining_process_group(group_id: int) -> bool:
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
