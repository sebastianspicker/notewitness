"""Loopback server lifecycle and request routing for the workbench."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import secrets
import socket
import sqlite3
import sys
import threading
from pathlib import Path
from urllib.parse import unquote, urlsplit

from notewitness.lessons.actors import TranscriptReviewError
from notewitness.lessons.review_rules import ReviewError
from notewitness.lessons.music_export import MusicExportError
from notewitness.projects.artifacts import LocalArtifactError, TranscriptPublicationError
from notewitness.projects.media import MediaIngestError
from notewitness.projects.store import ProjectConflictError, ProjectStore, ProjectStoreError

from .api import WorkbenchApiMixin
from .executor import LocalWorkbenchExecutor
from .jobs import WorkbenchExecutor, WorkbenchProcessingError
from .media import WorkbenchMediaMixin
from .protocol import (
    RequestError,
    ALLOWED_BIND_HOST,
    ASSETS,
    LAUNCH_PATH_PREFIX,
    SESSION_HEADER_NAME,
    WorkbenchProtocolMixin,
    WorkbenchServerError,
    coarse_log_route,
)
from .processing import WorkbenchProcessingService


_POST_ERROR_RESPONSES = (
    ((ProjectConflictError,), HTTPStatus.CONFLICT, "project_changed"),
    (
        (
            RequestError,
            ReviewError,
            TranscriptReviewError,
            ValueError,
            LocalArtifactError,
            TranscriptPublicationError,
            MusicExportError,
        ),
        HTTPStatus.UNPROCESSABLE_ENTITY,
        None,
    ),
    ((MediaIngestError, ProjectStoreError, WorkbenchProcessingError), HTTPStatus.CONFLICT, None),
    ((sqlite3.Error,), HTTPStatus.INTERNAL_SERVER_ERROR, "job_store_failed"),
    ((OSError,), HTTPStatus.INTERNAL_SERVER_ERROR, "local_io_failed"),
)
_POST_ERRORS = tuple(error for types, _status, _code in _POST_ERROR_RESPONSES for error in types)


class WorkbenchRequestHandler(
    WorkbenchApiMixin, WorkbenchMediaMixin, WorkbenchProtocolMixin, BaseHTTPRequestHandler
):
    """Small same-origin API; arbitrary paths and filesystem access are absent."""

    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(self.server.request_io_timeout_seconds)
        self._header_deadline_lock = threading.Lock()
        self._header_deadline_generation = 0
        self._header_deadline: threading.Timer | None = None
        self._request_headers_expired = False

    def handle_one_request(self) -> None:
        self._arm_header_deadline()
        try:
            super().handle_one_request()
        finally:
            self._cancel_header_deadline()

    def finish(self) -> None:
        self._cancel_header_deadline()
        super().finish()

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if not self._request_headers_complete():
            return
        self._dispatch_get(send_body=True)

    def do_HEAD(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if not self._request_headers_complete():
            return
        self._dispatch_get(send_body=False)

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if not self._request_headers_complete():
            return
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
            "/api/review/reject": self._reject_review,
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
        route = coarse_log_route(urlsplit(self.path).path)
        safe_code = str(code) if isinstance(code, int) or str(code).isdigit() else "-"
        safe_size = str(size) if isinstance(size, int) or str(size).lstrip("-").isdigit() else "-"
        sys.stderr.write(f"notewitness-workbench: {method} {route} {safe_code} {safe_size}\n")

    def log_message(self, *_args: object) -> None:
        return

    def _dispatch_get(self, *, send_body: bool) -> None:
        if not self._request_is_trusted(require_origin=False, require_session=False):
            return
        path = self._request_path()
        if path is None:
            return
        if self._dispatch_public_get(path, send_body=send_body):
            return
        if path.startswith("/api/media/"):
            self._dispatch_media_get(path, send_body=send_body)
            return
        if not self._session_is_authenticated():
            self._json_error(HTTPStatus.UNAUTHORIZED, "authentication_required", send_body=send_body)
            return
        if self._dispatch_private_get(path, send_body=send_body):
            return
        self._json_error(HTTPStatus.NOT_FOUND, "route_not_found", send_body=send_body)

    def _dispatch_public_get(self, path: str, *, send_body: bool) -> bool:
        if path in ASSETS:
            self._asset(path, send_body=send_body)
            return True
        if path.startswith(LAUNCH_PATH_PREFIX):
            self._launch(path, send_body=send_body)
            return True
        return False

    def _dispatch_private_get(self, path: str, *, send_body: bool) -> bool:
        handler = {"/api/workbench": self._workbench_snapshot, "/api/jobs": self._job_snapshot}.get(path)
        if handler is not None:
            handler(send_body=send_body)
            return True
        return False

    def _dispatch_media_get(self, path: str, *, send_body: bool) -> None:
        components = path.removeprefix("/api/media/").split("/")
        if len(components) not in {1, 2} or not all(components):
            self._json_error(HTTPStatus.NOT_FOUND, "media_not_found", send_body=send_body)
            return
        try:
            source_id = unquote(components[-1], errors="strict")
        except (UnicodeError, ValueError):
            self._json_error(HTTPStatus.NOT_FOUND, "media_not_found", send_body=send_body)
            return
        authenticated = (
            self._session_is_authenticated()
            if len(components) == 1
            else self.server.media_capability_is_authenticated(source_id, components[0])
        )
        if not authenticated:
            self._json_error(
                HTTPStatus.UNAUTHORIZED, "authentication_required", send_body=send_body
            )
            return
        try:
            self._media(source_id, send_body=send_body)
        except (RequestError, ReviewError, ProjectStoreError, OSError):
            self._json_error(HTTPStatus.NOT_FOUND, "media_not_found", send_body=send_body)

    def _request_is_trusted(self, *, require_origin: bool, require_session: bool) -> bool:
        if self.headers.get("Host") not in self.server.allowed_hosts:
            self._json_error(HTTPStatus.MISDIRECTED_REQUEST, "invalid_host")
            return False
        if self.headers.get("Transfer-Encoding") is not None:
            self._json_error(HTTPStatus.BAD_REQUEST, "transfer_encoding_not_supported")
            return False
        if require_session and not self._session_is_authenticated():
            self._json_error(HTTPStatus.UNAUTHORIZED, "authentication_required")
            return False
        if require_origin:
            origin = self.headers.get("Origin")
            token = self.headers.get("X-NoteWitness-CSRF")
            if origin not in self.server.allowed_origins or not secrets.compare_digest(token or "", self.server.csrf_token):
                self._json_error(HTTPStatus.FORBIDDEN, "origin_or_csrf_rejected")
                return False
        return True

    def _session_is_authenticated(self) -> bool:
        tokens = self.headers.get_all(SESSION_HEADER_NAME, [])
        return (
            len(tokens) == 1
            and len(tokens[0]) <= 128
            and tokens[0].isascii()
            and self.server.session_is_authenticated(tokens[0])
        )

    def _launch(self, path: str, *, send_body: bool) -> None:
        token = path.removeprefix(LAUNCH_PATH_PREFIX)
        if not send_body:
            self._json_error(HTTPStatus.METHOD_NOT_ALLOWED, "launch_requires_get", send_body=False)
            return
        if not token or "/" in token or not self.server.consume_launch_token(token):
            self._json_error(HTTPStatus.UNAUTHORIZED, "launch_expired_or_invalid")
            return
        self.send_response(HTTPStatus.SEE_OTHER)
        self._security_headers()
        self.send_header("Location", f"/#session={self.server.session_token}")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _request_headers_complete(self) -> bool:
        self._cancel_header_deadline()
        self.close_connection = True
        return not self._request_headers_expired

    def _arm_header_deadline(self) -> None:
        with self._header_deadline_lock:
            self._header_deadline_generation += 1
            generation = self._header_deadline_generation
            timer = threading.Timer(
                self.server.request_header_deadline_seconds,
                self._expire_incomplete_request,
                args=(generation,),
            )
            timer.daemon = True
            self._header_deadline = timer
            timer.start()

    def _cancel_header_deadline(self) -> None:
        lock = getattr(self, "_header_deadline_lock", None)
        if lock is None:
            return
        with lock:
            self._header_deadline_generation += 1
            timer = self._header_deadline
            self._header_deadline = None
        if timer is not None:
            timer.cancel()

    def _expire_incomplete_request(self, generation: int) -> None:
        with self._header_deadline_lock:
            if generation != self._header_deadline_generation:
                return
            self._header_deadline = None
            self._request_headers_expired = True
            self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _asset(self, path: str, *, send_body: bool) -> None:
        filename, content_type = ASSETS[path]
        try:
            body = (self.server.assets_root / filename).read_bytes()
        except OSError:
            self._json_error(HTTPStatus.INTERNAL_SERVER_ERROR, "asset_unavailable", send_body=send_body)
            return
        self._bytes(HTTPStatus.OK, body, content_type, send_body=send_body)


class LocalWorkbenchServer(ThreadingHTTPServer):
    """HTTP server carrying immutable project and origin configuration."""

    daemon_threads = True
    allow_reuse_address = False
    maximum_concurrent_requests = 32
    request_io_timeout_seconds = 30.0
    request_header_deadline_seconds = 10.0

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
        self._media_capability_key = secrets.token_bytes(32)
        self._request_slots = threading.BoundedSemaphore(
            self.maximum_concurrent_requests
        )
        self.media_verification_lock = threading.Lock()
        self.media_verifications: dict[str, tuple[tuple[int, ...], str]] = {}
        self.assets_root = Path(__file__).with_name("assets")
        _require_assets(self.assets_root)
        if runtime_config_path is not None and processing_executor is not None:
            raise WorkbenchServerError("runtime_config_path and processing_executor are mutually exclusive.")
        executor = processing_executor
        if runtime_config_path is not None:
            executor = LocalWorkbenchExecutor.from_private_config(
                self.project_root, runtime_config_path
            )
        self._processing_closed = True
        super().__init__((ALLOWED_BIND_HOST, port), WorkbenchRequestHandler)
        try:
            self.processing = WorkbenchProcessingService(self.project_root, executor)
        except BaseException:
            super().server_close()
            raise
        self._processing_closed = False

    def process_request(self, request: socket.socket, client_address: object) -> None:
        if not self._request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: object) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()

    def server_close(self) -> None:
        if not self._processing_closed:
            self.processing.close()
            self._processing_closed = True
        super().server_close()

    @property
    def origin(self) -> str:
        return f"http://{ALLOWED_BIND_HOST}:{self.server_port}"

    @property
    def launch_url(self) -> str:
        with self._launch_token_lock:
            token = self._launch_token
        if token is None:
            raise WorkbenchServerError("workbench launch URL has already been used.")
        return f"{self.origin}{LAUNCH_PATH_PREFIX}{token}"

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
    def session_token(self) -> str:
        return self._session_token

    def media_capability(self, source_id: str) -> str:
        return hmac.digest(
            self._media_capability_key, source_id.encode("utf-8"), "sha256"
        ).hex()

    def media_capability_is_authenticated(self, source_id: str, token: str) -> bool:
        return (
            len(token) == 64
            and token.isascii()
            and secrets.compare_digest(token, self.media_capability(source_id))
        )

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return frozenset({f"{ALLOWED_BIND_HOST}:{self.server_port}", f"localhost:{self.server_port}"})

    @property
    def allowed_origins(self) -> frozenset[str]:
        return frozenset({self.origin, f"http://localhost:{self.server_port}"})


def _require_assets(root: Path) -> None:
    missing = [filename for filename, _ in ASSETS.values() if not (root / filename).is_file()]
    if missing:
        raise WorkbenchServerError(f"Workbench assets are missing: {', '.join(sorted(missing))}.")
