"""Approved executable discovery and immutable identity checks."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import shutil
import stat
import sys
from typing import Literal

from notewitness.analysis.local_tools.contracts import (
    LocalExecutableIdentity,
    LocalToolIdentityChanged,
    LocalToolUnavailable,
)
from notewitness.projects.private_fs import is_owner_private


@dataclass(frozen=True, slots=True)
class LocalTool:
    name: str
    executable: Path
    identity: LocalExecutableIdentity = field(init=False)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Local tools require a non-empty name.")
        resolved = _validated_executable(self.executable)
        identity = _executable_identity(resolved)
        object.__setattr__(self, "executable", resolved)
        object.__setattr__(self, "identity", identity)

    def require_unchanged(self) -> None:
        """Reject replacement or mutation since this tool was discovered."""

        try:
            current = _executable_identity(self.executable)
        except LocalToolUnavailable as exc:
            raise LocalToolIdentityChanged(
                f"Local tool {self.name!r} changed after discovery."
            ) from exc
        if current != self.identity:
            raise LocalToolIdentityChanged(
                f"Local tool {self.name!r} changed after discovery."
            )


def discover_local_tool(name: str, explicit_path: str | Path | None = None) -> LocalTool:
    """Resolve one executable without running it or mutating the machine."""

    if not name or any(character in name for character in ("/", "\\", "\x00")):
        raise ValueError("Tool names must be plain executable names.")
    if explicit_path is None:
        candidate = shutil.which(name)
    else:
        raw = Path(explicit_path)
        if not raw.is_absolute():
            raise LocalToolUnavailable("Explicit tool paths must be absolute.")
        candidate = os.fspath(raw)
    if candidate is None:
        raise LocalToolUnavailable(f"Required local tool {name!r} was not found.")
    return LocalTool(name=name, executable=Path(candidate))


def _validated_executable(path: Path) -> Path:
    try:
        resolved = validated_trusted_path(
            path, kind="file", allow_package_manager_parents=True
        )
        # The runner trusts the executable's own directory as a strict leaf, so
        # refuse here, naming it, rather than failing later without a path.
        validated_trusted_path(
            resolved.parent, kind="directory", allow_package_manager_parents=True
        )
        metadata = resolved.stat()
    except (OSError, ValueError) as exc:
        raise LocalToolUnavailable(str(exc)) from exc
    if not stat.S_ISREG(metadata.st_mode) or not os.access(resolved, os.X_OK):
        raise LocalToolUnavailable("Local tool path is not an executable regular file.")
    return resolved


def validated_trusted_path(
    path: Path,
    *,
    kind: Literal["file", "directory"],
    allow_package_manager_parents: bool = False,
) -> Path:
    """Return a canonical path whose components cannot be replaced by peers.

    Root-owned system paths and current-user paths are accepted for ordinary
    local runs.  Privilege transitions accept only effective-user-owned paths:
    a user-controlled path must not be trusted by an elevated process.
    ``allow_package_manager_parents`` is for operator executables and their
    directories only; see ``_admin_group_write_permitted``.
    """

    if not path.is_absolute():
        raise ValueError("Trusted paths must be absolute.")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Trusted path is unavailable: {path}") from exc
    components = (
        Path(resolved.anchor),
        *(
            Path(resolved.anchor, *resolved.parts[1:index])
            for index in range(2, len(resolved.parts) + 1)
        ),
    )
    for index, component in enumerate(components):
        try:
            metadata = component.lstat()
        except OSError as exc:
            raise ValueError(f"Trusted path is unavailable: {component}") from exc
        is_leaf = index == len(components) - 1
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"Trusted paths must not contain symlinks: {component}")
        if not is_leaf and not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(f"Trusted path component is not a directory: {component}")
        if stat.S_IMODE(metadata.st_mode) & 0o022 and not (
            allow_package_manager_parents
            and not is_leaf
            and _admin_group_write_permitted(component, metadata)
        ):
            raise ValueError(
                "Trusted path components must not be group or world writable: "
                f"{component}"
            )
        if not _trusted_owner(metadata.st_uid):
            raise ValueError(
                f"Trusted path components must have a trusted owner: {component}"
            )
    leaf = components[-1]
    try:
        metadata = leaf.stat()
    except OSError as exc:
        raise ValueError(f"Trusted path is unavailable: {leaf}") from exc
    if kind == "file" and not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"Trusted path is not a regular file: {leaf}")
    if kind == "directory" and not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"Trusted path is not a directory: {leaf}")
    return resolved


def validated_private_current_user_directory(path: Path) -> Path:
    """Require a private current-UID directory for tool working files."""

    resolved = validated_trusted_path(path, kind="directory")
    try:
        metadata = resolved.stat()
    except OSError as exc:
        raise ValueError("Private directory is unavailable.") from exc
    if not is_owner_private(metadata):
        raise ValueError(
            "Private directory must deny group and other access and be owned "
            "by the current user."
        )
    return resolved


_MACOS_ADMIN_GID = 80
_PACKAGE_MANAGER_PREFIXES = (Path("/opt/homebrew"), Path("/usr/local"))


def _admin_group_write_permitted(component: Path, metadata: os.stat_result) -> bool:
    """Accept Homebrew's admin-group-writable directories, and nothing broader.

    Homebrew makes directories under its prefix writable by the macOS ``admin``
    group.  Accepting that lets other admin-group accounts replace a tool
    between the identity check and exec without their sudo password; the
    operator accepts that to use Homebrew tools.  Only non-leaf directories at
    or below the Homebrew prefixes qualify; world write access, other groups or
    owners, root processes and privilege transitions stay refused.  The
    standalone child launcher (``launcher.py``) repeats this rule and must stay
    identical.
    """

    return (
        sys.platform == "darwin"
        and os.getuid() != 0
        and not stat.S_IMODE(metadata.st_mode) & 0o002
        and metadata.st_gid == _MACOS_ADMIN_GID
        and metadata.st_uid in {os.getuid(), 0}
        and os.geteuid() == os.getuid()
        and any(
            component == prefix or prefix in component.parents
            for prefix in _PACKAGE_MANAGER_PREFIXES
        )
    )


def _trusted_owner(owner_uid: int) -> bool:
    current_uid = os.getuid()
    effective_uid = os.geteuid()
    if effective_uid != current_uid:
        return owner_uid == effective_uid
    return owner_uid in {current_uid, 0}


def _executable_identity(path: Path) -> LocalExecutableIdentity:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise LocalToolUnavailable("Local tool executable is unavailable.") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise LocalToolUnavailable(
                "Local tool path is not an executable regular file."
            )
        if stat.S_IMODE(before.st_mode) & 0o022:
            raise LocalToolUnavailable(
                "Local tool executable must not be group or world writable."
            )
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise LocalToolUnavailable("Local tool executable is unavailable.") from exc
    finally:
        os.close(descriptor)
    if executable_stat_identity(before) != executable_stat_identity(after):
        raise LocalToolUnavailable(
            "Local tool executable changed while its identity was captured."
        )
    return LocalExecutableIdentity(
        sha256=digest.hexdigest(),
        size_bytes=before.st_size,
        device=before.st_dev,
        inode=before.st_ino,
        owner_uid=before.st_uid,
        mode=stat.S_IMODE(before.st_mode),
        modified_ns=before.st_mtime_ns,
        changed_ns=before.st_ctime_ns,
    )


def executable_stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    """Identify an operator artifact including its owner and permission bits."""
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
        stat.S_IMODE(metadata.st_mode),
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )
