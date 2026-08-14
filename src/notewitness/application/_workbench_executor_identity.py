"""Private durable-run and artifact-identity checks for the workbench executor."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
from typing import Any

from ._workbench_executor_config import (
    _TranscriptionProfile,
    WorkbenchRuntimeConfigurationError,
)
from .run_integration import PUBLICATION_FILENAME


def _legacy(name: str) -> Any:
    from . import workbench_local_executor

    return getattr(workbench_local_executor, name)


def _workbench_run_token(job_id: str, step: str, attempt: int) -> str:
    if not _is_workbench_job_id(job_id):
        raise WorkbenchRuntimeConfigurationError("workbench_run_identity_invalid")
    if not _is_workbench_step(step):
        raise WorkbenchRuntimeConfigurationError("workbench_run_identity_invalid")
    if not _is_workbench_attempt_type(attempt):
        raise WorkbenchRuntimeConfigurationError("workbench_run_identity_invalid")
    if not _is_workbench_attempt_in_range(attempt):
        raise WorkbenchRuntimeConfigurationError("workbench_run_identity_invalid")
    material = f"notewitness-workbench-v1\0{job_id}\0{step}\0{attempt}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _is_workbench_job_id(value: object) -> bool:
    return isinstance(value, str) and value.startswith("job:workbench-")


def _is_workbench_step(value: object) -> bool:
    return value in {"transcription", "analysis"}


def _is_workbench_attempt_type(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_workbench_attempt_in_range(value: int) -> bool:
    return 1 <= value <= 100


def _recover_completed_workbench_run(
    project_root: Path,
    *,
    job_id: str,
    step: str,
    attempt: int,
) -> bool:
    for prior_attempt in range(1, attempt + 1):
        token = _legacy("_workbench_run_token")(job_id, step, prior_attempt)
        run_id = f"run:{'analysis-' if step == 'analysis' else ''}{token}"
        directory_name = f"analysis-{token}" if step == "analysis" else token
        publication_path = project_root / "runs" / directory_name / PUBLICATION_FILENAME
        if not publication_path.exists() and not publication_path.is_symlink():
            continue
        _legacy("integrate_completed_run")(project_root, run_id)
        return True
    return False


def _require_checkpoint_identity(profile: _TranscriptionProfile) -> None:
    current_sha256, current_size = _legacy("_stable_file_identity")(
        profile.settings.model_checkpoint
    )
    if (
        current_sha256 != profile.checkpoint_sha256
        or current_size != profile.checkpoint_size_bytes
    ):
        raise WorkbenchRuntimeConfigurationError(
            "configured_transcription_checkpoint_changed"
        )


def _stable_file_identity(path: Path) -> tuple[str, int]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise WorkbenchRuntimeConfigurationError(
            "configured_artifact_unavailable"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
            raise WorkbenchRuntimeConfigurationError("configured_artifact_not_regular")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = lambda item: (
        item.st_dev,
        item.st_ino,
        item.st_size,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )
    if identity(before) != identity(after):
        raise WorkbenchRuntimeConfigurationError("configured_artifact_changed")
    return digest.hexdigest(), before.st_size
