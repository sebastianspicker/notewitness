"""Fixed child-side resource limiter for :mod:`notewitness.local_tools`.

This module is intentionally not a public command-line interface.  The parent
constructs its arguments from a previously approved local-tool command, then
this process sets finite resource limits and replaces itself with that command.
"""

from __future__ import annotations

import os
import resource
import sys


MAX_TOOL_FILE_BYTES = 512 * 1024 * 1024
MAX_TOOL_TIMEOUT_SECONDS = 12 * 60 * 60
_FAILURE_MESSAGE = b"notewitness local-tool launcher failed.\n"


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


def _fail() -> int:
    try:
        os.write(2, _FAILURE_MESSAGE)
    except OSError:
        pass
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
