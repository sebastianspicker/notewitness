"""Exclusive owner-only publication of derived local JSON artifacts."""

from __future__ import annotations

from collections.abc import Mapping
import errno
import json
import os
from pathlib import Path
import stat
from typing import Any

from notewitness.projects.private_fs import (
    open_directory_no_follow,
    trusted_absolute_path,
    write_new_private_file,
)


MAX_LOCAL_JSON_BYTES = 256 * 1024 * 1024
MAX_LOCAL_ARTIFACT_BYTES = 512 * 1024 * 1024
MAX_TRANSCRIPT_BYTES = 10_000_000


class LocalArtifactError(RuntimeError):
    pass


def publish_new_private_text(path: str | Path, contents: str) -> Path:
    """Exclusively create an owner-private UTF-8 transcript without symlinks."""

    return write_new_private_bytes(
        path, contents.encode("utf-8"), maximum_bytes=MAX_TRANSCRIPT_BYTES
    )


def write_new_private_json(path: str | Path, payload: Mapping[str, Any]) -> Path:
    """Atomically create private JSON without following directory symlinks."""

    try:
        serialized = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LocalArtifactError("JSON artifact is not serializable.") from exc
    if len(serialized) > MAX_LOCAL_JSON_BYTES:
        raise LocalArtifactError(
            f"JSON artifact exceeds {MAX_LOCAL_JSON_BYTES} bytes."
        )
    return write_new_private_bytes(
        path,
        serialized,
        maximum_bytes=MAX_LOCAL_JSON_BYTES,
    )


def write_new_private_bytes(
    path: str | Path,
    contents: bytes,
    *,
    maximum_bytes: int = MAX_LOCAL_ARTIFACT_BYTES,
) -> Path:
    """Atomically create an exact owner-private artifact without replacement."""

    if not isinstance(contents, bytes):
        raise TypeError("Artifact contents must be exact bytes.")
    if (
        not isinstance(maximum_bytes, int)
        or isinstance(maximum_bytes, bool)
        or not 1 <= maximum_bytes <= MAX_LOCAL_ARTIFACT_BYTES
    ):
        raise ValueError("maximum_bytes exceeds the local artifact bound.")
    if len(contents) > maximum_bytes:
        raise LocalArtifactError(f"Artifact exceeds {maximum_bytes} bytes.")
    target = trusted_absolute_path(Path(path))
    if target == Path(os.path.sep) or target.name in {"", ".", ".."}:
        raise LocalArtifactError(f"Invalid artifact path: {path}")
    try:
        parent_descriptor = open_directory_no_follow(target.parent)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
            raise LocalArtifactError(
                f"Artifact parent must be an existing, non-symlink directory: {target.parent}"
            ) from exc
        raise
    try:
        parent_mode = stat.S_IMODE(os.fstat(parent_descriptor).st_mode)
        if parent_mode & 0o077:
            raise LocalArtifactError(
                "Artifact parent must deny group and other access "
                f"(current mode: {parent_mode:04o})."
            )
        _write_new_private_file(parent_descriptor, target.name, contents)
    finally:
        os.close(parent_descriptor)
    return target


def _write_new_private_file(
    directory_descriptor: int, name: str, contents: bytes
) -> None:
    try:
        write_new_private_file(directory_descriptor, name, contents)
    except FileExistsError as exc:
        raise LocalArtifactError(
            f"Refusing to replace existing artifact path: {name}"
        ) from exc
