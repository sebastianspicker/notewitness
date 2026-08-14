"""Loopback server lifecycle and request routing for the workbench."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from notewitness.application.workbench import WorkbenchError
from notewitness.application.workbench_processing import WorkbenchExecutor, WorkbenchProcessingError
from notewitness.local_artifacts import LocalArtifactError
from notewitness.media_ingest import MediaIngestError
from notewitness.project_store import ProjectConflictError, ProjectStoreError

from .workbench_api import WorkbenchApiMixin
from .workbench_media import WorkbenchMediaMixin
from .workbench_protocol import (
    _ALLOWED_BIND_HOST,
    _ASSETS,
    _LAUNCH_PATH_PREFIX,
    _SESSION_COOKIE_NAME,
    WorkbenchProtocolMixin,
    WorkbenchServerError,
    _coarse_log_route,
)


def _legacy(name: str) -> Any:
    """Resolve façade symbols so pre-existing patch targets remain effective."""

    from . import workbench_server

    return getattr(workbench_server, name)


def _music_export_error() -> type[Exception]:
    from notewitness.application.music_export import MusicExportError

    return MusicExportError


_POST_ERROR_RESPONSES = (
    ((ProjectConflictError,), HTTPStatus.CONFLICT, "project_changed"),
    (
        (WorkbenchError, ValueError, LocalArtifactError, _music_export_error()),
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
        if not self._session_is_authenticated():
            self._json_error(HTTPStatus.UNAUTHORIZED, "authentication_required", send_body=send_body)
            return
        if self._dispatch_private_get(path, send_body=send_body):
            return
        self._json_error(HTTPStatus.NOT_FOUND, "route_not_found", send_body=send_body)

    def _dispatch_public_get(self, path: str, *, send_body: bool) -> bool:
        if path in _ASSETS:
            self._asset(path, send_body=send_body)
            return True
        if path.startswith(_LAUNCH_PATH_PREFIX):
            self._launch(path, send_body=send_body)
            return True
        return False

    def _dispatch_private_get(self, path: str, *, send_body: bool) -> bool:
        handler = {"/api/workbench": self._workbench_snapshot, "/api/jobs": self._job_snapshot}.get(path)
        if handler is not None:
            handler(send_body=send_body)
            return True
        prefix = "/api/media/"
        if path.startswith(prefix):
            encoded_source_id = path[len(prefix):]
            if not encoded_source_id or "/" in encoded_source_id:
                self._json_error(HTTPStatus.NOT_FOUND, "media_not_found")
                return True
            try:
                self._media(unquote(encoded_source_id, errors="strict"), send_body=send_body)
            except (UnicodeError, WorkbenchError, ProjectStoreError, OSError):
                self._json_error(HTTPStatus.NOT_FOUND, "media_not_found")
            return True
        return False

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
        tokens: list[str] = []
        for component in self.headers.get("Cookie", "").split(";"):
            name, separator, value = component.strip().partition("=")
            if separator and name == _SESSION_COOKIE_NAME:
                tokens.append(value)
        return len(tokens) == 1 and self.server.session_is_authenticated(tokens[0])

    def _launch(self, path: str, *, send_body: bool) -> None:
        token = path.removeprefix(_LAUNCH_PATH_PREFIX)
        if not send_body:
            self._json_error(HTTPStatus.METHOD_NOT_ALLOWED, "launch_requires_get", send_body=False)
            return
        if not token or "/" in token or not self.server.consume_launch_token(token):
            self._json_error(HTTPStatus.UNAUTHORIZED, "launch_expired_or_invalid")
            return
        self.send_response(HTTPStatus.SEE_OTHER)
        self._security_headers()
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", self.server.session_cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _asset(self, path: str, *, send_body: bool) -> None:
        filename, content_type = _ASSETS[path]
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
        store = _legacy("ProjectStore")
        self.project_root = store(project_root).root
        store(self.project_root).load()
        self.csrf_token = secrets.token_urlsafe(32)
        self._launch_token: str | None = secrets.token_urlsafe(32)
        self._launch_token_lock = threading.Lock()
        self._session_token = secrets.token_urlsafe(32)
        self.media_verification_lock = threading.Lock()
        self.media_verifications: dict[str, tuple[tuple[int, ...], str]] = {}
        self.assets_root = Path(__file__).with_name("workbench_assets")
        _legacy("_require_assets")(self.assets_root)
        if runtime_config_path is not None and processing_executor is not None:
            raise WorkbenchServerError("runtime_config_path and processing_executor are mutually exclusive.")
        executor = processing_executor
        if runtime_config_path is not None:
            executor = _legacy("LocalWorkbenchExecutor").from_private_config(
                self.project_root, runtime_config_path
            )
        self._processing_closed = True
        super().__init__((_ALLOWED_BIND_HOST, port), _legacy("WorkbenchRequestHandler"))
        try:
            self.processing = _legacy("WorkbenchProcessingService")(self.project_root, executor)
        except BaseException:
            super().server_close()
            raise
        self._processing_closed = False

    def server_close(self) -> None:
        if not self._processing_closed:
            self.processing.close()
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
        return f"{_SESSION_COOKIE_NAME}={self._session_token}; HttpOnly; SameSite=Strict; Path=/"

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return frozenset({f"{_ALLOWED_BIND_HOST}:{self.server_port}", f"localhost:{self.server_port}"})

    @property
    def allowed_origins(self) -> frozenset[str]:
        return frozenset({self.origin, f"http://localhost:{self.server_port}"})


def _require_assets(root: Path) -> None:
    missing = [filename for filename, _ in _ASSETS.values() if not (root / filename).is_file()]
    if missing:
        raise WorkbenchServerError(f"Workbench assets are missing: {', '.join(sorted(missing))}.")
