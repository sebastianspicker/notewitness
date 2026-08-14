"""Safe local-media HTTP handlers and container validation for the workbench."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
from http import HTTPStatus
import mimetypes
import os
from pathlib import Path
import secrets
import stat
from typing import Any, BinaryIO
from urllib.parse import unquote

from notewitness.application.workbench import WorkbenchError
from notewitness.media_ingest import MAX_INGEST_BYTES

from .workbench_protocol import _required_header


MAX_CAPTURE_BYTES = 512 * 1024 * 1024
_STREAM_CHUNK_BYTES = 1024 * 1024
_CAPTURE_SUFFIXES = {
    "audio/mp4": ".m4a", "audio/ogg": ".ogg", "audio/wav": ".wav",
    "audio/webm": ".webm", "video/mp4": ".mp4", "video/webm": ".webm",
}
_IMPORT_SUFFIXES = {
    "audio/aac": ".aac", "audio/aiff": ".aiff", "audio/flac": ".flac",
    "audio/mp3": ".mp3", "audio/mp4": ".m4a", "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg", "audio/wav": ".wav", "audio/webm": ".webm",
    "audio/x-aiff": ".aiff", "audio/x-caf": ".caf", "audio/x-flac": ".flac",
    "audio/x-m4a": ".m4a", "audio/x-pn-wav": ".wav", "audio/x-wav": ".wav",
    "audio/vnd.wave": ".wav", "video/mp4": ".mp4", "video/webm": ".webm",
}
_IMPORT_NAME_SUFFIXES = frozenset(_IMPORT_SUFFIXES.values())


def _legacy(name: str) -> Any:
    """Resolve façade symbols so legacy server-module monkeypatching survives."""

    from . import workbench_server

    return getattr(workbench_server, name)


class WorkbenchMediaMixin:
    """Handlers that open, stream, and ingest private local media safely."""

    def _media(self, source_id: str, *, send_body: bool) -> None:
        _, source, relative = _legacy("resolve_media_source")(
            str(self.server.project_root), source_id
        )
        if len(relative.parts) != 2 or relative.parts[0] != "media":
            raise WorkbenchError("Media path is not an ingested source.")
        directory_descriptor = os.open(
            self.server.project_root / "media", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        descriptor: int | None = None
        try:
            descriptor = os.open(
                relative.parts[1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_descriptor
            )
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise WorkbenchError("Media must be an owner-private regular file.")
            _legacy("_require_verified_media")(
                self.server, source_id, descriptor, metadata, source.get("sha256")
            )
            size = metadata.st_size
            selected = _legacy("_parse_range")(self.headers.get("Range"), size)
            if selected is None:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self._security_headers()
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start, end, partial = selected
            length = end - start + 1
            content_type = mimetypes.guess_type(str(source["uri"]))[0] or "application/octet-stream"
            self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
            self._security_headers()
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if send_body:
                os.lseek(descriptor, start, os.SEEK_SET)
                remaining = length
                while remaining:
                    chunk = os.read(descriptor, min(_STREAM_CHUNK_BYTES, remaining))
                    if not chunk:
                        raise WorkbenchServerError("Media changed during playback.")
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(directory_descriptor)

    def _capture(self) -> None:
        length = self._content_length(MAX_CAPTURE_BYTES)
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        suffix = _CAPTURE_SUFFIXES.get(content_type)
        if suffix is None:
            raise WorkbenchError("Capture media type is unsupported.")
        capture_name = self.headers.get("X-Capture-Name", "Browser recording")
        if not capture_name.strip() or len(capture_name) > 255:
            raise WorkbenchError("Capture name must be bounded non-empty text.")
        author_id = _required_header(self.headers, "X-Capture-Author", 256)
        started_at = _required_header(self.headers, "X-Capture-Started-At", 64)
        duration_raw = _required_header(self.headers, "X-Capture-Duration-Ms", 16)
        try:
            duration_ms = int(duration_raw)
        except ValueError as exc:
            raise WorkbenchError("Capture duration must be an integer.") from exc
        publication_hook = _legacy("capture_publication_hook")(
            author_id=author_id,
            capture_name=capture_name,
            content_type=content_type,
            started_at=started_at,
            duration_ms=duration_ms,
        )
        runs = _legacy("ProjectStore")(self.server.project_root).ensure_private_directory("runs")
        staging = runs / f"capture-{secrets.token_hex(16)}{suffix}"
        descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            _legacy("_stream_request")(self.rfile, descriptor, length)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            _legacy("_validate_capture_container")(staging, content_type)
            imported = _legacy("ingest_media")(
                self.server.project_root,
                staging,
                create_restricted_rights=True,
                publication_hook=publication_hook,
            )
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass
        self._json(
            HTTPStatus.CREATED,
            {
                "byte_count": imported.byte_count,
                "name": capture_name.strip(),
                "network_used": False,
                "project_sha256": imported.project.sha256,
                "relative_path": imported.relative_path,
                "sha256": imported.sha256,
                "source_id": imported.source_id,
            },
        )

    def _import_media(self) -> None:
        length = self._content_length(MAX_INGEST_BYTES)
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        encoded_name = _required_header(self.headers, "X-Media-Name", 768)
        try:
            media_name = unquote(encoded_name, errors="strict")
        except (UnicodeError, ValueError) as exc:
            raise WorkbenchError("Imported media name is invalid.") from exc
        suffix = _safe_import_suffix(content_type, media_name)
        runs = _legacy("ProjectStore")(self.server.project_root).ensure_private_directory("runs")
        staging = runs / f"import-{secrets.token_hex(16)}{suffix}"
        descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            _legacy("_stream_request")(self.rfile, descriptor, length)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            _legacy("_validate_import_container")(staging, suffix)
            probe_factory = getattr(self.server.processing.executor, "ingest_probe", None)
            probe = probe_factory() if callable(probe_factory) else None
            imported = _legacy("ingest_media")(
                self.server.project_root, staging, create_restricted_rights=True, probe=probe
            )
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass
        self._json(
            HTTPStatus.CREATED,
            {
                "byte_count": imported.byte_count,
                "metadata": asdict(imported.metadata) if imported.metadata else None,
                "name": media_name,
                "network_used": False,
                "project_sha256": imported.project.sha256,
                "sha256": imported.sha256,
                "source_id": imported.source_id,
            },
        )


def _parse_range(value: str | None, size: int) -> tuple[int, int, bool] | None:
    if size <= 0:
        return None
    if value is None:
        return 0, size - 1, False
    if not value.startswith("bytes=") or "," in value:
        return None
    raw_start, separator, raw_end = value[6:].partition("-")
    if not separator:
        return None
    try:
        if raw_start:
            start = int(raw_start)
            end = int(raw_end) if raw_end else size - 1
            if start < 0 or end < start or start >= size:
                return None
            return start, min(end, size - 1), True
        suffix = int(raw_end)
        if suffix <= 0:
            return None
        return max(0, size - suffix), size - 1, True
    except ValueError:
        return None


def _stream_request(source: BinaryIO, descriptor: int, length: int) -> None:
    remaining = length
    while remaining:
        chunk = source.read(min(_STREAM_CHUNK_BYTES, remaining))
        if not chunk:
            raise WorkbenchError("Capture ended before Content-Length.")
        offset = 0
        while offset < len(chunk):
            offset += os.write(descriptor, chunk[offset:])
        remaining -= len(chunk)


def _require_verified_media(
    server: Any, source_id: str, descriptor: int, metadata: os.stat_result, expected_sha256: object
) -> None:
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise WorkbenchError("Media source checksum is invalid.")
    identity = _stat_identity(metadata)
    with server.media_verification_lock:
        if server.media_verifications.get(source_id) == (identity, expected_sha256):
            return
        digest = hashlib.sha256()
        offset = 0
        while offset < metadata.st_size:
            chunk = os.pread(descriptor, min(_STREAM_CHUNK_BYTES, metadata.st_size - offset), offset)
            if not chunk:
                raise WorkbenchError("Media changed during checksum verification.")
            digest.update(chunk)
            offset += len(chunk)
        if _stat_identity(os.fstat(descriptor)) != identity:
            raise WorkbenchError("Media changed during checksum verification.")
        if digest.hexdigest() != expected_sha256:
            raise WorkbenchError("Media checksum no longer matches its source record.")
        server.media_verifications[source_id] = (identity, expected_sha256)


def _stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns
    )


def _validate_capture_container(path: Path, content_type: str) -> None:
    header = _read_header(path, "Capture could not be validated safely.")
    valid = {
        "audio/ogg": header.startswith(b"OggS"),
        "audio/wav": len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WAVE",
        "audio/webm": header.startswith(b"\x1a\x45\xdf\xa3"),
        "video/webm": header.startswith(b"\x1a\x45\xdf\xa3"),
        "audio/mp4": _is_mp4_header(header),
        "video/mp4": _is_mp4_header(header),
    }.get(content_type, False)
    if not valid:
        raise WorkbenchError("Capture bytes do not match the declared media container.")


def _safe_import_suffix(content_type: str, media_name: str) -> str:
    if (
        not media_name.strip()
        or len(media_name) > 255
        or any(character in media_name for character in ("/", "\\", "\x00", "\r", "\n"))
    ):
        raise WorkbenchError("Imported media name must be bounded plain text.")
    suffix = _IMPORT_SUFFIXES.get(content_type)
    name_suffix = Path(media_name).suffix.lower()
    if suffix is None and content_type == "application/octet-stream":
        suffix = name_suffix if name_suffix in _IMPORT_NAME_SUFFIXES else None
    if suffix is None:
        raise WorkbenchError("Imported media type is unsupported.")
    if name_suffix in _IMPORT_NAME_SUFFIXES and not _compatible_suffixes(suffix, name_suffix):
        raise WorkbenchError("Imported media name and declared type disagree.")
    return suffix


def _compatible_suffixes(declared: str, named: str) -> bool:
    families = (
        frozenset({".aif", ".aiff"}), frozenset({".m4a", ".mp4"}), frozenset({".wav"}),
        frozenset({".webm"}), frozenset({".ogg"}), frozenset({".flac"}),
        frozenset({".mp3"}), frozenset({".aac"}), frozenset({".caf"}),
    )
    return any(declared in family and named in family for family in families)


def _validate_import_container(path: Path, suffix: str) -> None:
    header = _read_header(path, "Imported media could not be validated safely.")
    checks = {
        ".aac": len(header) >= 2 and header[0] == 0xFF and header[1] & 0xF6 == 0xF0,
        ".aiff": len(header) >= 12 and header.startswith(b"FORM") and header[8:12] in {b"AIFF", b"AIFC"},
        ".caf": header.startswith(b"caff"), ".flac": header.startswith(b"fLaC"),
        ".m4a": _is_mp4_header(header), ".mp4": _is_mp4_header(header),
        ".mp3": header.startswith(b"ID3") or (len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0),
        ".ogg": header.startswith(b"OggS"),
        ".wav": len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WAVE",
        ".webm": header.startswith(b"\x1a\x45\xdf\xa3"),
    }
    if not checks.get(suffix, False):
        raise WorkbenchError("Imported bytes do not match the selected media container.")


def _read_header(path: Path, error: str) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
            return os.read(descriptor, min(64, metadata.st_size))
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise WorkbenchError(error) from exc


def _is_mp4_header(header: bytes) -> bool:
    if len(header) < 12 or header[4:8] != b"ftyp":
        return False
    box_size = int.from_bytes(header[:4], "big")
    return box_size in {0, 1} or 8 <= box_size <= len(header)


from .workbench_protocol import WorkbenchServerError  # noqa: E402
