"""Streaming, local-only media ingest for private NoteWitness projects."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path
import stat
from typing import Protocol

from notewitness.project_store import ProjectSnapshot, ProjectStore, ProjectStoreError


_FILE_MODE = 0o600
_CHUNK_SIZE = 1024 * 1024
# 20 GiB accommodates multi-hour, high-bitrate local lesson recordings while
# keeping one ingest transaction bounded against accidental or hostile inputs.
MAX_INGEST_BYTES = 20 * 1024 * 1024 * 1024
_INGEST_FREE_SPACE_MARGIN = 16 * 1024 * 1024


class MediaIngestError(RuntimeError):
    """A local media item could not be ingested without weakening project safety."""


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    """Optional, probe-supplied descriptive metadata; it is not evidence analysis."""

    kind: str
    duration_us: int | None = None
    stream_count: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("media kind must be a non-empty string")
        for field in ("duration_us", "stream_count"):
            value = getattr(self, field)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"{field} must be a non-negative integer or None")


class MediaMetadataProbe(Protocol):
    """Injected metadata boundary; adapters may wrap ffprobe outside this module."""

    def probe(self, source_path: Path) -> MediaMetadata:
        """Return descriptive metadata for the already-explicit local source path."""


@dataclass(frozen=True, slots=True)
class MediaPublication:
    source_id: str
    rights_id: str
    relative_path: str
    sha256: str
    byte_count: int
    metadata: MediaMetadata | None


class MediaPublicationHook(Protocol):
    """Append source-linked records in the same validated project transaction."""

    def __call__(
        self,
        payload: dict[str, object],
        publication: MediaPublication,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ImportedMedia:
    source_id: str
    rights_id: str
    relative_path: str
    sha256: str
    byte_count: int
    metadata: MediaMetadata | None
    project: ProjectSnapshot


def ingest_media(
    project_root: str | Path,
    source_path: str | Path,
    *,
    rights_id: str | None = None,
    create_restricted_rights: bool = False,
    probe: MediaMetadataProbe | None = None,
    publication_hook: MediaPublicationHook | None = None,
) -> ImportedMedia:
    """Stage one regular local file, then publish evidence under a short lock.

    The caller must either select an existing ``rights_id`` or explicitly opt in
    to a new restricted/local-only rights record.  Media is never uploaded,
    inspected for content, or treated as an automatic annotation here.
    """
    if create_restricted_rights == (rights_id is not None):
        raise MediaIngestError(
            "select an existing rights_id or set create_restricted_rights=True"
        )
    if rights_id is not None and not _valid_id(rights_id):
        raise MediaIngestError("rights_id must be a stable evidence identifier")

    source = Path(source_path)
    metadata: MediaMetadata | None = None

    store = ProjectStore(project_root)
    destination_name: str | None = None
    try:
        store.load()
        with store._open_root() as root_descriptor:
            media_descriptor = _open_private_media_directory(root_descriptor)
            try:
                digest, byte_count, destination_name = _stream_copy(
                    source, media_descriptor
                )
            finally:
                os.close(media_descriptor)
        try:
            relative_path = f"media/{destination_name}"
            if probe is not None:
                metadata = probe.probe(store.root / relative_path)
                if not isinstance(metadata, MediaMetadata):
                    raise MediaIngestError("metadata probe must return MediaMetadata")
            source_id = f"source:media-{digest[:32]}"
            effective_rights_id = rights_id or f"rights:local-{digest[:32]}"
            publication = MediaPublication(
                source_id,
                effective_rights_id,
                relative_path,
                digest,
                byte_count,
                metadata,
            )

            def publish(payload: dict[str, object]) -> None:
                _append_records(
                    payload,
                    source_id=source_id,
                    rights_id=effective_rights_id,
                    relative_path=relative_path,
                    sha256=digest,
                    create_restricted_rights=create_restricted_rights,
                )
                if publication_hook is not None:
                    publication_hook(payload, publication)

            project = store.mutate(publish)
        except Exception:
            with store._open_root() as root_descriptor:
                _unlink_new_media(root_descriptor, destination_name)
            raise
    except (OSError, ProjectStoreError) as exc:
        raise MediaIngestError(str(exc)) from exc

    return ImportedMedia(
        source_id=source_id,
        rights_id=effective_rights_id,
        relative_path=relative_path,
        sha256=digest,
        byte_count=byte_count,
        metadata=metadata,
        project=project,
    )


def _append_records(
    payload: dict[str, object],
    *,
    source_id: str,
    rights_id: str,
    relative_path: str,
    sha256: str,
    create_restricted_rights: bool,
) -> None:
    rights = payload.get("rights")
    sources = payload.get("sources")
    if not isinstance(rights, list) or not isinstance(sources, list):
        raise MediaIngestError("project graph does not contain mutable rights and sources")
    existing_rights = {record.get("id") for record in rights if isinstance(record, dict)}
    existing_sources = {record.get("id") for record in sources if isinstance(record, dict)}
    if source_id in existing_sources:
        raise MediaIngestError("identical media is already recorded in this project")
    if create_restricted_rights:
        if rights_id in existing_rights:
            raise MediaIngestError("restricted rights record already exists")
        rights.append(
            {
                "id": rights_id,
                "access": "restricted",
                "remote_processing": False,
                "model_training": False,
                "retention": "local-project-only",
                "license": "local-restricted",
            }
        )
    elif rights_id not in existing_rights:
        raise MediaIngestError("rights_id does not reference an existing project rights record")
    sources.append(
        {
            "id": source_id,
            "kind": "media",
            "uri": relative_path,
            "sha256": sha256,
            "rights_id": rights_id,
        }
    )


def _open_private_media_directory(root_descriptor: int) -> int:
    descriptor = os.open(
        "media", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_descriptor
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise MediaIngestError("project media directory must be owner-private")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _stream_copy(source_path: Path, media_descriptor: int) -> tuple[str, int, str]:
    source_descriptor = _open_explicit_regular_file(source_path)
    destination_descriptor: int | None = None
    destination_name: str | None = None
    linked_destination = False
    try:
        source_info = os.fstat(source_descriptor)
        if source_info.st_size > MAX_INGEST_BYTES:
            raise MediaIngestError(f"source media exceeds {MAX_INGEST_BYTES} bytes")
        filesystem = os.fstatvfs(media_descriptor)
        available_bytes = filesystem.f_bavail * filesystem.f_frsize
        if source_info.st_size + _INGEST_FREE_SPACE_MARGIN > available_bytes:
            raise MediaIngestError("project storage has insufficient space for media ingest")
        suffix = _safe_suffix(source_path.name)
        # Reserve a stable digest-derived filename only after hash streaming into
        # a private staging file.  The final link operation remains O_EXCL-safe.
        staging_name = (
            f".ingest-{os.getpid()}-{hashlib.sha256(os.urandom(32)).hexdigest()}.tmp"
        )
        destination_descriptor = os.open(
            staging_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            _FILE_MODE,
            dir_fd=media_descriptor,
        )
        os.fchmod(destination_descriptor, _FILE_MODE)
        digest = hashlib.sha256()
        byte_count = 0
        while True:
            chunk = os.read(source_descriptor, _CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            byte_count += len(chunk)
            offset = 0
            while offset < len(chunk):
                offset += os.write(destination_descriptor, chunk[offset:])
        final_source_info = os.fstat(source_descriptor)
        if not _same_source_snapshot(source_info, final_source_info):
            raise MediaIngestError("source file changed while it was being copied")
        if byte_count <= 0:
            raise MediaIngestError("source media must not be empty")
        os.fsync(destination_descriptor)
        os.close(destination_descriptor)
        destination_descriptor = None
        destination_name = f"{digest.hexdigest()}{suffix}"
        try:
            os.link(
                staging_name,
                destination_name,
                src_dir_fd=media_descriptor,
                dst_dir_fd=media_descriptor,
                follow_symlinks=False,
            )
            linked_destination = True
        except FileExistsError as exc:
            raise MediaIngestError(
                "a media file with this stable destination already exists"
            ) from exc
        os.unlink(staging_name, dir_fd=media_descriptor)
        os.fsync(media_descriptor)
        return digest.hexdigest(), byte_count, destination_name
    except BaseException:
        if linked_destination and destination_name is not None:
            try:
                os.unlink(destination_name, dir_fd=media_descriptor)
            except FileNotFoundError:
                pass
        raise
    finally:
        if destination_descriptor is not None:
            os.close(destination_descriptor)
        try:
            os.unlink(staging_name, dir_fd=media_descriptor)
        except (FileNotFoundError, UnboundLocalError):
            pass
        os.close(source_descriptor)


def _open_explicit_regular_file(source: Path) -> int:
    absolute = _trusted_absolute_path(source)
    if absolute == Path(os.path.sep):
        raise MediaIngestError("source path must be a regular file")
    directory = os.open(os.path.sep, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in absolute.parts[1:-1]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory,
            )
            os.close(directory)
            directory = child
        descriptor = os.open(
            absolute.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            os.close(descriptor)
            raise MediaIngestError("source path must be a regular file")
        return descriptor
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOENT, errno.ENOTDIR}:
            raise MediaIngestError("source path must not contain symlinks") from exc
        raise
    finally:
        os.close(directory)


def _same_source_snapshot(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        before.st_dev == after.st_dev
        and before.st_ino == after.st_ino
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
        and before.st_ctime_ns == after.st_ctime_ns
    )


def _unlink_new_media(root_descriptor: int, name: str) -> None:
    descriptor = _open_private_media_directory(root_descriptor)
    try:
        os.unlink(name, dir_fd=descriptor)
        os.fsync(descriptor)
    except FileNotFoundError:
        pass
    finally:
        os.close(descriptor)


def _safe_suffix(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if len(suffix) > 16 or any(
        character not in ".abcdefghijklmnopqrstuvwxyz0123456789" for character in suffix
    ):
        return ""
    return suffix


def _trusted_absolute_path(source: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(source)))
    var_alias = Path("/var")
    private_var = Path("/private/var")
    if absolute == var_alias or var_alias in absolute.parents:
        if var_alias.is_symlink() and Path(os.path.realpath(var_alias)) == private_var:
            return private_var / absolute.relative_to(var_alias)
    return absolute


def _valid_id(value: str) -> bool:
    return bool(value) and value[0].isalpha() and all(
        character.isalnum() or character in "._:-" for character in value
    )
