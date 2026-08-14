"""Approved executable discovery and immutable identity checks."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import shutil
import stat

from notewitness._local_tool_contracts import (
    LocalExecutableIdentity,
    LocalToolIdentityChanged,
    LocalToolUnavailable,
)


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


def discover_local_tool(name: str, explicit_path: str | Path | None = None) -> LocalTool:
    """Resolve one executable without running it or mutating the machine."""

    if not name or any(character in name for character in ("/", "\\", "\x00")):
        raise ValueError("Tool names must be plain executable names.")
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
    if _stat_identity(before) != _stat_identity(after):
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
