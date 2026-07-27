"""Composition-time registry for probed local device and model capabilities."""

from __future__ import annotations

from collections.abc import Callable

from notewitness.application.adapter_registry import StrictLocalAdapterRegistry


_BACKEND_CAPABILITIES = frozenset(
    {
        "one_action_capture",
        "local_media_ingest",
        "local_playback_backend",
        "lesson_history",
        "metronome_audio_output",
        "live_pitch_input",
    }
)
_COMPONENT_CAPABILITIES = frozenset(
    {
        "bounded_resumable_jobs",
        "html_text_vtt_transcript_exports",
        "research_transcription_options",
        "transcript_correction_workspace",
        "transcription_language_modes",
        "transcription_run_manifest",
        "transcription_speaker_options",
    }
)


class BackendRegistrationError(RuntimeError):
    pass


class RuntimeCapabilityRegistry:
    """Expose only adapters, devices, and services that passed a local probe."""

    def __init__(
        self, analysis_registry: StrictLocalAdapterRegistry | None = None
    ) -> None:
        self._analysis_registry = analysis_registry
        self._backends: dict[str, str] = {}

    def register_backend(
        self,
        capability_id: str,
        provider_id: str,
        probe: Callable[[], bool],
    ) -> None:
        if capability_id not in _BACKEND_CAPABILITIES:
            raise BackendRegistrationError(
                f"Capability {capability_id!r} is not a device/backend capability."
            )
        self._register(capability_id, provider_id, probe)

    def register_component(
        self,
        capability_id: str,
        provider_id: str,
        probe: Callable[[], bool],
    ) -> None:
        """Register a bounded local service after its executable probe passes."""

        if capability_id not in _COMPONENT_CAPABILITIES:
            raise BackendRegistrationError(
                f"Capability {capability_id!r} is not a local service component."
            )
        self._register(capability_id, provider_id, probe)

    def _register(
        self,
        capability_id: str,
        provider_id: str,
        probe: Callable[[], bool],
    ) -> None:
        if not provider_id:
            raise BackendRegistrationError("provider_id must not be empty.")
        if capability_id in self._backends:
            raise BackendRegistrationError(
                f"Capability {capability_id!r} already has a registered provider."
            )
        try:
            passed = probe()
        except Exception as exc:
            raise BackendRegistrationError(
                f"Local capability probe failed for {capability_id!r}."
            ) from exc
        if passed is not True:
            raise BackendRegistrationError(
                f"Local capability probe did not pass for {capability_id!r}."
            )
        self._backends[capability_id] = provider_id

    @property
    def available_capability_ids(self) -> tuple[str, ...]:
        capabilities = set(self._backends)
        if self._analysis_registry is not None:
            capabilities.update(self._analysis_registry.available_capability_ids)
        return tuple(sorted(capabilities))
