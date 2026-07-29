from __future__ import annotations

import io
import unittest
from urllib.error import HTTPError

from notewitness.network import (
    MAX_RESPONSE_BYTES,
    NetworkAccessDenied,
    NetworkMode,
    NetworkPolicy,
    OpenAIHTTPTransport,
    TransportFailure,
)
from tests.support.http_fakes import FakeOpener, FakeResponse


class NetworkPolicyTests(unittest.TestCase):
    def test_offline_and_download_modes_deny_remote_inference(self) -> None:
        for mode in (NetworkMode.OFFLINE, NetworkMode.DOWNLOAD_MODELS_ONLY):
            with self.subTest(mode=mode):
                with self.assertRaises(NetworkAccessDenied):
                    NetworkPolicy(mode).require_remote_inference(
                        confirmed=True, rights_allow_remote=True
                    )

    def test_remote_mode_still_requires_confirmation_and_rights(self) -> None:
        policy = NetworkPolicy(NetworkMode.REMOTE_EXPLICIT)
        with self.assertRaises(NetworkAccessDenied):
            policy.require_remote_inference(confirmed=False, rights_allow_remote=True)
        with self.assertRaises(NetworkAccessDenied):
            policy.require_remote_inference(confirmed=True, rights_allow_remote=False)

    def test_transport_rejects_oversized_response(self) -> None:
        opener = FakeOpener(
            FakeResponse(b"x" * (MAX_RESPONSE_BYTES + 1))
        )

        with self.assertRaisesRegex(TransportFailure, "exceeds"):
            OpenAIHTTPTransport(opener).post(api_key="test-key", payload={"x": 1})

    def test_transport_classifies_429_without_exposing_body(self) -> None:
        error_body = io.BytesIO(b'{"error":{"message":"sensitive echo"}}')
        error = HTTPError(
            "https://api.openai.com/v1/responses", 429, "rate", {}, error_body
        )
        opener = FakeOpener(error=error)

        with self.assertRaises(TransportFailure) as context:
            OpenAIHTTPTransport(opener).post(api_key="test-key", payload={"x": 1})

        self.assertTrue(context.exception.retryable)
        self.assertNotIn("sensitive echo", str(context.exception))

    def test_transport_requires_json_content_type(self) -> None:
        opener = FakeOpener(FakeResponse(b"{}", content_type="text/plain"))

        with self.assertRaisesRegex(TransportFailure, "application/json"):
            OpenAIHTTPTransport(opener).post(api_key="test-key", payload={"x": 1})

    def test_transport_rejects_non_header_safe_key_without_leaking_it(self) -> None:
        secret = "test-secret\r\nInjected: value"
        opener = FakeOpener(FakeResponse(b"{}"))

        with self.assertRaises(TransportFailure) as context:
            OpenAIHTTPTransport(opener).post(api_key=secret, payload={"x": 1})

        self.assertEqual(0, opener.calls)
        self.assertNotIn("test-secret", str(context.exception))

    def test_transport_sanitizes_header_errors_from_opener(self) -> None:
        opener = FakeOpener(
            error=ValueError("Invalid header value b'Bearer test-key'")
        )

        with self.assertRaises(TransportFailure) as context:
            OpenAIHTTPTransport(opener).post(api_key="test-key", payload={"x": 1})

        self.assertNotIn("test-key", str(context.exception))
        self.assertIsNone(context.exception.__cause__)

    def test_transport_rejects_redirects_as_sanitized_http_failures(self) -> None:
        error = HTTPError(
            "https://api.openai.com/v1/responses",
            302,
            "redirect",
            {"Location": "https://example.invalid/steal"},
            io.BytesIO(b"redirect body"),
        )
        opener = FakeOpener(error=error)

        with self.assertRaises(TransportFailure) as context:
            OpenAIHTTPTransport(opener).post(api_key="test-key", payload={"x": 1})

        self.assertEqual(302, context.exception.status_code)
        self.assertNotIn("example.invalid", str(context.exception))


if __name__ == "__main__":
    unittest.main()
