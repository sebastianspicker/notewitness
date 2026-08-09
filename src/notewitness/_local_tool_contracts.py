"""Shared contracts for the bounded local-tool execution boundary."""

from __future__ import annotations

from dataclasses import dataclass


MAX_TOOL_ARGUMENTS = 256
MAX_TOOL_ARGUMENT_CHARS = 4_096
MAX_TOOL_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_TOOL_FILE_BYTES = 512 * 1024 * 1024
MAX_TOOL_TIMEOUT_SECONDS = 12 * 60 * 60


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
class LocalToolResult:
    tool_name: str
    return_code: int
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    duration_ms: int
    network_isolated: bool
