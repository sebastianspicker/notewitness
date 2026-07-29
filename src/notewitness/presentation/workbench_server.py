"""Dependency-free, loopback-only HTTP runtime for the local workbench."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import sys
import threading
from typing import Any, BinaryIO, Mapping
from urllib.parse import unquote, urlsplit
import webbrowser

from notewitness.application.workbench import (
    WorkbenchError,
    accept_evidence_suggestion,
    accept_relation_suggestion,
    capture_publication_hook,
    create_exact_time_bookmark,
    project_workbench,
    reject_relation_suggestion,
    revise_evidence_annotation,
    set_practice_task_completed,
    resolve_media_source,
)
from notewitness.application.transcript_review_service import add_project_actor
from notewitness.application.workbench_local_executor import LocalWorkbenchExecutor
from notewitness.application.workbench_processing import (
    WorkbenchExecutor,
    WorkbenchProcessingError,
    WorkbenchProcessingService,
)
from notewitness.application.music_export import (
    MusicExportError,
    MusicExportFormat,
    SymbolicMusicExportService,
)
from notewitness.application.transcript_export import (
    TranscriptEvidenceExportService,
    TranscriptEvidenceLayer,
    TranscriptExportError,
)
from notewitness.domain.transcription_options import TranscriptExportFormat
from notewitness.domain.utilities import MetronomePlan, tuner_reading
from notewitness.local_artifacts import LocalArtifactError
from notewitness.media_ingest import (
    MAX_INGEST_BYTES,
    MediaIngestError,
    ingest_media,
)
from notewitness.project_store import (
    ProjectConflictError,
    ProjectStore,
    ProjectStoreError,
)


MAX_JSON_REQUEST_BYTES = 1024 * 1024
MAX_CAPTURE_BYTES = 512 * 1024 * 1024
MAX_REQUEST_PATH_CHARS = 4_096
_STREAM_CHUNK_BYTES = 1024 * 1024
_ALLOWED_BIND_HOST = "127.0.0.1"
_LAUNCH_PATH_PREFIX = "/launch/"
_SESSION_COOKIE_NAME = "notewitness_session"
_CAPTURE_SUFFIXES = {
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}
_IMPORT_SUFFIXES = {
    "audio/aac": ".aac",
    "audio/aiff": ".aiff",
    "audio/flac": ".flac",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/x-aiff": ".aiff",
    "audio/x-caf": ".caf",
    "audio/x-flac": ".flac",
    "audio/x-m4a": ".m4a",
    "audio/x-pn-wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/vnd.wave": ".wav",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}
_IMPORT_NAME_SUFFIXES = frozenset(_IMPORT_SUFFIXES.values())
_JS_TYPE = "text/javascript; charset=utf-8"
_CSS_TYPE = "text/css; charset=utf-8"
_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/assets/app.css": ("app.css", _CSS_TYPE),
    "/assets/app.js": ("app.js", _JS_TYPE),
    "/assets/workbench_ui.mjs": ("workbench_ui.mjs", _JS_TYPE),
    "/assets/pitch_estimator.mjs": ("pitch_estimator.mjs", _JS_TYPE),
    "/assets/notewitness-mark.svg": (
        "notewitness-mark.svg",
        "image/svg+xml",
    ),
    # UI modules
    "/assets/ui/utils.mjs": ("ui/utils.mjs", _JS_TYPE),
    "/assets/ui/shell.mjs": ("ui/shell.mjs", _JS_TYPE),
    "/assets/ui/timeline.mjs": ("ui/timeline.mjs", _JS_TYPE),
    "/assets/ui/panels.mjs": ("ui/panels.mjs", _JS_TYPE),
    "/assets/ui/processing.mjs": ("ui/processing.mjs", _JS_TYPE),
    "/assets/ui/context.mjs": ("ui/context.mjs", _JS_TYPE),
    "/assets/ui/transport.mjs": ("ui/transport.mjs", _JS_TYPE),
    # JS controller modules
    "/assets/js/api.mjs": ("js/api.mjs", _JS_TYPE),
    "/assets/js/playback.mjs": ("js/playback.mjs", _JS_TYPE),
    "/assets/js/processing.mjs": ("js/processing.mjs", _JS_TYPE),
    "/assets/js/actions.mjs": ("js/actions.mjs", _JS_TYPE),
    # CSS modules
    "/assets/styles/tokens.css": ("styles/tokens.css", _CSS_TYPE),
    "/assets/styles/base.css": ("styles/base.css", _CSS_TYPE),
    "/assets/styles/shell.css": ("styles/shell.css", _CSS_TYPE),
    "/assets/styles/timeline.css": ("styles/timeline.css", _CSS_TYPE),
    "/assets/styles/panels.css": ("styles/panels.css", _CSS_TYPE),
    "/assets/styles/forms.css": ("styles/forms.css", _CSS_TYPE),
}
_POST_ERROR_RESPONSES = (
    ((ProjectConflictError,), HTTPStatus.CONFLICT, "project_changed"),
    (
        (WorkbenchError, ValueError, LocalArtifactError, MusicExportError),
        HTTPStatus.UNPROCESSABLE_ENTITY,
        None,
    ),
    (
        (MediaIngestError, ProjectStoreError, WorkbenchProcessingError),
        HTTPStatus.CONFLICT,
        None,
    ),
    ((sqlite3.Error,), HTTPStatus.INTERNAL_SERVER_ERROR, "job_store_failed"),
    ((OSError,), HTTPStatus.INTERNAL_SERVER_ERROR, "local_io_failed"),
)
_POST_ERRORS = (
    ProjectConflictError,
    WorkbenchError,
    ValueError,
    LocalArtifactError,
    MusicExportError,
    MediaIngestError,
    ProjectStoreError,
    WorkbenchProcessingError,
    sqlite3.Error,
    OSError,
)


class WorkbenchServerError(RuntimeError):
    """The local workbench server could not uphold its runtime contract."""


class LocalWorkbenchServer(ThreadingHTTPServer):
    """HTTP server carrying immutable project and origin configuration."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        project_root: str | Path,
        port: int = 0,
        *,
        runtime_config_path: str | Path | None = None,
        processing_executor: WorkbenchExecutor | None = None,
    ) -> None:
        if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65_535:
            raise WorkbenchServerError("port must be an integer between 0 and 65535.")
        self.project_root = ProjectStore(project_root).root
        ProjectStore(self.project_root).load()
        self.csrf_token = secrets.token_urlsafe(32)
        self._launch_token: str | None = secrets.token_urlsafe(32)
        self._launch_token_lock = threading.Lock()
        self._session_token = secrets.token_urlsafe(32)
        self.media_verification_lock = threading.Lock()
        self.media_verifications: dict[str, tuple[tuple[int, ...], str]] = {}
        self.assets_root = Path(__file__).with_name("workbench_assets")
        _require_assets(self.assets_root)
        if runtime_config_path is not None and processing_executor is not None:
            raise WorkbenchServerError(
                "runtime_config_path and processing_executor are mutually exclusive."
            )
        executor = processing_executor
        if runtime_config_path is not None:
            executor = LocalWorkbenchExecutor.from_private_config(
                self.project_root,
                runtime_config_path,
            )
        # ThreadingHTTPServer closes a partially initialized server when bind
        # fails.  Mark processing as already closed until it actually exists so
        # that cleanup preserves the original bind error.
        self._processing_closed = True
        super().__init__((_ALLOWED_BIND_HOST, port), WorkbenchRequestHandler)
        try:
            self.processing = WorkbenchProcessingService(
                self.project_root,
                executor,
            )
        except BaseException:
            super().server_close()
            raise
        self._processing_closed = False

    def server_close(self) -> None:
        if not self._processing_closed:
            self.processing.close()
            # A bounded processing shutdown can fail while its worker still
            # owns the project lock.  Keep this retryable rather than marking
            # the processor closed before it has actually stopped.
            self._processing_closed = True
        super().server_close()

    @property
    def origin(self) -> str:
        return f"http://{_ALLOWED_BIND_HOST}:{self.server_port}"

    @property
    def launch_url(self) -> str:
        with self._launch_token_lock:
            token = self._launch_token
        if token is None:
            raise WorkbenchServerError("workbench launch URL has already been used.")
        return f"{self.origin}{_LAUNCH_PATH_PREFIX}{token}"

    def consume_launch_token(self, token: str) -> bool:
        with self._launch_token_lock:
            expected = self._launch_token
            if expected is None or not secrets.compare_digest(token, expected):
                return False
            self._launch_token = None
            return True

    def session_is_authenticated(self, token: str) -> bool:
        return secrets.compare_digest(token, self._session_token)

    @property
    def session_cookie(self) -> str:
        return (
            f"{_SESSION_COOKIE_NAME}={self._session_token}; "
            "HttpOnly; SameSite=Strict; Path=/"
        )

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return frozenset(
            {
                f"{_ALLOWED_BIND_HOST}:{self.server_port}",
                f"localhost:{self.server_port}",
            }
        )

    @property
    def allowed_origins(self) -> frozenset[str]:
        return frozenset(
            {
                self.origin,
                f"http://localhost:{self.server_port}",
            }
        )


class WorkbenchRequestHandler(BaseHTTPRequestHandler):
    """Small same-origin API; arbitrary paths and filesystem access are absent."""

    protocol_version = "HTTP/1.1"
    server: LocalWorkbenchServer

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._dispatch_get(send_body=True)

    def do_HEAD(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._dispatch_get(send_body=False)

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if not self._request_is_trusted(require_origin=True, require_session=True):
            return
        path = self._request_path()
        if path is None:
            return
        try:
            self._dispatch_post(path)
        except _POST_ERRORS as exc:
            self._post_error(exc)

    def _post_error(self, error: Exception) -> None:
        for error_types, status, code in _POST_ERROR_RESPONSES:
            if isinstance(error, error_types):
                self._json_error(status, str(error) if code is None else code)
                return
        raise AssertionError("Unhandled POST error type.")

    def _dispatch_post(self, path: str) -> None:
        handler = {
            "/api/review/accept": self._accept_review,
            "/api/review/relations/accept": self._accept_relation_review,
            "/api/review/relations/reject": self._reject_relation_review,
            "/api/review/revise": self._revise_annotation,
            "/api/bookmarks": self._create_bookmark,
            "/api/actors": self._create_actor,
            "/api/practice": self._update_practice,
            "/api/tuner": self._tuner,
            "/api/metronome": self._metronome,
            "/api/captures": self._capture,
            "/api/imports": self._import_media,
            "/api/exports/music": self._export_music,
            "/api/exports/transcript": self._export_transcript,
            "/api/jobs": self._enqueue_job,
        }.get(path)
        if handler is not None:
            handler()
        elif path.startswith("/api/jobs/"):
            self._job_action(path)
        else:
            self._json_error(HTTPStatus.NOT_FOUND, "route_not_found")

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        method = self.command if self.command in {"GET", "HEAD", "POST"} else "OTHER"
        route = _coarse_log_route(urlsplit(self.path).path)
        safe_code = str(code) if isinstance(code, int) or str(code).isdigit() else "-"
        safe_size = str(size) if isinstance(size, int) or str(size).lstrip("-").isdigit() else "-"
        sys.stderr.write(
            f"notewitness-workbench: {method} {route} {safe_code} {safe_size}\n"
        )

    def log_message(self, *_args: object) -> None:
        # BaseHTTPRequestHandler error strings may include raw request targets.
        # Request completion is already recorded by the redacted log_request().
        return

    def _dispatch_get(self, *, send_body: bool) -> None:
        if not self._request_is_trusted(require_origin=False, require_session=False):
            return
        path = self._request_path()
        if path is None:
            return
        if self._dispatch_public_get(path, send_body=send_body):
            return
        if not self._session_is_authenticated():
            self._json_error(
                HTTPStatus.UNAUTHORIZED,
                "authentication_required",
                send_body=send_body,
            )
            return
        if self._dispatch_private_get(path, send_body=send_body):
            return
        self._json_error(
            HTTPStatus.NOT_FOUND,
            "route_not_found",
            send_body=send_body,
        )

    def _dispatch_public_get(self, path: str, *, send_body: bool) -> bool:
        if path in _ASSETS:
            self._asset(path, send_body=send_body)
            return True
        if path.startswith(_LAUNCH_PATH_PREFIX):
            self._launch(path, send_body=send_body)
            return True
        return False

    def _dispatch_private_get(self, path: str, *, send_body: bool) -> bool:
        handler = {
            "/api/workbench": self._workbench_snapshot,
            "/api/jobs": self._job_snapshot,
        }.get(path)
        if handler is not None:
            handler(send_body=send_body)
            return True
        prefix = "/api/media/"
        if path.startswith(prefix):
            encoded_source_id = path[len(prefix) :]
            if not encoded_source_id or "/" in encoded_source_id:
                self._json_error(HTTPStatus.NOT_FOUND, "media_not_found")
                return
            try:
                source_id = unquote(encoded_source_id, errors="strict")
                self._media(source_id, send_body=send_body)
            except (UnicodeError, WorkbenchError, ProjectStoreError, OSError):
                self._json_error(HTTPStatus.NOT_FOUND, "media_not_found")
            return True
        return False

    def _workbench_snapshot(self, *, send_body: bool) -> None:
        try:
            payload = project_workbench(str(self.server.project_root))
            payload["csrf_token"] = self.server.csrf_token
            self._json(HTTPStatus.OK, payload, send_body=send_body)
        except (WorkbenchError, ProjectStoreError, ValueError):
            self._json_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "project_projection_failed",
                send_body=send_body,
            )

    def _job_snapshot(self, *, send_body: bool) -> None:
        try:
            self._json(
                HTTPStatus.OK,
                self.server.processing.snapshot(),
                send_body=send_body,
            )
        except (WorkbenchProcessingError, OSError, sqlite3.Error):
            self._json_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "job_store_failed",
                send_body=send_body,
            )

    def _request_is_trusted(
        self,
        *,
        require_origin: bool,
        require_session: bool,
    ) -> bool:
        host = self.headers.get("Host")
        if host not in self.server.allowed_hosts:
            self._json_error(HTTPStatus.MISDIRECTED_REQUEST, "invalid_host")
            return False
        if self.headers.get("Transfer-Encoding") is not None:
            self._json_error(
                HTTPStatus.BAD_REQUEST,
                "transfer_encoding_not_supported",
            )
            return False
        if require_session and not self._session_is_authenticated():
            self._json_error(HTTPStatus.UNAUTHORIZED, "authentication_required")
            return False
        if require_origin:
            origin = self.headers.get("Origin")
            token = self.headers.get("X-NoteWitness-CSRF")
            if origin not in self.server.allowed_origins or not secrets.compare_digest(
                token or "",
                self.server.csrf_token,
            ):
                self._json_error(HTTPStatus.FORBIDDEN, "origin_or_csrf_rejected")
                return False
        return True

    def _session_is_authenticated(self) -> bool:
        raw_cookie = self.headers.get("Cookie", "")
        tokens: list[str] = []
        for component in raw_cookie.split(";"):
            name, separator, value = component.strip().partition("=")
            if separator and name == _SESSION_COOKIE_NAME:
                tokens.append(value)
        return len(tokens) == 1 and self.server.session_is_authenticated(tokens[0])

    def _launch(self, path: str, *, send_body: bool) -> None:
        token = path.removeprefix(_LAUNCH_PATH_PREFIX)
        if not send_body:
            self._json_error(
                HTTPStatus.METHOD_NOT_ALLOWED,
                "launch_requires_get",
                send_body=False,
            )
            return
        if not token or "/" in token or not self.server.consume_launch_token(token):
            self._json_error(
                HTTPStatus.UNAUTHORIZED,
                "launch_expired_or_invalid",
            )
            return
        self.send_response(HTTPStatus.SEE_OTHER)
        self._security_headers()
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", self.server.session_cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _request_path(self) -> str | None:
        if len(self.path) > MAX_REQUEST_PATH_CHARS:
            self._json_error(HTTPStatus.REQUEST_URI_TOO_LONG, "request_uri_too_long")
            return None
        parsed = urlsplit(self.path)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            self._json_error(HTTPStatus.BAD_REQUEST, "invalid_request_target")
            return None
        return parsed.path

    def _asset(self, path: str, *, send_body: bool) -> None:
        filename, content_type = _ASSETS[path]
        try:
            body = (self.server.assets_root / filename).read_bytes()
        except OSError:
            self._json_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "asset_unavailable",
                send_body=send_body,
            )
            return
        self._bytes(
            HTTPStatus.OK,
            body,
            content_type,
            send_body=send_body,
        )

    def _media(self, source_id: str, *, send_body: bool) -> None:
        _, source, relative = resolve_media_source(
            str(self.server.project_root),
            source_id,
        )
        if len(relative.parts) != 2 or relative.parts[0] != "media":
            raise WorkbenchError("Media path is not an ingested source.")
        directory_descriptor = os.open(
            self.server.project_root / "media",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        descriptor: int | None = None
        try:
            descriptor = os.open(
                relative.parts[1],
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=directory_descriptor,
            )
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise WorkbenchError("Media must be an owner-private regular file.")
            _require_verified_media(
                self.server,
                source_id,
                descriptor,
                metadata,
                source.get("sha256"),
            )
            size = metadata.st_size
            selected = _parse_range(self.headers.get("Range"), size)
            if selected is None:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self._security_headers()
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start, end, partial = selected
            length = end - start + 1
            content_type = (
                mimetypes.guess_type(str(source["uri"]))[0]
                or "application/octet-stream"
            )
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

    def _accept_review(self) -> None:
        request = self._json_request()
        result = accept_evidence_suggestion(
            str(self.server.project_root),
            event_id=_required_string(request, "event_id"),
            author_id=_required_string(request, "author_id"),
            actor_id=_required_string(request, "actor_id"),
            reason=_required_string(request, "reason"),
            expected_sha256=_required_string(request, "project_sha256"),
            replacement_text=_optional_string(request, "replacement_text"),
        )
        self._json(HTTPStatus.CREATED, asdict(result))

    def _accept_relation_review(self) -> None:
        request = self._json_request()
        result = accept_relation_suggestion(
            str(self.server.project_root),
            relation_id=_required_string(request, "relation_id"),
            author_id=_required_string(request, "author_id"),
            reason=_required_string(request, "reason"),
            expected_sha256=_required_string(request, "project_sha256"),
        )
        self._json(HTTPStatus.CREATED, asdict(result))

    def _reject_relation_review(self) -> None:
        request = self._json_request()
        result = reject_relation_suggestion(
            str(self.server.project_root),
            relation_id=_required_string(request, "relation_id"),
            author_id=_required_string(request, "author_id"),
            reason=_required_string(request, "reason"),
            expected_sha256=_required_string(request, "project_sha256"),
        )
        self._json(HTTPStatus.CREATED, asdict(result))

    def _create_bookmark(self) -> None:
        request = self._json_request()
        result = create_exact_time_bookmark(
            str(self.server.project_root),
            source_id=_required_string(request, "source_id"),
            start_us=_required_integer(request, "start_us"),
            duration_us=_required_integer(request, "duration_us"),
            label=_required_string(request, "label"),
            author_id=_required_string(request, "author_id"),
            expected_sha256=_required_string(request, "project_sha256"),
        )
        self._json(HTTPStatus.CREATED, asdict(result))

    def _create_actor(self) -> None:
        request = self._json_request()
        snapshot = add_project_actor(
            str(self.server.project_root),
            actor_id=_required_string(request, "actor_id"),
            role=_required_string(request, "role"),
            visibility="restricted",
            expected_sha256=_required_string(request, "project_sha256"),
        )
        self._json(HTTPStatus.CREATED, {"project_sha256": snapshot.sha256})

    def _revise_annotation(self) -> None:
        request = self._json_request()
        result = revise_evidence_annotation(
            str(self.server.project_root),
            event_id=_required_string(request, "event_id"),
            author_id=_required_string(request, "author_id"),
            actor_id=_required_string(request, "actor_id"),
            reason=_required_string(request, "reason"),
            replacement_text=_required_string(request, "replacement_text"),
            expected_sha256=_required_string(request, "project_sha256"),
        )
        self._json(HTTPStatus.CREATED, asdict(result))

    def _update_practice(self) -> None:
        request = self._json_request()
        completed = request.get("completed")
        if not isinstance(completed, bool):
            raise WorkbenchError("completed must be a boolean.")
        result = set_practice_task_completed(
            str(self.server.project_root),
            task_id=_required_string(request, "task_id"),
            completed=completed,
            author_id=_required_string(request, "author_id"),
            expected_sha256=_required_string(request, "project_sha256"),
        )
        self._json(HTTPStatus.CREATED, asdict(result))

    def _tuner(self) -> None:
        request = self._json_request()
        frequency_hz = _required_number(request, "frequency_hz")
        a4_hz = _optional_number(request, "a4_hz", default=440.0)
        self._json(
            HTTPStatus.OK,
            asdict(tuner_reading(frequency_hz, a4_hz=a4_hz)),
        )

    def _metronome(self) -> None:
        request = self._json_request()
        plan = MetronomePlan(
            bpm=_required_number(request, "bpm"),
            beats_per_bar=_required_integer(request, "beats_per_bar"),
            subdivisions_per_beat=_required_integer(request, "subdivisions"),
        )
        ticks = plan.schedule(_required_integer(request, "bars"))
        self._json(
            HTTPStatus.OK,
            {
                "beats_per_bar": plan.beats_per_bar,
                "bpm": plan.bpm,
                "subdivisions_per_beat": plan.subdivisions_per_beat,
                "ticks": [asdict(tick) for tick in ticks],
            },
        )

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
        publication_hook = capture_publication_hook(
            author_id=author_id,
            capture_name=capture_name,
            content_type=content_type,
            started_at=started_at,
            duration_ms=duration_ms,
        )
        runs = ProjectStore(self.server.project_root).ensure_private_directory("runs")
        staging = runs / f"capture-{secrets.token_hex(16)}{suffix}"
        descriptor = os.open(
            staging,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            _stream_request(self.rfile, descriptor, length)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            _validate_capture_container(staging, content_type)
            imported = ingest_media(
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
        runs = ProjectStore(self.server.project_root).ensure_private_directory("runs")
        staging = runs / f"import-{secrets.token_hex(16)}{suffix}"
        descriptor = os.open(
            staging,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            _stream_request(self.rfile, descriptor, length)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            _validate_import_container(staging, suffix)
            probe_factory = getattr(self.server.processing.executor, "ingest_probe", None)
            probe = probe_factory() if callable(probe_factory) else None
            imported = ingest_media(
                self.server.project_root,
                staging,
                create_restricted_rights=True,
                probe=probe,
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

    def _enqueue_job(self) -> None:
        payload = self._json_request()
        job = self.server.processing.enqueue(
            _required_string(payload, "kind"),
            _required_string(payload, "source_id"),
        )
        self._json(HTTPStatus.ACCEPTED, job.as_public_dict())

    def _export_music(self) -> None:
        payload = self._json_request()
        expected = {
            "acknowledge_export_losses",
            "authorize_local_export",
            "filename",
            "format",
            "source_id",
        }
        if set(payload) != expected:
            raise WorkbenchError("Music export request has unknown or missing fields.")
        authorized = payload.get("authorize_local_export")
        acknowledged = payload.get("acknowledge_export_losses")
        if not isinstance(authorized, bool) or not isinstance(acknowledged, bool):
            raise WorkbenchError("Music export decisions must be booleans.")
        result = SymbolicMusicExportService.for_project(
            self.server.project_root
        ).export(
            export_format=MusicExportFormat(
                _required_string(payload, "format")
            ),
            filename=_required_string(payload, "filename"),
            rights_authorized=authorized,
            loss_preview_acknowledged=acknowledged,
            source_id=_required_string(payload, "source_id"),
        )
        self._json(
            HTTPStatus.CREATED,
            {
                "checksum_sha256": result.checksum_sha256,
                "documented_losses": [
                    asdict(loss) for loss in result.documented_losses
                ],
                "filename": Path(result.path).name,
                "format": result.export_format.value,
                "network_used": False,
                "record_count": result.record_count,
                "source_ids": list(result.source_ids),
            },
        )

    def _export_transcript(self) -> None:
        payload = self._json_request()
        expected = {
            "acknowledge_export_losses", "authorize_local_export", "evidence_layer",
            "filename", "format", "pause_threshold_ms", "source_id",
            "timestamp_interval_ms", "visible_timestamps",
        }
        if set(payload) != expected:
            raise WorkbenchError("Transcript export request has unknown or missing fields.")
        authorized = payload.get("authorize_local_export")
        acknowledged = payload.get("acknowledge_export_losses")
        visible = payload.get("visible_timestamps")
        interval = payload.get("timestamp_interval_ms")
        pause = payload.get("pause_threshold_ms")
        if not all(isinstance(value, bool) for value in (authorized, acknowledged, visible)):
            raise WorkbenchError("Transcript export decisions must be booleans.")
        if not isinstance(interval, int) or isinstance(interval, bool):
            raise WorkbenchError("timestamp_interval_ms must be an integer.")
        if pause is not None and (not isinstance(pause, int) or isinstance(pause, bool)):
            raise WorkbenchError("pause_threshold_ms must be an integer or null.")
        try:
            result = TranscriptEvidenceExportService.for_project(self.server.project_root).export(
                export_format=TranscriptExportFormat(_required_string(payload, "format")),
                filename=_required_string(payload, "filename"),
                source_id=_required_string(payload, "source_id"),
                evidence_layer=TranscriptEvidenceLayer(_required_string(payload, "evidence_layer")),
                rights_authorized=authorized, loss_preview_acknowledged=acknowledged,
                visible_timestamps=visible, timestamp_interval_ms=interval,
                pause_threshold_ms=pause,
            )
        except (TranscriptExportError, ValueError) as exc:
            raise WorkbenchError(str(exc)) from exc
        self._json(HTTPStatus.CREATED, {
            "checksum_sha256": result.checksum_sha256,
            "documented_losses": [asdict(loss) for loss in result.documented_losses],
            "evidence_layer": result.evidence_layer.value,
            "filename": Path(result.path).name,
            "format": result.export_format.value,
            "network_used": False,
            "record_count": result.record_count,
            "source_id": result.source_id,
        })

    def _job_action(self, path: str) -> None:
        parts = path.split("/")
        if len(parts) != 5 or parts[:3] != ["", "api", "jobs"]:
            self._json_error(HTTPStatus.NOT_FOUND, "route_not_found")
            return
        try:
            job_id = unquote(parts[3], errors="strict")
        except UnicodeError:
            self._json_error(HTTPStatus.NOT_FOUND, "job_not_found")
            return
        self._json_request()
        if parts[4] == "cancel":
            job = self.server.processing.cancel(job_id)
        elif parts[4] == "retry":
            job = self.server.processing.retry(job_id)
        else:
            self._json_error(HTTPStatus.NOT_FOUND, "route_not_found")
            return
        self._json(HTTPStatus.ACCEPTED, job.as_public_dict())

    def _json_request(self) -> Mapping[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type != "application/json":
            raise WorkbenchError("JSON endpoints require application/json.")
        length = self._content_length(MAX_JSON_REQUEST_BYTES)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise WorkbenchError("Request body ended before Content-Length.")
        try:
            payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkbenchError("Request body is not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise WorkbenchError("Request JSON must be an object.")
        return payload

    def _content_length(self, maximum: int) -> int:
        raw = self.headers.get("Content-Length")
        try:
            length = int(raw) if raw is not None else -1
        except ValueError as exc:
            raise WorkbenchError("Content-Length is invalid.") from exc
        if not 0 < length <= maximum:
            raise WorkbenchError(f"Content-Length must be between 1 and {maximum}.")
        return length

    def _json(
        self,
        status: HTTPStatus,
        payload: Mapping[str, Any],
        *,
        send_body: bool = True,
    ) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        self._bytes(
            status,
            body,
            "application/json; charset=utf-8",
            send_body=send_body,
        )

    def _json_error(
        self,
        status: HTTPStatus,
        code: str,
        *,
        send_body: bool = True,
    ) -> None:
        self.close_connection = True
        self._json(status, {"error": code}, send_body=send_body)

    def _bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        *,
        send_body: bool,
    ) -> None:
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'",
        )
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "microphone=(self), camera=()")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")


def serve_workbench(
    project_root: str | Path,
    *,
    port: int = 0,
    open_browser: bool = True,
    runtime_config_path: str | Path | None = None,
) -> None:
    """Serve one project until interrupted, never binding beyond loopback."""

    server = LocalWorkbenchServer(
        project_root,
        port,
        runtime_config_path=runtime_config_path,
    )
    url = f"{server.origin}/"
    if open_browser:
        launch_url = server.launch_url
        if webbrowser.open(launch_url, new=2, autoraise=True):
            print(json.dumps({"network_mode": "loopback_only", "url": url}))
        else:
            print(
                json.dumps(
                    {
                        "launch_url": launch_url,
                        "network_mode": "loopback_only",
                        "notice": "Browser launch failed; this single-use URL grants access to the private workbench.",
                    }
                )
            )
    else:
        print(
            json.dumps(
                {
                    "launch_url": server.launch_url,
                    "network_mode": "loopback_only",
                    "notice": "This single-use URL grants access to the private workbench.",
                }
            )
        )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _require_assets(root: Path) -> None:
    missing = [filename for filename, _ in _ASSETS.values() if not (root / filename).is_file()]
    if missing:
        raise WorkbenchServerError(
            f"Workbench assets are missing: {', '.join(sorted(missing))}."
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
    server: LocalWorkbenchServer,
    source_id: str,
    descriptor: int,
    metadata: os.stat_result,
    expected_sha256: object,
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
            chunk = os.pread(
                descriptor,
                min(_STREAM_CHUNK_BYTES, metadata.st_size - offset),
                offset,
            )
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
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _validate_capture_container(path: Path, content_type: str) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
            header = os.read(descriptor, min(64, metadata.st_size))
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise WorkbenchError("Capture could not be validated safely.") from exc
    valid = {
        "audio/ogg": header.startswith(b"OggS"),
        "audio/wav": len(header) >= 12
        and header.startswith(b"RIFF")
        and header[8:12] == b"WAVE",
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
        frozenset({".aif", ".aiff"}),
        frozenset({".m4a", ".mp4"}),
        frozenset({".wav"}),
        frozenset({".webm"}),
        frozenset({".ogg"}),
        frozenset({".flac"}),
        frozenset({".mp3"}),
        frozenset({".aac"}),
        frozenset({".caf"}),
    )
    return any(declared in family and named in family for family in families)


def _validate_import_container(path: Path, suffix: str) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
            header = os.read(descriptor, min(64, metadata.st_size))
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise WorkbenchError("Imported media could not be validated safely.") from exc
    checks = {
        ".aac": len(header) >= 2 and header[0] == 0xFF and header[1] & 0xF6 == 0xF0,
        ".aiff": len(header) >= 12 and header.startswith(b"FORM")
        and header[8:12] in {b"AIFF", b"AIFC"},
        ".caf": header.startswith(b"caff"),
        ".flac": header.startswith(b"fLaC"),
        ".m4a": _is_mp4_header(header),
        ".mp4": _is_mp4_header(header),
        ".mp3": header.startswith(b"ID3")
        or (len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0),
        ".ogg": header.startswith(b"OggS"),
        ".wav": len(header) >= 12 and header.startswith(b"RIFF")
        and header[8:12] == b"WAVE",
        ".webm": header.startswith(b"\x1a\x45\xdf\xa3"),
    }
    if not checks.get(suffix, False):
        raise WorkbenchError("Imported bytes do not match the selected media container.")


def _is_mp4_header(header: bytes) -> bool:
    if len(header) < 12 or header[4:8] != b"ftyp":
        return False
    box_size = int.from_bytes(header[:4], "big")
    return box_size in {0, 1} or 8 <= box_size <= len(header)


def _coarse_log_route(path: str) -> str:
    if path.startswith(_LAUNCH_PATH_PREFIX):
        return "/launch/:token"
    if path.startswith("/api/media/"):
        return "/api/media/:source"
    if path.startswith("/api/jobs/"):
        return "/api/jobs/:job/:action"
    if path.startswith("/assets/"):
        return "/assets/:asset"
    if path in {
        "/",
        "/api/bookmarks",
        "/api/actors",
        "/api/captures",
        "/api/exports/music",
        "/api/exports/transcript",
        "/api/imports",
        "/api/jobs",
        "/api/metronome",
        "/api/practice",
        "/api/review/accept",
        "/api/review/relations/accept",
        "/api/review/relations/reject",
        "/api/review/revise",
        "/api/tuner",
        "/api/workbench",
    }:
        return path
    return "/:unknown"


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkbenchError(f"Duplicate JSON key: {key}.")
        result[key] = value
    return result


def _required_string(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise WorkbenchError(f"{name} must be a non-empty string.")
    return value


def _optional_string(payload: Mapping[str, Any], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise WorkbenchError(f"{name} must be a string or null.")
    return value


def _required_integer(payload: Mapping[str, Any], name: str) -> int:
    value = payload.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise WorkbenchError(f"{name} must be an integer.")
    return value


def _required_number(payload: Mapping[str, Any], name: str) -> float:
    value = payload.get(name)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise WorkbenchError(f"{name} must be a number.")
    return float(value)


def _optional_number(
    payload: Mapping[str, Any],
    name: str,
    *,
    default: float,
) -> float:
    value = payload.get(name, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise WorkbenchError(f"{name} must be a number.")
    return float(value)


def _required_header(headers: Mapping[str, str], name: str, maximum: int) -> str:
    value = headers.get(name)
    if value is None or not value.strip() or len(value) > maximum:
        raise WorkbenchError(f"{name} must be bounded non-empty text.")
    return value.strip()
