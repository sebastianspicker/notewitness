"""Exclusive owner-only publication of derived local JSON artifacts."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import stat
from typing import Any, Mapping
from uuid import uuid4


_FILE_MODE = 0o600
MAX_LOCAL_JSON_BYTES = 256 * 1024 * 1024
MAX_LOCAL_ARTIFACT_BYTES = 512 * 1024 * 1024


class LocalArtifactError(RuntimeError):
    pass


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
    target = _trusted_absolute_path(Path(path))
    if target == Path(os.path.sep) or target.name in {"", ".", ".."}:
        raise LocalArtifactError(f"Invalid artifact path: {path}")
    try:
        parent_descriptor = _open_existing_directory(target.parent)
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


def _trusted_absolute_path(target: Path) -> Path:
    absolute_target = Path(os.path.abspath(os.fspath(target)))
    var_alias = Path("/var")
    private_var = Path("/private/var")
    if absolute_target == var_alias or var_alias in absolute_target.parents:
        if var_alias.is_symlink() and Path(os.path.realpath(var_alias)) == private_var:
            return private_var / absolute_target.relative_to(var_alias)
    return absolute_target


def _open_existing_directory(directory: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(os.path.sep, flags)
    try:
        for component in directory.parts[1:]:
            child_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _write_new_private_file(
    directory_descriptor: int, name: str, contents: bytes
) -> None:
    temporary_name = f".{name}.{uuid4().hex}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            _FILE_MODE,
            dir_fd=directory_descriptor,
        )
        os.fchmod(descriptor, _FILE_MODE)
        with os.fdopen(descriptor, "wb") as temporary_file:
            descriptor = None
            temporary_file.write(contents)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.link(
            temporary_name,
            name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        os.unlink(temporary_name, dir_fd=directory_descriptor)
        os.fsync(directory_descriptor)
    except FileExistsError as exc:
        raise LocalArtifactError(
            f"Refusing to replace existing artifact path: {name}"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory_descriptor)
        except FileNotFoundError:
            pass
