"""Owner-private input and artifact identity checks for analysis CLIs."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
from typing import Callable


MAX_ARTIFACT_TREE_ENTRIES = 200_000
MAX_ARTIFACT_TREE_BYTES = 128 * 1024 * 1024 * 1024


def private_directory(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        mode = stat.S_IMODE(resolved.stat().st_mode)
    except OSError as exc:
        raise ValueError("working_directory must exist.") from exc
    if not resolved.is_dir() or mode & 0o077:
        raise ValueError("working_directory must deny group and other access.")
    return resolved


def private_analysis_input(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("Analysis input paths must be absolute non-symlinks.")
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise ValueError("Analysis input path is unavailable.") from exc
    if (
        not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode))
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise ValueError(
            "Analysis inputs must be owner-private regular files or directories."
        )
    return resolved


def analysis_artifact_identity(
    path: str | Path,
    *,
    private_input: Callable[[Path], Path] = private_analysis_input,
    regular_file_identity: Callable[[Path], tuple[str, int]] | None = None,
) -> tuple[str, int]:
    """Hash one owner-private file or an explicit symlink-free model tree."""

    file_identity = regular_file_identity or _regular_file_identity
    resolved = private_input(Path(path))
    if resolved.is_file():
        return file_identity(resolved)
    digest = hashlib.sha256(b"notewitness-model-tree-v1\0")
    total_size = 0
    entry_count = 0
    pending = [resolved]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise ValueError("Analysis artifact directory could not be read.") from exc
        directories: list[Path] = []
        for entry in entries:
            entry_count += 1
            if entry_count > MAX_ARTIFACT_TREE_ENTRIES:
                raise ValueError("Analysis artifact directory has too many entries.")
            try:
                metadata = entry.lstat()
            except OSError as exc:
                raise ValueError("Analysis artifact entry became unavailable.") from exc
            if (
                stat.S_ISLNK(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise ValueError(
                    "Analysis artifact trees must be owner-private and symlink-free."
                )
            relative = entry.relative_to(resolved).as_posix().encode(
                "utf-8", errors="surrogateescape"
            )
            if stat.S_ISDIR(metadata.st_mode):
                digest.update(b"D\0" + relative + b"\0")
                directories.append(entry)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(
                    "Analysis artifact trees may contain only files and directories."
                )
            file_digest, file_size = file_identity(entry)
            total_size += file_size
            if total_size > MAX_ARTIFACT_TREE_BYTES:
                raise ValueError("Analysis artifact directory exceeds the byte bound.")
            digest.update(
                b"F\0"
                + relative
                + b"\0"
                + str(file_size).encode("ascii")
                + b"\0"
                + file_digest.encode("ascii")
                + b"\0"
            )
        pending.extend(reversed(directories))
    if entry_count == 0 or total_size <= 0:
        raise ValueError("Analysis artifact directory must contain model bytes.")
    return digest.hexdigest(), total_size


def _regular_file_identity(path: Path) -> tuple[str, int]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("Analysis artifact file could not be opened safely.") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) & 0o077
        ):
            raise ValueError("Analysis artifact files must be owner-private.")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = lambda item: (
        item.st_dev,
        item.st_ino,
        item.st_mode,
        item.st_size,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )
    if identity(before) != identity(after):
        raise ValueError("Analysis artifact file changed while it was read.")
    return digest.hexdigest(), before.st_size
