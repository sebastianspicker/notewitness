"""Input, environment, network, and launcher policy for local tools."""

from __future__ import annotations

import os
from pathlib import Path
import platform
import stat
import sys
from typing import Mapping, Sequence

from notewitness._local_tool_contracts import (
    MAX_TOOL_ARGUMENT_CHARS,
    MAX_TOOL_ARGUMENTS,
    MAX_TOOL_TIMEOUT_SECONDS,
    LocalToolError,
    NetworkIsolationUnavailable,
)


_NETWORK_DENY_PROFILE = "(version 1) (allow default) (deny network*)"
_SAFE_ENVIRONMENT_KEYS = ("LANG", "LC_ALL", "LC_CTYPE")


def validated_arguments(arguments: Sequence[str]) -> tuple[str, ...]:
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


def validated_timeout(value: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= MAX_TOOL_TIMEOUT_SECONDS
    ):
        raise ValueError(
            f"Tool timeout must be an integer in [1, {MAX_TOOL_TIMEOUT_SECONDS}]."
        )
    return value


def validated_private_directory(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise LocalToolError("Tool working directory is unavailable.") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise LocalToolError("Tool working directory must deny group and other access.")
    return resolved


def validated_search_paths(paths: Sequence[str | Path]) -> tuple[Path, ...]:
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


def bounded_environment(
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
    path_entries = unique_paths(
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


def network_isolated_command(command: tuple[str, ...]) -> tuple[str, ...]:
    if platform.system() != "Darwin":
        raise NetworkIsolationUnavailable(
            "Hard local-tool network isolation is implemented only for macOS."
        )
    sandbox = Path("/usr/bin/sandbox-exec")
    if not sandbox.is_file() or not os.access(sandbox, os.X_OK):
        raise NetworkIsolationUnavailable("macOS sandbox-exec is unavailable.")
    return (os.fspath(sandbox), "-p", _NETWORK_DENY_PROFILE, *command)


def resource_limited_launcher_command(
    command: tuple[str, ...], timeout_seconds: int
) -> tuple[str, ...]:
    """Run the fixed package helper before execing the approved command."""

    try:
        launcher = Path(__file__).with_name("_local_tool_launcher.py").resolve(
            strict=True
        )
        metadata = launcher.stat()
    except OSError as exc:
        raise LocalToolError("Local tool launcher is unavailable.") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o022:
        raise LocalToolError("Local tool launcher is unavailable.")
    interpreter = trusted_python_launcher()
    cpu_seconds = min(MAX_TOOL_TIMEOUT_SECONDS, max(2, timeout_seconds * 2))
    return (
        os.fspath(interpreter),
        os.fspath(launcher),
        "--cpu-seconds",
        str(cpu_seconds),
        "--",
        *command,
    )


def trusted_python_launcher() -> Path:
    """Select an immutable interpreter for the fixed resource-limit helper."""

    for candidate in dict.fromkeys((Path(sys.executable), Path("/usr/bin/python3"))):
        try:
            interpreter = candidate.resolve(strict=True)
            metadata = interpreter.stat()
        except OSError:
            continue
        if (
            stat.S_ISREG(metadata.st_mode)
            and os.access(interpreter, os.X_OK)
            and not stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            return interpreter
    raise LocalToolError("Local Python launcher is unavailable.")


def unique_paths(paths: Sequence[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    for path in paths:
        if path not in result:
            result.append(path)
    return tuple(result)
