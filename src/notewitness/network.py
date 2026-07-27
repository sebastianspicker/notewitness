"""Application-enforced network policy and the sole outbound HTTP transport."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
import json
import socket
from threading import Lock
from typing import Any, Iterator, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 15.0
MAX_API_KEY_CHARS = 512


class NetworkMode(StrEnum):
    OFFLINE = "offline"
    DOWNLOAD_MODELS_ONLY = "download_models_only"
    REMOTE_EXPLICIT = "remote_explicit"


class NetworkAccessDenied(RuntimeError):
    """Raised before transport creation when remote access is not authorized."""


@dataclass(slots=True)
class OfflineSocketProbe:
    """Observed socket attempts while a startup conformance probe is running."""

    attempted_operations: int = 0

    def deny(self, *_args: object, **_kwargs: object) -> None:
        self.attempted_operations += 1
        raise NetworkAccessDenied(
            "Outbound sockets are disabled during strict-local conformance probes."
        )


_OFFLINE_PROBE_LOCK = Lock()


@contextmanager
def deny_outbound_sockets() -> Iterator[OfflineSocketProbe]:
    """Temporarily deny socket creation for a synchronous startup probe.

    This guard is intentionally narrow: adapter conformance runs synchronously
    during registry composition, before workers start. Deployment sandboxing is
    still required for a hard process-level offline guarantee.
    """

    probe = OfflineSocketProbe()
    with _OFFLINE_PROBE_LOCK:
        original_socket = socket.socket
        original_create_connection = socket.create_connection
        original_getaddrinfo = socket.getaddrinfo
        socket.socket = probe.deny  # type: ignore[assignment]
        socket.create_connection = probe.deny  # type: ignore[assignment]
        socket.getaddrinfo = probe.deny  # type: ignore[assignment]
        try:
            yield probe
        finally:
            socket.socket = original_socket
            socket.create_connection = original_create_connection
            socket.getaddrinfo = original_getaddrinfo


@dataclass(frozen=True, slots=True)
class NetworkPolicy:
    mode: NetworkMode = NetworkMode.OFFLINE

    @classmethod
    def from_mapping(cls, value: object) -> "NetworkPolicy":
        if not isinstance(value, Mapping):
            return cls()
        raw_mode = value.get("mode", NetworkMode.OFFLINE.value)
        try:
            return cls(NetworkMode(str(raw_mode)))
        except ValueError:
            return cls()

    def require_remote_inference(
        self, *, confirmed: bool, rights_allow_remote: bool
    ) -> None:
        if self.mode is not NetworkMode.REMOTE_EXPLICIT:
            raise NetworkAccessDenied(
                "Remote inference requires project network mode 'remote_explicit'."
            )
        if not confirmed:
            raise NetworkAccessDenied(
                "Remote inference requires the explicit --allow-remote flag."
            )
        if not rights_allow_remote:
            raise NetworkAccessDenied(
                "Every selected event and source must allow remote processing."
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
        if (
            not isinstance(api_key, str)
            or not 1 <= len(api_key) <= MAX_API_KEY_CHARS
            or any(ord(character) < 0x21 or ord(character) > 0x7E for character in api_key)
        ):
            raise TransportFailure("OpenAI API key has an invalid header-safe format.")
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

        try:
            request = Request(
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

        try:
            with self._opener.open(
                request, timeout=REQUEST_TIMEOUT_SECONDS
            ) as response:
                status = int(getattr(response, "status", 200))
                content_type = str(response.headers.get("Content-Type", ""))
                raw = response.read(MAX_RESPONSE_BYTES + 1)
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
            raise TransportFailure(
                "OpenAI request failed at the transport layer."
            ) from None

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
