"""Shared HTTP parsing, response, and validation primitives for the workbench."""

from __future__ import annotations

from http import HTTPStatus
import json
from typing import Any, Mapping
from urllib.parse import urlsplit

from notewitness.application.workbench import WorkbenchError


MAX_JSON_REQUEST_BYTES = 1024 * 1024
MAX_REQUEST_PATH_CHARS = 4_096
_ALLOWED_BIND_HOST = "127.0.0.1"
_LAUNCH_PATH_PREFIX = "/launch/"
_SESSION_COOKIE_NAME = "notewitness_session"
_JS_TYPE = "text/javascript; charset=utf-8"
_CSS_TYPE = "text/css; charset=utf-8"
_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/assets/app.css": ("app.css", _CSS_TYPE),
    "/assets/app.js": ("app.js", _JS_TYPE),
    "/assets/workbench_ui.mjs": ("workbench_ui.mjs", _JS_TYPE),
    "/assets/pitch_estimator.mjs": ("pitch_estimator.mjs", _JS_TYPE),
    "/assets/notewitness-mark.svg": ("notewitness-mark.svg", "image/svg+xml"),
    "/assets/ui/utils.mjs": ("ui/utils.mjs", _JS_TYPE),
    "/assets/ui/value_utils.mjs": ("ui/value_utils.mjs", _JS_TYPE),
    "/assets/ui/filter_utils.mjs": ("ui/filter_utils.mjs", _JS_TYPE),
    "/assets/ui/render_utils.mjs": ("ui/render_utils.mjs", _JS_TYPE),
    "/assets/ui/timeline_utils.mjs": ("ui/timeline_utils.mjs", _JS_TYPE),
    "/assets/ui/shell.mjs": ("ui/shell.mjs", _JS_TYPE),
    "/assets/ui/timeline.mjs": ("ui/timeline.mjs", _JS_TYPE),
    "/assets/ui/panels.mjs": ("ui/panels.mjs", _JS_TYPE),
    "/assets/ui/processing.mjs": ("ui/processing.mjs", _JS_TYPE),
    "/assets/ui/context.mjs": ("ui/context.mjs", _JS_TYPE),
    "/assets/ui/transport.mjs": ("ui/transport.mjs", _JS_TYPE),
    "/assets/js/api.mjs": ("js/api.mjs", _JS_TYPE),
    "/assets/js/playback.mjs": ("js/playback.mjs", _JS_TYPE),
    "/assets/js/processing.mjs": ("js/processing.mjs", _JS_TYPE),
    "/assets/js/actions.mjs": ("js/actions.mjs", _JS_TYPE),
    "/assets/js/review_actions.mjs": ("js/review_actions.mjs", _JS_TYPE),
    "/assets/js/export_actions.mjs": ("js/export_actions.mjs", _JS_TYPE),
    "/assets/js/capture_actions.mjs": ("js/capture_actions.mjs", _JS_TYPE),
    "/assets/js/audio_actions.mjs": ("js/audio_actions.mjs", _JS_TYPE),
    "/assets/js/app_state.mjs": ("js/app_state.mjs", _JS_TYPE),
    "/assets/js/app_rendering.mjs": ("js/app_rendering.mjs", _JS_TYPE),
    "/assets/js/app_loading.mjs": ("js/app_loading.mjs", _JS_TYPE),
    "/assets/js/app_events.mjs": ("js/app_events.mjs", _JS_TYPE),
    "/assets/styles/tokens.css": ("styles/tokens.css", _CSS_TYPE),
    "/assets/styles/base.css": ("styles/base.css", _CSS_TYPE),
    "/assets/styles/shell.css": ("styles/shell.css", _CSS_TYPE),
    "/assets/styles/timeline.css": ("styles/timeline.css", _CSS_TYPE),
    "/assets/styles/panels.css": ("styles/panels.css", _CSS_TYPE),
    "/assets/styles/forms.css": ("styles/forms.css", _CSS_TYPE),
}


class WorkbenchServerError(RuntimeError):
    """The local workbench server could not uphold its runtime contract."""


class WorkbenchProtocolMixin:
    """Methods mixed into the request handler to preserve response semantics."""

    def _request_path(self) -> str | None:
        if len(self.path) > MAX_REQUEST_PATH_CHARS:
            self._json_error(HTTPStatus.REQUEST_URI_TOO_LONG, "request_uri_too_long")
            return None
        parsed = urlsplit(self.path)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            self._json_error(HTTPStatus.BAD_REQUEST, "invalid_request_target")
            return None
        return parsed.path

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
        self, status: HTTPStatus, payload: Mapping[str, Any], *, send_body: bool = True
    ) -> None:
        body = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        self._bytes(status, body, "application/json; charset=utf-8", send_body=send_body)

    def _json_error(
        self, status: HTTPStatus, code: str, *, send_body: bool = True
    ) -> None:
        self.close_connection = True
        self._json(status, {"error": code}, send_body=send_body)

    def _bytes(
        self, status: HTTPStatus, body: bytes, content_type: str, *, send_body: bool
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
        "/", "/api/bookmarks", "/api/actors", "/api/captures", "/api/exports/music",
        "/api/exports/transcript", "/api/imports", "/api/jobs", "/api/metronome",
        "/api/practice", "/api/review/accept", "/api/review/relations/accept",
        "/api/review/relations/reject", "/api/review/revise", "/api/tuner", "/api/workbench",
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


def _optional_number(payload: Mapping[str, Any], name: str, *, default: float) -> float:
    value = payload.get(name, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise WorkbenchError(f"{name} must be a number.")
    return float(value)


def _required_header(headers: Mapping[str, str], name: str, maximum: int) -> str:
    value = headers.get(name)
    if value is None or not value.strip() or len(value) > maximum:
        raise WorkbenchError(f"{name} must be bounded non-empty text.")
    return value.strip()
