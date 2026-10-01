"""Owner-private filesystem primitives: pinned directories and no-follow walks."""

from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import stat
from typing import Iterator


class PrivatePathError(RuntimeError):
    """A private directory was replaced, linked, or no longer owner-private."""


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def open_private_directory(path: str | Path) -> "PrivateDirectory":
    """Pin an existing private directory and retain its no-follow descriptor."""
    absolute = trusted_absolute_path(Path(path))
    descriptor = _open_existing_private_directory(absolute)
    try:
        metadata = os.fstat(descriptor)
        _require_private_directory(metadata, absolute)
        return PrivateDirectory(absolute, descriptor, metadata.st_dev, metadata.st_ino)
    except BaseException:
        os.close(descriptor)
        raise


class PrivateDirectory:
    """A directory FD whose pathname must continue to identify the same directory."""

    def __init__(self, path: Path, descriptor: int, device: int, inode: int) -> None:
        self.path = path
        self._descriptor = descriptor
        self._identity = (device, inode)

    def __enter__(self) -> "PrivateDirectory":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def descriptor(self) -> int:
        self.require_current()
        return self._descriptor

    @property
    def identity(self) -> tuple[int, int]:
        return self._identity

    def close(self) -> None:
        if self._descriptor >= 0:
            os.close(self._descriptor)
            self._descriptor = -1

    def require_current(self) -> None:
        """Fail closed if the public path no longer resolves to this pinned FD."""
        if self._descriptor < 0:
            raise PrivatePathError("private directory capability is closed")
        metadata = os.fstat(self._descriptor)
        _require_private_directory(metadata, self.path)
        if (metadata.st_dev, metadata.st_ino) != self._identity:
            raise PrivatePathError("private directory identity changed")
        current = _open_existing_private_directory(self.path)
        try:
            current_metadata = os.fstat(current)
            _require_private_directory(current_metadata, self.path)
            if (current_metadata.st_dev, current_metadata.st_ino) != self._identity:
                raise PrivatePathError("private directory identity changed")
        finally:
            os.close(current)

    def open_file(self, name: str, flags: int, mode: int = 0o600) -> int:
        """Open one flat child through the pinned directory without following links."""
        _flat_name(name)
        self.require_current()
        descriptor: int | None = None
        try:
            descriptor = os.open(name, flags | os.O_NOFOLLOW, mode, dir_fd=self._descriptor)
            self.require_current()
            return descriptor
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            raise

    def stat(self, name: str) -> os.stat_result:
        _flat_name(name)
        self.require_current()
        result = os.stat(name, dir_fd=self._descriptor, follow_symlinks=False)
        self.require_current()
        return result

    def unlink(self, name: str) -> None:
        _flat_name(name)
        self.require_current()
        os.unlink(name, dir_fd=self._descriptor)
        self.require_current()


@contextmanager
def private_directory(path: str | Path) -> Iterator[PrivateDirectory]:
    capability = open_private_directory(path)
    try:
        yield capability
    finally:
        capability.close()


def is_owner_private(metadata: os.stat_result) -> bool:
    """Whether the current user owns the file and group/other have no access."""
    return metadata.st_uid == os.getuid() and not stat.S_IMODE(metadata.st_mode) & 0o077


def require_private_regular(metadata: os.stat_result, label: str) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise PrivatePathError(f"private path is not a regular file: {label}")
    if not is_owner_private(metadata):
        raise PrivatePathError(f"private file is not owner-private: {label}")


def _flat_name(name: str) -> None:
    if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        raise PrivatePathError("private child name must be a flat filename")


def trusted_absolute_path(path: str | Path) -> Path:
    """Return a lexical absolute path, allowing only macOS's system ``/var`` alias."""
    absolute = Path(os.path.abspath(os.fspath(path)))
    var = Path("/var")
    private_var = Path("/private/var")
    if (absolute == var or var in absolute.parents) and var.is_symlink():
        # macOS exposes /var as this OS-owned alias. Normalize it before opening
        # from / so every user-controlled component is still opened no-follow.
        if Path(os.path.realpath(var)) == private_var:
            return private_var / absolute.relative_to(var)
    return absolute


def open_directory_no_follow(path: Path) -> int:
    """Walk an absolute path from ``/`` without following any symlink component.

    Returns the final directory descriptor; walk failures propagate as ``OSError``
    so each caller keeps its own error vocabulary.
    """
    if not path.is_absolute():
        raise ValueError("no-follow directory walk requires an absolute path")
    descriptor = os.open(os.path.sep, _DIRECTORY_FLAGS)
    try:
        for component in path.parts[1:]:
            child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def content_stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    """Identify one file's inode and content-change state for before/after checks."""
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _open_existing_private_directory(path: Path) -> int:
    if not path.is_absolute() or path == Path(os.path.sep):
        raise PrivatePathError("private directory must be an existing non-root directory")
    try:
        return open_directory_no_follow(path)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOENT, errno.ENOTDIR}:
            raise PrivatePathError("private directory must be existing and contain no symlinks") from exc
        raise


def _require_private_directory(metadata: os.stat_result, label: Path) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise PrivatePathError(f"private path is not a directory: {label}")
    if not is_owner_private(metadata):
        raise PrivatePathError(f"private directory is not owner-private: {label}")
