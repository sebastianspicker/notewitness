"""Stateful one-action capture coordination over a local backend."""

from __future__ import annotations

from notewitness.application.ports import CapturePort
from notewitness.domain.capture import (
    CaptureHandle,
    CaptureRequest,
    CapturedMedia,
    CaptureState,
)


class CaptureTransitionError(RuntimeError):
    pass


class LocalCaptureCoordinator:
    """Keep capture transitions explicit while the backend owns device I/O."""

    def __init__(self, backend: CapturePort) -> None:
        self._backend = backend
        self._state = CaptureState.IDLE
        self._handle: CaptureHandle | None = None
        self._request: CaptureRequest | None = None

    @property
    def state(self) -> CaptureState:
        return self._state

    def start(self, request: CaptureRequest) -> CaptureHandle:
        if self._state is not CaptureState.IDLE:
            raise CaptureTransitionError(f"Cannot start capture from {self._state.value}.")
        handle = self._backend.start(request)
        if handle.session_id != request.session_id:
            cancel_error: Exception | None = None
            try:
                self._backend.cancel(handle)
            except Exception as exc:
                cancel_error = exc
            self._state = CaptureState.FAILED
            if cancel_error is not None:
                raise CaptureTransitionError(
                    "Capture backend returned a different session ID and could not "
                    "be cancelled cleanly."
                ) from cancel_error
            raise CaptureTransitionError("Capture backend returned a different session ID.")
        self._handle = handle
        self._request = request
        self._state = CaptureState.RECORDING
        return handle

    def finish(self) -> CapturedMedia:
        handle = self._require_recording()
        self._state = CaptureState.FINALIZING
        try:
            media = self._backend.finish(handle)
        except Exception:
            self._state = CaptureState.FAILED
            raise
        request = self._request
        if (
            request is None
            or media.session_id != handle.session_id
            or media.path != request.destination
            or media.rights_id != request.rights_id
        ):
            self._state = CaptureState.FAILED
            raise CaptureTransitionError(
                "Capture result does not match its session, destination, or rights."
            )
        self._state = CaptureState.COMPLETED
        return media

    def cancel(self) -> None:
        handle = self._require_recording()
        self._backend.cancel(handle)
        self._state = CaptureState.CANCELLED

    def _require_recording(self) -> CaptureHandle:
        if self._state is not CaptureState.RECORDING or self._handle is None:
            raise CaptureTransitionError(
                f"Capture is not recording (current state: {self._state.value})."
            )
        return self._handle
