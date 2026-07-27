"""Private, atomic persistence for one local NoteWitness project."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import copy
from dataclasses import dataclass
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Mapping
from uuid import uuid4

from notewitness.evidence import EvidenceGraph, EvidenceGraphError, MAX_PROJECT_BYTES


_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600
_PROJECT_NAME = "project.json"
_LOCK_NAME = ".project.lock"


class ProjectStoreError(RuntimeError):
    """A project document cannot be accessed safely."""


class ProjectConflictError(ProjectStoreError):
    """The caller attempted to update an unexpected document version."""


@dataclass(frozen=True, slots=True)
class ProjectSnapshot:
    """One validated project document and the digest of its on-disk bytes."""

    payload: dict[str, Any]
    sha256: str


Mutation = Callable[[dict[str, Any]], None]


class ProjectStore:
    """Owner-private project storage accepting its root or ``project.json``."""

    def __init__(self, project_root: str | Path) -> None:
        target = Path(project_root)
        if target.name == _PROJECT_NAME:
            target = target.parent
        self.root = _trusted_absolute_path(target)

    def load(self) -> ProjectSnapshot:
        """Load and validate the current project document without mutating it."""
        with self._open_root() as root_descriptor:
            return self._load_from(root_descriptor)

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Hold the project-local advisory writer lock for a short transaction."""
        with self._open_root() as root_descriptor:
            descriptor = _open_lock(root_descriptor)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def mutate(
        self,
        mutation: Mutation,
        *,
        expected_sha256: str | None = None,
    ) -> ProjectSnapshot:
        """Validate and atomically publish a mutation, optionally compare-and-swap."""
        if expected_sha256 is not None and not _is_sha256(expected_sha256):
            raise ProjectConflictError("expected_sha256 must be a lowercase SHA-256 digest")
        with self.locked():
            with self._open_root() as root_descriptor:
                return self._mutate_from(root_descriptor, mutation, expected_sha256)

    def ensure_private_directory(self, name: str) -> Path:
        """Create or verify one allowlisted owner-private runtime directory."""

        if name not in {"exports", "runs"}:
            raise ProjectStoreError("runtime directory name is not allowlisted")
        with self.locked():
            with self._open_root() as root_descriptor:
                try:
                    os.mkdir(name, _DIRECTORY_MODE, dir_fd=root_descriptor)
                except FileExistsError:
                    pass
                descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=root_descriptor,
                )
                try:
                    _require_private_directory(descriptor, self.root / name)
                    os.fchmod(descriptor, _DIRECTORY_MODE)
                finally:
                    os.close(descriptor)
        return self.root / name

    @contextmanager
    def _open_root(self) -> Iterator[int]:
        descriptor = _open_existing_private_directory(self.root)
        try:
            yield descriptor
        finally:
            os.close(descriptor)

    @staticmethod
    def _load_from(root_descriptor: int) -> ProjectSnapshot:
        raw = _read_private_regular_file(root_descriptor, _PROJECT_NAME)
        payload = _parse_payload(raw)
        _validate_payload(payload)
        return ProjectSnapshot(payload, hashlib.sha256(raw).hexdigest())

    @staticmethod
    def _mutate_from(
        root_descriptor: int,
        mutation: Mutation,
        expected_sha256: str | None,
    ) -> ProjectSnapshot:
        """Mutate while the caller owns ``locked()``."""
        before = ProjectStore._load_from(root_descriptor)
        if expected_sha256 is not None and before.sha256 != expected_sha256:
            raise ProjectConflictError("project document changed before mutation")
        candidate = copy.deepcopy(before.payload)
        outcome = mutation(candidate)
        if outcome is not None:
            raise ProjectStoreError("mutation must update its payload in place and return None")
        _validate_payload(candidate)
        raw = _serialize(candidate)
        if len(raw) > MAX_PROJECT_BYTES:
            raise ProjectStoreError(f"project document exceeds {MAX_PROJECT_BYTES} bytes")
        _replace_private_file(root_descriptor, _PROJECT_NAME, raw)
        return ProjectSnapshot(candidate, hashlib.sha256(raw).hexdigest())


def _trusted_absolute_path(target: Path) -> Path:
    absolute_target = Path(os.path.abspath(os.fspath(target)))
    var_alias = Path("/var")
    private_var = Path("/private/var")
    if absolute_target == var_alias or var_alias in absolute_target.parents:
        if var_alias.is_symlink() and Path(os.path.realpath(var_alias)) == private_var:
            return private_var / absolute_target.relative_to(var_alias)
    return absolute_target


def _open_existing_private_directory(directory: Path) -> int:
    if directory == Path(os.path.sep):
        raise ProjectStoreError("project root must not be the filesystem root")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(os.path.sep, flags)
    try:
        for component in directory.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        _require_private_directory(descriptor, directory)
        return descriptor
    except OSError as exc:
        os.close(descriptor)
        if exc.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
            raise ProjectStoreError(
                f"project root must be an existing non-symlink directory: {directory}"
            ) from exc
        raise
    except BaseException:
        os.close(descriptor)
        raise


def _require_private_directory(descriptor: int, label: Path) -> None:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        raise ProjectStoreError(f"project root is not a directory: {label}")
    if metadata.st_uid != os.getuid():
        raise ProjectStoreError("project root must be owned by the current user")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ProjectStoreError("project root must deny group and other access")


def _read_private_regular_file(directory_descriptor: int, name: str) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_descriptor)
        metadata = os.fstat(descriptor)
        _require_private_regular(metadata, name)
        chunks: list[bytes] = []
        remaining = MAX_PROJECT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_PROJECT_BYTES:
            raise ProjectStoreError(f"project document exceeds {MAX_PROJECT_BYTES} bytes")
        return raw
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOENT, errno.ENOTDIR}:
            raise ProjectStoreError(
                f"project document is not a safe regular file: {name}"
            ) from exc
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _require_private_regular(metadata: os.stat_result, name: str) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise ProjectStoreError(f"project path is not a regular file: {name}")
    if metadata.st_uid != os.getuid():
        raise ProjectStoreError(f"project file must be owned by the current user: {name}")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ProjectStoreError(f"project file must deny group and other access: {name}")


def _parse_payload(raw: bytes) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate object key {key!r}")
            result[key] = value
        return result

    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProjectStoreError(f"project document contains invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProjectStoreError("project document must be a JSON object")
    return payload


def _validate_payload(payload: Mapping[str, Any]) -> None:
    try:
        EvidenceGraph(payload).require_valid()
    except EvidenceGraphError as exc:
        raise ProjectStoreError(f"project graph is invalid: {exc}") from exc


def _serialize(payload: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProjectStoreError(f"project mutation is not JSON serializable: {exc}") from exc


def _replace_private_file(directory_descriptor: int, name: str, raw: bytes) -> None:
    existing = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    _require_private_regular(existing, name)
    temporary = f".{name}.{uuid4().hex}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            _FILE_MODE,
            dir_fd=directory_descriptor,
        )
        os.fchmod(descriptor, _FILE_MODE)
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(
            temporary,
            name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        os.fsync(directory_descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory_descriptor)
        except FileNotFoundError:
            pass


def _open_lock(directory_descriptor: int) -> int:
    descriptor = os.open(
        _LOCK_NAME,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        _FILE_MODE,
        dir_fd=directory_descriptor,
    )
    try:
        _require_private_regular(os.fstat(descriptor), _LOCK_NAME)
        os.fchmod(descriptor, _FILE_MODE)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
