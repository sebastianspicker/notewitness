"""Formatting and validation shared by local CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from notewitness.analysis.transcription.runtime import LocalTranscriptionRuntimeError


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
    """Print the stable JSON projection used by local commands."""

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
