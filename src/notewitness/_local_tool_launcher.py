"""Fixed child-side resource limiter for :mod:`notewitness.local_tools`.

This module is intentionally not a public command-line interface.  The parent
constructs its arguments from a previously approved local-tool command, then
this process sets finite resource limits and replaces itself with that command.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import resource
import stat
import sys


MAX_TOOL_FILE_BYTES = 512 * 1024 * 1024
MAX_TOOL_TIMEOUT_SECONDS = 12 * 60 * 60
_FAILURE_MESSAGE = b"notewitness local-tool launcher failed.\n"
_IDENTITY_ENVIRONMENT_KEY = "NOTEWITNESS_LOCAL_TOOL_IDENTITY"
_NETWORK_SANDBOX = "/usr/bin/sandbox-exec"
_NETWORK_DENY_PROFILE = "(version 1) (allow default) (deny network*)"


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if len(values) < 4 or values[:1] != ["--cpu-seconds"] or values[2:3] != ["--"]:
        return _fail()
    try:
        cpu_seconds = int(values[1])
    except ValueError:
        return _fail()
    command = values[3:]
    if (
        not 2 <= cpu_seconds <= MAX_TOOL_TIMEOUT_SECONDS
        or not command
        or not os.path.isabs(command[0])
        or any("\x00" in value for value in command)
    ):
        return _fail()
    try:
        if not _command_matches_approved_tool(command):
            return _fail()
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(
            resource.RLIMIT_FSIZE,
            (MAX_TOOL_FILE_BYTES, MAX_TOOL_FILE_BYTES),
        )
        resource.setrlimit(
            resource.RLIMIT_CPU,
            (cpu_seconds, min(MAX_TOOL_TIMEOUT_SECONDS, cpu_seconds + 1)),
        )
        os.execve(command[0], command, os.environ)
    except (OSError, ValueError):
        return _fail()
    return _fail()


def _command_matches_approved_tool(command: list[str]) -> bool:
    """Revalidate the discovered tool as late as this fixed launcher allows."""

    try:
        expected = json.loads(os.environ[_IDENTITY_ENVIRONMENT_KEY])
        if not isinstance(expected, dict):
            return False
        path = expected["path"]
        if not isinstance(path, str) or not os.path.isabs(path):
            return False
        if command[0] == path:
            pass
        elif (
            command[:3] == [_NETWORK_SANDBOX, "-p", _NETWORK_DENY_PROFILE]
            and len(command) >= 4
            and command[3] == path
        ):
            pass
        else:
            return False
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except (KeyError, OSError, TypeError, json.JSONDecodeError):
        return False
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) & 0o022:
            return False
        if not _trusted_canonical_file(Path(path)):
            return False
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
    except OSError:
        return False
    finally:
        os.close(descriptor)
    return (
        _stat_identity(before) == _stat_identity(after)
        and digest.hexdigest() == expected.get("sha256")
        and before.st_size == expected.get("size_bytes")
        and before.st_dev == expected.get("device")
        and before.st_ino == expected.get("inode")
        and before.st_uid == expected.get("owner_uid")
        and stat.S_IMODE(before.st_mode) == expected.get("mode")
        and before.st_mtime_ns == expected.get("modified_ns")
        and before.st_ctime_ns == expected.get("changed_ns")
    )


def _trusted_canonical_file(path: Path) -> bool:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return False
    if resolved != path:
        return False
    current_uid = os.getuid()
    effective_uid = os.geteuid()
    allowed_owners = (
        {effective_uid} if effective_uid != current_uid else {current_uid, 0}
    )
    components = (
        Path(resolved.anchor),
        *(
            Path(resolved.anchor, *resolved.parts[1:index])
            for index in range(2, len(resolved.parts) + 1)
        ),
    )
    for index, component in enumerate(components):
        try:
            metadata = component.lstat()
        except OSError:
            return False
        if stat.S_ISLNK(metadata.st_mode) or metadata.st_uid not in allowed_owners:
            return False
        if stat.S_IMODE(metadata.st_mode) & 0o022:
            return False
        if index < len(components) - 1 and not stat.S_ISDIR(metadata.st_mode):
            return False
    return True


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


def _fail() -> int:
    try:
        os.write(2, _FAILURE_MESSAGE)
    except OSError:
        pass
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
