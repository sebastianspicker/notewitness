"""Small HTTP doubles shared by transport and provider tests."""

from __future__ import annotations


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_type: str = "application/json",
    ) -> None:
        self._body = body
        self.status = status
        self.headers = {"Content-Type": content_type}

    def read(self, limit: int) -> bytes:
        return self._body[:limit]

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeOpener:
    def __init__(
        self,
        response: FakeResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls = 0
        self.request: object | None = None
        self.timeout: float | None = None

    def open(self, request: object, *, timeout: float) -> FakeResponse:
        self.calls += 1
        self.request = request
        self.timeout = timeout
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response
