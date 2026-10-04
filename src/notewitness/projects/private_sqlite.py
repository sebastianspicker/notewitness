"""Hardened, owner-private SQLite connection lifecycle for feature job stores."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from enum import Enum
import errno
import os
from pathlib import Path
import sqlite3
import stat
import sys

from notewitness.projects.private_fs import (
    PrivateDirectory,
    PrivatePathError,
    private_directory,
    require_private_regular,
    trusted_absolute_path,
)


_FILE_MODE = 0o600
_MIN_BUSY_TIMEOUT_MS = 1
_MAX_BUSY_TIMEOUT_MS = 60_000
_SIDECAR_SUFFIXES = ("", "-wal", "-shm")


class PrivateSQLiteFailure(Enum):
    """Failure kinds each store translates into its own exception vocabulary."""

    PARENT_NOT_PRIVATE = "parent_not_private"
    PARENT_IDENTITY_CHANGED = "parent_identity_changed"
    DATABASE_NOT_REGULAR = "database_not_regular"
    DATABASE_NOT_PRIVATE = "database_not_private"
    SIDECAR_NOT_REGULAR = "sidecar_not_regular"
    SIDECAR_NOT_PRIVATE = "sidecar_not_private"
    SIDECAR_ACCESS_FAILED = "sidecar_access_failed"


ErrorTranslator = Callable[[PrivateSQLiteFailure], Exception]


def validated_busy_timeout_ms(value: object) -> int:
    """Validate the only dynamic value used in SQLite's PRAGMA statement."""
    if type(value) is not int or not _MIN_BUSY_TIMEOUT_MS <= value <= _MAX_BUSY_TIMEOUT_MS:
        raise ValueError("busy_timeout_ms must be between 1 and 60000.")
    return value


def busy_timeout_pragma(value: int) -> str:
    """Render the validated, parameter-free SQLite PRAGMA syntax."""
    return "PRAGMA busy_timeout = " + str(validated_busy_timeout_ms(value))


class PrivateSQLiteDatabase:
    """One owner-private database file under a pinned owner-private parent.

    ``errors`` maps each failure kind to the owning store's exception.
    ``leading_pragmas`` run before the shared busy-timeout/WAL/FULL pragmas.
    When ``defer_close_failure_to_active_exception`` is true, a sidecar close
    failure never replaces any exception already being handled; otherwise it
    is suppressed only after this module's own sidecar validation failed.
    """

    def __init__(
        self,
        path: Path,
        *,
        errors: ErrorTranslator,
        busy_timeout_ms: int,
        leading_pragmas: tuple[str, ...] = (),
        defer_close_failure_to_active_exception: bool = False,
    ) -> None:
        self.path = trusted_absolute_path(path)
        self._errors = errors
        self._busy_timeout_ms = validated_busy_timeout_ms(busy_timeout_ms)
        self._leading_pragmas = leading_pragmas
        self._defer_close_failure = defer_close_failure_to_active_exception
        try:
            with private_directory(
                self.path.parent, require_trusted_ancestors=True
            ) as parent:
                self._parent_identity = parent.identity
        except PrivatePathError as exc:
            raise errors(PrivateSQLiteFailure.PARENT_NOT_PRIVATE) from exc

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        try:
            with private_directory(
                self.path.parent, require_trusted_ancestors=True
            ) as parent:
                if parent.identity != self._parent_identity:
                    raise PrivatePathError("database parent identity changed")
                self._prepare_path(parent)
                # sqlite3 has no dir_fd/custom-VFS interface.  Revalidate the
                # parent immediately around its pathname-only open and reject
                # the operation if a replacement or symlink is observed.
                parent.require_current()
                connection = sqlite3.connect(
                    self.path,
                    timeout=self._busy_timeout_ms / 1000,
                    isolation_level=None,
                )
                connection.row_factory = sqlite3.Row
                try:
                    for pragma in self._leading_pragmas:
                        connection.execute(pragma)
                    connection.execute(busy_timeout_pragma(self._busy_timeout_ms))
                    connection.execute("PRAGMA journal_mode = WAL")
                    connection.execute("PRAGMA synchronous = FULL")
                    parent.require_current()
                    yield connection
                    parent.require_current()
                finally:
                    connection.close()
                    self.secure_sidecars(parent)
                    parent.require_current()
        except PrivatePathError as exc:
            raise self._errors(PrivateSQLiteFailure.PARENT_IDENTITY_CHANGED) from exc

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")

    def secure_sidecars(self, parent: PrivateDirectory) -> None:
        """Privatize the database file and any WAL/SHM sidecar through their FDs."""
        for suffix in _SIDECAR_SUFFIXES:
            self.secure_sidecar(parent, self.path.name + suffix)

    def secure_sidecar(self, parent: PrivateDirectory, name: str) -> None:
        """Privatize one flat child of ``parent`` with this store's error policy."""
        secure_sidecar(
            parent,
            name,
            self._errors,
            defer_close_failure_to_active_exception=self._defer_close_failure,
        )

    def _prepare_path(self, parent: PrivateDirectory) -> None:
        try:
            self._require_private_database(parent.stat(self.path.name))
        except FileNotFoundError:
            descriptor = parent.open_file(
                self.path.name, os.O_RDWR | os.O_CREAT | os.O_EXCL, _FILE_MODE
            )
            try:
                self._require_private_database(os.fstat(descriptor))
            finally:
                os.close(descriptor)

    def _require_private_database(self, metadata: os.stat_result) -> None:
        try:
            require_private_regular(metadata, self.path.name)
        except PrivatePathError as exc:
            if stat.S_ISREG(metadata.st_mode):
                raise self._errors(PrivateSQLiteFailure.DATABASE_NOT_PRIVATE) from exc
            raise self._errors(PrivateSQLiteFailure.DATABASE_NOT_REGULAR) from exc


def secure_sidecar(
    parent: PrivateDirectory,
    name: str,
    errors: ErrorTranslator,
    *,
    defer_close_failure_to_active_exception: bool = False,
) -> None:
    """Secure an opened SQLite file so a pathname swap cannot redirect chmod."""
    descriptor = _open_sidecar_descriptor(parent, name, errors)
    if descriptor is None:
        return
    failed = False
    try:
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                failed = True
                raise errors(PrivateSQLiteFailure.SIDECAR_NOT_REGULAR)
            if metadata.st_uid != os.getuid():
                failed = True
                raise errors(PrivateSQLiteFailure.SIDECAR_NOT_PRIVATE)
            os.fchmod(descriptor, _FILE_MODE)
        except OSError as exc:
            failed = True
            raise errors(PrivateSQLiteFailure.SIDECAR_ACCESS_FAILED) from exc
    finally:
        preserve_failure = failed or (
            defer_close_failure_to_active_exception and sys.exception() is not None
        )
        _close_sidecar_descriptor(descriptor, errors, preserve_failure=preserve_failure)


def _open_sidecar_descriptor(
    parent: PrivateDirectory, name: str, errors: ErrorTranslator
) -> int | None:
    """Open a sidecar or classify its race-safe absence and unsafe path errors."""
    try:
        return parent.open_file(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
    except FileNotFoundError:
        # SQLite may remove WAL and SHM files while its connection closes.
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise errors(PrivateSQLiteFailure.SIDECAR_NOT_REGULAR) from exc
        raise errors(PrivateSQLiteFailure.SIDECAR_ACCESS_FAILED) from exc


def _close_sidecar_descriptor(
    descriptor: int, errors: ErrorTranslator, *, preserve_failure: bool
) -> None:
    """Close an opened sidecar without obscuring an earlier failure."""
    try:
        os.close(descriptor)
    except OSError as exc:
        if not preserve_failure:
            raise errors(PrivateSQLiteFailure.SIDECAR_ACCESS_FAILED) from exc
