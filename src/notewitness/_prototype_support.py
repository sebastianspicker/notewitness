"""Shared implementation details for the prototype command façade."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from notewitness.application.transcription_runtime import LocalTranscriptionRuntimeError
from notewitness.local_tools import LocalTool


def project_root(value: str) -> Path:
    """Resolve either a project directory or its project document."""

    path = Path(value)
    return path.parent if path.name == "project.json" else path


def project_relative(project_root: Path, path: Path) -> str:
    """Return a project-relative artifact path without allowing an escape."""

    try:
        return path.relative_to(project_root).as_posix()
    except ValueError as exc:
        raise LocalTranscriptionRuntimeError(
            "Runtime artifact escaped the project root."
        ) from exc


def print_json(payload: dict[str, Any]) -> None:
    """Print the stable JSON projection used by prototype commands."""

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def path_identity(path: Path) -> tuple[str, int]:
    """Hash a non-empty local file for simple source-checksum comparisons."""

    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as input_file:
            for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise ValueError("Configured local artifact could not be read.") from exc
    if size <= 0:
        raise ValueError("Configured local artifact must not be empty.")
    return digest.hexdigest(), size


def tool_identity_payload(tool: LocalTool) -> dict[str, int | str]:
    """Keep durable-job fingerprints tied to the discovered executable identity."""

    identity = tool.identity
    return {
        "changed_ns": identity.changed_ns,
        "device": identity.device,
        "inode": identity.inode,
        "mode": identity.mode,
        "modified_ns": identity.modified_ns,
        "owner_uid": identity.owner_uid,
        "sha256": identity.sha256,
        "size_bytes": identity.size_bytes,
    }
