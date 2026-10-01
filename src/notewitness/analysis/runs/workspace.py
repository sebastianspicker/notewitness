"""Private run-directory lifecycle shared by every local runtime.

Both the transcription and the one-shot analysis-suite runtimes create a
``runs/<name>/`` directory, resolve their project media source, identify files,
and write status records through this module. The two runtimes historically
differ in wording and in a few checks; those differences are kept as explicit
per-runtime policies rather than separate implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any, Mapping

from notewitness.analysis.local_tools.contracts import LocalToolFailure
from notewitness.projects.artifacts import write_new_private_json
from notewitness.projects.media import is_project_media_uri
from notewitness.projects.private_fs import content_stat_identity, is_owner_private
from notewitness.projects.store import ProjectSnapshot, ProjectStore


_DIRECTORY_NAME_CHARACTERS = "abcdefghijklmnopqrstuvwxyz0123456789-"


@dataclass(frozen=True, slots=True)
class FileIdentityPolicy:
    """Error wording and symlink handling for one runtime's file identities."""

    error_type: type[Exception]
    unavailable: str = "Runtime artifact could not be identified safely."
    not_regular: str = "Runtime artifact is not a regular file."
    changed: str = "Runtime artifact changed during identity verification."
    follow_symlinks: bool = False


@dataclass(frozen=True, slots=True)
class SourceMediaPolicy:
    """How one runtime resolves and re-verifies its project media source.

    ``nested_media`` permits ``media/<dir>/<name>`` in addition to
    ``media/<name>``. ``owner_private_leaf`` requires an owner-private regular
    file at resolution time; otherwise the leaf is only required not to be a
    symlink. ``malformed_sources`` set to ``None`` treats a non-list sources
    collection as having no matching source.
    """

    error_type: type[Exception]
    not_found: str
    invalid_uri: str
    not_project_media: str
    leaf_invalid: str
    checksum: FileIdentityPolicy
    checksum_mismatch: str
    malformed_sources: str | None = None
    nested_media: bool = False
    owner_private_leaf: bool = False
    leaf_unavailable: str = ""


def now() -> str:
    """Return the UTC timestamp written into run status records."""

    return datetime.now(timezone.utc).isoformat()


def valid_run_token(value: object) -> bool:
    """Accept exactly 32 lowercase hexadecimal characters."""

    return (
        isinstance(value, str)
        and len(value) == 32
        and all(character in "0123456789abcdef" for character in value)
    )


def source_media(
    store: ProjectStore,
    snapshot: ProjectSnapshot,
    source_id: str,
    policy: SourceMediaPolicy,
) -> tuple[dict[str, Any], Path]:
    """Resolve one ingested project media source without following its leaf."""

    sources = snapshot.payload.get("sources")
    if not isinstance(sources, list):
        if policy.malformed_sources is not None:
            raise policy.error_type(policy.malformed_sources)
        sources = []
    matches = [
        item
        for item in sources
        if isinstance(item, dict) and item.get("id") == source_id
    ]
    if len(matches) != 1:
        raise policy.error_type(policy.not_found)
    source = matches[0]
    uri = source.get("uri")
    if not isinstance(uri, str):
        raise policy.error_type(policy.invalid_uri)
    relative = PurePosixPath(uri)
    if not is_project_media_uri(uri, nested=policy.nested_media):
        raise policy.error_type(policy.not_project_media)
    media_path = store.root.joinpath(*relative.parts)
    if policy.owner_private_leaf:
        _require_owner_private_leaf(media_path, policy)
    elif media_path.is_symlink():
        raise policy.error_type(policy.leaf_invalid)
    return source, media_path


def _require_owner_private_leaf(path: Path, policy: SourceMediaPolicy) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise policy.error_type(policy.leaf_unavailable) from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or not is_owner_private(metadata)
    ):
        raise policy.error_type(policy.leaf_invalid)


def require_source_checksum(
    path: Path, expected_sha256: str, policy: SourceMediaPolicy
) -> None:
    """Re-hash resolved source media and reject any content change."""

    digest, _ = file_identity(path, policy.checksum)
    if digest != expected_sha256:
        raise policy.error_type(policy.checksum_mismatch)


def file_identity(path: Path, policy: FileIdentityPolicy) -> tuple[str, int]:
    """Hash one regular file and reject a change while it is being read."""

    flags = os.O_RDONLY | (0 if policy.follow_symlinks else os.O_NOFOLLOW)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise policy.error_type(policy.not_regular)
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise policy.error_type(policy.unavailable) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if content_stat_identity(before) != content_stat_identity(after):
        raise policy.error_type(policy.changed)
    return digest.hexdigest(), size


def create_run_directory(
    store: ProjectStore,
    name: str,
    *,
    error_type: type[Exception],
    failure_message: str = "Could not create private run storage.",
) -> Path:
    """Create a new owner-private ``runs/<name>`` directory; never reuse one."""

    return create_private_directory(
        store.ensure_private_directory("runs"),
        name,
        error_type=error_type,
        failure_message=failure_message,
    )


def create_private_directory(
    parent: Path,
    name: str,
    *,
    error_type: type[Exception],
    failure_message: str = "Could not create private run storage.",
) -> Path:
    """Create one new owner-private child directory and verify its mode."""

    if not name or any(character not in _DIRECTORY_NAME_CHARACTERS for character in name):
        raise error_type("Private run directory name is invalid.")
    path = parent / name
    try:
        os.mkdir(path, 0o700)
        os.chmod(path, 0o700, follow_symlinks=False)
        metadata = path.lstat()
    except OSError as exc:
        raise error_type(failure_message) from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise error_type("Run storage is not an owner-private directory.")
    return path


def write_queued_status(run_directory: Path, *, run_id: str, source_id: str) -> None:
    """Record that a run directory exists before any provider is started."""

    write_new_private_json(
        run_directory / "status.queued.json",
        {
            "run_id": run_id,
            "source_id": source_id,
            "state": "queued",
            "timestamp": now(),
            "network_mode": "offline",
        },
    )


def write_status(
    run_directory: Path,
    filename: str,
    record: Mapping[str, Any],
    *,
    suppressed: tuple[type[BaseException], ...] = (Exception,),
) -> None:
    """Best-effort terminal status; the publication and project stay authoritative."""

    try:
        write_new_private_json(run_directory / filename, {**record, "timestamp": now()})
    except suppressed:
        return


def failure_code(error: Exception) -> str:
    """Summarize a failure without retaining provider output or paths."""

    if isinstance(error, LocalToolFailure):
        return f"local_tool:{error.tool_name}:exit:{error.return_code}"
    return type(error).__name__
