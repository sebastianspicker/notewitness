from __future__ import annotations

import unittest

from notewitness.application.capture import (
    CaptureTransitionError,
    LocalCaptureCoordinator,
)
from notewitness.domain.capture import (
    CaptureHandle,
    CaptureRequest,
    CapturedMedia,
    CaptureState,
)


class FakeCaptureBackend:
    def __init__(self) -> None:
        self.cancelled = False

    def start(self, request: CaptureRequest) -> CaptureHandle:
        return CaptureHandle(request.session_id, "backend:test")

    def finish(self, handle: CaptureHandle) -> CapturedMedia:
        return CapturedMedia(
            session_id=handle.session_id,
            path="media/test.wav",
            sha256="a" * 64,
            duration_us=1_000_000,
            byte_count=16,
            rights_id="rights:test",
        )

    def cancel(self, handle: CaptureHandle) -> None:
        self.cancelled = True


class CaptureCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = CaptureRequest(
            session_id="capture:test",
            destination="media/test.wav",
            rights_id="rights:test",
            audio_input_id="input:default",
        )

    def test_explicit_capture_transitions_to_completed_media(self) -> None:
        coordinator = LocalCaptureCoordinator(FakeCaptureBackend())

        coordinator.start(self.request)
        self.assertEqual(CaptureState.RECORDING, coordinator.state)
        media = coordinator.finish()

        self.assertEqual(CaptureState.COMPLETED, coordinator.state)
        self.assertEqual("a" * 64, media.sha256)

    def test_double_start_is_rejected(self) -> None:
        coordinator = LocalCaptureCoordinator(FakeCaptureBackend())
        coordinator.start(self.request)

        with self.assertRaises(CaptureTransitionError):
            coordinator.start(self.request)

    def test_capture_paths_cannot_escape_project_media(self) -> None:
        with self.assertRaises(ValueError):
            CaptureRequest(
                session_id="capture:escape",
                destination="../outside.wav",
                rights_id="rights:test",
                audio_input_id="input:default",
            )

    def test_mismatched_backend_handle_is_cancelled(self) -> None:
        backend = FakeCaptureBackend()
        backend.start = (  # type: ignore[method-assign]
            lambda request: CaptureHandle("capture:wrong", "bad")
        )
        coordinator = LocalCaptureCoordinator(backend)

        with self.assertRaises(CaptureTransitionError):
            coordinator.start(self.request)

        self.assertTrue(backend.cancelled)
        self.assertEqual(CaptureState.FAILED, coordinator.state)


if __name__ == "__main__":
    unittest.main()
