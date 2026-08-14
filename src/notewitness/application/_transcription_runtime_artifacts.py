"""Private filesystem helpers for local transcription runs."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any

from notewitness.project_store import ProjectSnapshot, ProjectStore


def source_media(
    store: ProjectStore,
    snapshot: ProjectSnapshot,
    source_id: str,
    *,
    error_type: type[Exception],
) -> tuple[dict[str, Any], Path]:
    sources = snapshot.payload.get("sources")
    if not isinstance(sources, list):
        raise error_type("Project sources collection is malformed.")
    matches = [
        item
        for item in sources
        if isinstance(item, dict) and item.get("id") == source_id
    ]
    if len(matches) != 1:
        raise error_type("Transcription source was not found uniquely.")
    source = matches[0]
    uri = source.get("uri")
    if not isinstance(uri, str):
        raise error_type("Transcription source URI is invalid.")
    relative = PurePosixPath(uri)
    if (
        relative.is_absolute()
        or "\\" in uri
        or len(relative.parts) != 2
        or relative.parts[0] != "media"
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise error_type("Transcription accepts only ingested project media.")
    media_path = store.root.joinpath(*relative.parts)
    if media_path.is_symlink():
        raise error_type("Transcription media must not be a symlink.")
    return source, media_path


def require_source_checksum(
    path: Path,
    expected_sha256: str,
    *,
    error_type: type[Exception],
) -> None:
    digest = hashlib.sha256()
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise error_type("Ingested media is not a regular file.")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
    except OSError as exc:
        raise error_type("Ingested media is unavailable.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if digest.hexdigest() != expected_sha256:
        raise error_type("Ingested media checksum no longer matches.")


def file_identity(
    path: Path,
    *,
    error_type: type[Exception],
) -> tuple[str, int]:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise error_type("Runtime artifact is not a regular file.")
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise error_type("Runtime artifact could not be identified safely.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if _stat_identity(before) != _stat_identity(after):
        raise error_type("Runtime artifact changed during identity verification.")
    return digest.hexdigest(), size


def valid_run_token(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 32
        and all(character in "0123456789abcdef" for character in value)
    )


def create_run_directory(
    store: ProjectStore,
    run_token: str,
    *,
    error_type: type[Exception],
) -> Path:
    return create_private_directory(
        store.ensure_private_directory("runs"), run_token, error_type=error_type
    )


def create_private_directory(
    parent: Path,
    name: str,
    *,
    error_type: type[Exception],
) -> Path:
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789-"
    if not name or any(character not in allowed for character in name):
        raise error_type("Private run directory name is invalid.")
    path = parent / name
    try:
        os.mkdir(path, 0o700)
        os.chmod(path, 0o700, follow_symlinks=False)
        metadata = path.lstat()
    except OSError as exc:
        raise error_type("Could not create private run storage.") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise error_type("Run storage is not an owner-private directory.")
    return path


def _stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )
