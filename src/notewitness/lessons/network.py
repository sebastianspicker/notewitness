"""Application-enforced network policy and the sole outbound HTTP transport."""

from __future__ import annotations

import json
import socket
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from notewitness.core.evidence.contract import (
    NetworkAccessDenied,
    NetworkMode,
    NetworkPolicy,
)


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 15.0
MAX_API_KEY_CHARS = 512

__all__ = (
    "MAX_API_KEY_CHARS",
    "NetworkAccessDenied",
    "NetworkMode",
    "NetworkPolicy",
    "OPENAI_RESPONSES_URL",
    "OpenAIHTTPTransport",
    "TransportFailure",
)


class TransportFailure(RuntimeError):
    """A sanitized remote transport or API failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = status_code == 429 or (
            status_code is not None and 500 <= status_code <= 599
        )


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(  # type: ignore[override]
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class OpenAIHTTPTransport:
    """A bounded, non-redirecting transport pinned to OpenAI's Responses URL."""

    def __init__(self, opener: Any | None = None) -> None:
        self._opener = (
            opener
            if opener is not None
            else build_opener(ProxyHandler({}), _RejectRedirects())
        )

    def post(self, *, api_key: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        _validate_api_key(api_key)
        request = _openai_request(api_key, _json_request_body(payload))
        status, content_type, raw = _openai_response(self._opener, request)
        return _validated_json_response(status, content_type, raw)


def _validate_api_key(api_key: str) -> None:
    if (
        not isinstance(api_key, str)
        or not 1 <= len(api_key) <= MAX_API_KEY_CHARS
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in api_key)
    ):
        raise TransportFailure("OpenAI API key has an invalid header-safe format.")


def _json_request_body(payload: Mapping[str, Any]) -> bytes:
    try:
        body = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise TransportFailure(
            "OpenAI request payload is not JSON serializable."
        ) from None
    if len(body) > MAX_REQUEST_BYTES:
        raise TransportFailure(
            f"OpenAI request exceeds the {MAX_REQUEST_BYTES}-byte limit."
        )
    return body


def _openai_request(api_key: str, body: bytes) -> Request:
    try:
        return Request(
            OPENAI_RESPONSES_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "NoteWitness/0.1",
            },
        )
    except (TypeError, ValueError, UnicodeError):
        raise TransportFailure("OpenAI request headers are invalid.") from None


def _openai_response(opener: Any, request: Request) -> tuple[int, str, bytes]:
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return (
                int(getattr(response, "status", 200)),
                str(response.headers.get("Content-Type", "")),
                response.read(MAX_RESPONSE_BYTES + 1),
            )
    except HTTPError as exc:
        status_code = exc.code
        exc.close()
        raise TransportFailure(
            f"OpenAI request failed with HTTP {status_code}.",
            status_code=status_code,
        ) from None
    except (TimeoutError, socket.timeout):
        raise TransportFailure("OpenAI request timed out.") from None
    except URLError:
        raise TransportFailure("OpenAI request could not reach the endpoint.") from None
    except (ValueError, UnicodeError):
        raise TransportFailure("OpenAI request headers are invalid.") from None
    except OSError:
        raise TransportFailure("OpenAI request failed at the transport layer.") from None


def _validated_json_response(
    status: int, content_type: str, raw: bytes
) -> dict[str, Any]:
    if not 200 <= status <= 299:
        raise TransportFailure(
            f"OpenAI request failed with HTTP {status}.", status_code=status
        )
    if len(raw) > MAX_RESPONSE_BYTES:
        raise TransportFailure(
            f"OpenAI response exceeds the {MAX_RESPONSE_BYTES}-byte limit."
        )
    media_type = content_type.partition(";")[0].strip().lower()
    if media_type != "application/json":
        raise TransportFailure("OpenAI response did not use application/json.")
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise TransportFailure("OpenAI response was not valid JSON.") from None
    if not isinstance(decoded, dict):
        raise TransportFailure("OpenAI response JSON must be an object.")
    return decoded
