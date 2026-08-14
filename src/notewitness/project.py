"""Creation of empty, private-by-default NoteWitness project documents."""

from __future__ import annotations

from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
from uuid import uuid4

from notewitness.evidence import EvidenceGraph, SCHEMA_VERSION


class ProjectInitializationError(RuntimeError):
    pass


_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600


def _trusted_absolute_path(target: Path) -> Path:
    """Return a lexical absolute path, allowing only macOS's system ``/var`` alias."""
    absolute_target = Path(os.path.abspath(os.fspath(target)))
    var_alias = Path("/var")
    private_var = Path("/private/var")
    if absolute_target == var_alias or var_alias in absolute_target.parents:
        # macOS exposes /var as this OS-owned alias. Normalize it before opening
        # from / so every user-controlled component is still opened no-follow.
        if var_alias.is_symlink() and Path(os.path.realpath(var_alias)) == private_var:
            return private_var / absolute_target.relative_to(var_alias)
    return absolute_target


def _open_or_create_private_directory(
    parent_descriptor: int, component: str
) -> int:
    """Open a directory component, creating it with private permissions if absent."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        return os.open(component, flags, dir_fd=parent_descriptor)
    except FileNotFoundError:
        _create_private_directory_if_absent(parent_descriptor, component)
    return os.open(component, flags, dir_fd=parent_descriptor)


def _create_private_directory_if_absent(
    parent_descriptor: int, component: str
) -> None:
    """Create and restrict a previously absent directory component."""
    try:
        os.mkdir(component, _DIRECTORY_MODE, dir_fd=parent_descriptor)
    except FileExistsError:
        return
    os.chmod(
        component,
        _DIRECTORY_MODE,
        dir_fd=parent_descriptor,
        follow_symlinks=False,
    )


def _open_private_directory_component(
    parent_descriptor: int, component: str, target: Path
) -> int:
    """Open one trusted directory component without following symlinks."""
    try:
        return _open_or_create_private_directory(parent_descriptor, component)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ProjectInitializationError(
                f"Project target contains a symlink or non-directory component: {target}"
            ) from exc
        raise


def _require_empty_private_directory(descriptor: int, target: Path) -> None:
    """Require an empty target directory before restoring its private mode."""
    if os.listdir(descriptor):
        raise ProjectInitializationError(
            f"Refusing to initialize non-empty directory: {target}"
        )
    os.fchmod(descriptor, _DIRECTORY_MODE)


def _open_empty_private_directory(target: Path) -> int:
    """Open an empty project directory from the trusted root descriptor."""
    absolute_target = _trusted_absolute_path(target)
    if absolute_target == Path(os.path.sep):
        raise ProjectInitializationError("Refusing to initialize the filesystem root.")
    if absolute_target.is_symlink():
        raise ProjectInitializationError(f"Project target is a symlink: {target}")

    descriptor = os.open(os.path.sep, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    keep_descriptor = False
    try:
        for component in absolute_target.parts[1:]:
            child_descriptor = _open_private_directory_component(
                descriptor, component, target
            )
            os.close(descriptor)
            descriptor = child_descriptor
        _require_empty_private_directory(descriptor, target)
        keep_descriptor = True
        return descriptor
    finally:
        if not keep_descriptor:
            os.close(descriptor)


def _write_private_file(directory_descriptor: int, name: str, contents: str) -> None:
    """Atomically publish a new private file without replacing an existing path."""
    temporary_name = f".{name}.{uuid4().hex}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            _FILE_MODE,
            dir_fd=directory_descriptor,
        )
        os.fchmod(descriptor, _FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
            descriptor = None
            temporary_file.write(contents)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.link(
            temporary_name,
            name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        os.unlink(temporary_name, dir_fd=directory_descriptor)
    except FileExistsError as exc:
        raise ProjectInitializationError(
            f"Refusing to replace existing project path: {name}"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory_descriptor)
        except FileNotFoundError:
            pass


def _create_private_project_directory(
    project_descriptor: int, directory_name: str
) -> None:
    """Create a project-owned directory without replacing an existing path."""
    try:
        os.mkdir(directory_name, _DIRECTORY_MODE, dir_fd=project_descriptor)
    except FileExistsError as exc:
        raise ProjectInitializationError(
            f"Refusing to replace existing project path: {directory_name}"
        ) from exc
    os.chmod(
        directory_name,
        _DIRECTORY_MODE,
        dir_fd=project_descriptor,
        follow_symlinks=False,
    )


def _write_private_media_readme(project_descriptor: int) -> None:
    """Write the media directory's private-use notice through its descriptor."""
    media_descriptor = os.open(
        "media",
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=project_descriptor,
    )
    try:
        os.fchmod(media_descriptor, _DIRECTORY_MODE)
        _write_private_file(
            media_descriptor,
            "README.txt",
            "Private source media belongs here. Do not commit this directory.\n",
        )
    finally:
        os.close(media_descriptor)


def _initialize_project_contents(project_descriptor: int, payload: dict[str, object]) -> None:
    """Publish the initial private project document and required directories."""
    _write_private_file(
        project_descriptor,
        "project.json",
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    for directory_name in ("media", "runs", "exports"):
        _create_private_project_directory(project_descriptor, directory_name)
    _write_private_media_readme(project_descriptor)


def initialize_project(directory: str | Path, *, name: str | None = None) -> Path:
    target = Path(directory)
    project_name = (name or target.name).strip()
    if not project_name:
        raise ProjectInitializationError("Project name must not be empty.")

    payload = {
        "schema_version": SCHEMA_VERSION,
        "project": {
            "id": f"project:{uuid4()}",
            "name": project_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "network": {"mode": "offline"},
        "rights": [],
        "sources": [],
        "actors": [],
        "targets": [],
        "generators": [],
        "events": [],
        "relations": [],
        "revisions": [],
    }
    EvidenceGraph(payload).require_valid()

    project_descriptor = _open_empty_private_directory(target)
    try:
        _initialize_project_contents(project_descriptor, payload)
    finally:
        os.close(project_descriptor)
    return target / "project.json"
