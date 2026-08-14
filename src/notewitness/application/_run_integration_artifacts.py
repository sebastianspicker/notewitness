"""Private-file, path, and directory checks for completed run artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any, Iterable

from notewitness.application._run_integration_support import (
    MAX_PUBLICATION_BYTES,
    PUBLICATION_FILENAME,
    RunIntegrationError,
)
from notewitness.application._run_publication_contract import RUN_ID_PATTERN
from notewitness.local_artifacts import MAX_LOCAL_ARTIFACT_BYTES
from notewitness.project_store import ProjectStore


def completed_artifact_sha256s(
    run_directory: Path, relative_paths: Iterable[str]
) -> dict[str, str]:
    """Identify completed private artifacts before sealing a publication."""

    result: dict[str, str] = {}
    for relative_path in relative_paths:
        normalized = artifact_relative_path(relative_path)
        if normalized in result:
            raise RunIntegrationError("Publication artifact paths must be unique.")
        path = run_directory.joinpath(*PurePosixPath(normalized).parts)
        digest, _ = private_file_identity(path, MAX_LOCAL_ARTIFACT_BYTES)
        result[normalized] = digest
    return result


def artifact_relative_path(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Publication artifact path must be a string.")
    relative = PurePosixPath(value)
    if unsafe_artifact_path(relative, value):
        raise ValueError("Publication artifact path is unsafe.")
    return relative.as_posix()


def unsafe_artifact_path(relative: PurePosixPath, value: str) -> bool:
    return any(
        (
            invalid_artifact_root(relative, value),
            invalid_artifact_parts(relative),
            relative.name == PUBLICATION_FILENAME,
            invalid_nested_artifact(relative),
        )
    )


def invalid_artifact_root(relative: PurePosixPath, value: str) -> bool:
    return relative.is_absolute() or "\\" in value or not relative.parts


def invalid_artifact_parts(relative: PurePosixPath) -> bool:
    return len(relative.parts) > 2 or any(part in {"", ".", ".."} for part in relative.parts)


def invalid_nested_artifact(relative: PurePosixPath) -> bool:
    return len(relative.parts) == 2 and relative.parts[0] != "raw"


def run_directory(store: ProjectStore, run_id: str) -> Path:
    match = RUN_ID_PATTERN.fullmatch(run_id)
    if match is None:
        raise RunIntegrationError("Run ID must identify a completed local run.")
    token = match.group(2)
    name = f"analysis-{token}" if match.group(1) == "analysis" else token
    runs = store.root / "runs"
    require_private_directory(runs)
    directory = runs / name
    require_private_directory(directory)
    return directory


def require_private_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RunIntegrationError("Completed run directory is unavailable.") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise RunIntegrationError("Completed run directory is not owner-private.")


def read_json_artifact(path: Path, maximum: int, label: str) -> dict[str, Any]:
    raw = read_private_file(path, maximum)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunIntegrationError(f"{label} contains invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise RunIntegrationError(f"{label} must be a JSON object.")
    return payload


def read_private_file(path: Path, maximum: int) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        metadata = os.fstat(descriptor)
        if not bounded_private_file(metadata, maximum):
            raise RunIntegrationError("Completed run artifact is not a bounded private file.")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if remaining:
            raise RunIntegrationError("Completed run artifact changed while reading.")
        return b"".join(chunks)
    except OSError as exc:
        raise RunIntegrationError("Completed run artifact is unavailable.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def private_file_identity(path: Path, maximum: int) -> tuple[str, int]:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        before = os.fstat(descriptor)
        if not bounded_private_file(before, maximum):
            raise RunIntegrationError("Completed run artifact is not a bounded private file.")
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise RunIntegrationError("Completed run artifact is unavailable.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if stat_identity(before) != stat_identity(after):
        raise RunIntegrationError("Completed run artifact changed while reading.")
    return digest.hexdigest(), size


def bounded_private_file(metadata: os.stat_result, maximum: int) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and not stat.S_IMODE(metadata.st_mode) & 0o077
        and metadata.st_size <= maximum
    )


def stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def completed_manifest(run_directory: Path, run_id: str) -> dict[str, Any]:
    manifest = read_json_artifact(
        run_directory / "manifest.completed.json",
        MAX_PUBLICATION_BYTES,
        "Completed run manifest",
    )
    if manifest.get("state") != "completed":
        raise RunIntegrationError("Run manifest is not completed.")
    if manifest.get("run_id") != run_id:
        raise RunIntegrationError("Completed manifest run identity changed.")
    return manifest
