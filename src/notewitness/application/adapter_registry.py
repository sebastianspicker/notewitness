"""Strict-local registration of installed analysis adapters and artifacts."""

from __future__ import annotations

from collections.abc import Mapping

from notewitness.application.noscribe_conformance import (
    AdapterConformanceResult,
    NOSCRIBE_ASR_FEATURES,
    NOSCRIBE_DIARIZATION_FEATURES,
    NOSCRIBE_PROBE_INVARIANTS,
    NOSCRIBE_PROBE_PROFILE,
    NOSCRIBE_PROBE_VERSION,
    required_features,
    run_noscribe_conformance,
)
from notewitness.application.pipeline import LocalAnalysisPipeline
from notewitness.application.ports import AnalysisPort
from notewitness.domain.analysis import AnalysisStage
from notewitness.domain.models import (
    AdapterManifest,
    ArtifactVerification,
    ModelArtifact,
    strict_local_adapter_issues,
)


_CAPABILITY_BY_STAGE = {
    AnalysisStage.ACTIVITY_SEGMENTATION: "activity_segmentation",
    AnalysisStage.SPEECH_RECOGNITION: "local_asr",
    AnalysisStage.ANONYMOUS_DIARIZATION: "anonymous_diarization",
    AnalysisStage.NOTE_TRANSCRIPTION: "note_detection",
    AnalysisStage.CONTINUOUS_PITCH: "continuous_pitch",
    AnalysisStage.INSTRUMENT_DETECTION: "instrument_detection",
    AnalysisStage.INSTRUMENT_DIARIZATION: "instrument_diarization",
    AnalysisStage.ONSET_BEAT_CHORD: "onset_beat_chord",
    AnalysisStage.SCORE_ALIGNMENT: "score_alignment",
    AnalysisStage.PEDAGOGICAL_RELATIONS: "local_lesson_digest",
}
_TONIC_MODEL_STAGES = frozenset(
    {
        AnalysisStage.ACTIVITY_SEGMENTATION,
        AnalysisStage.SPEECH_RECOGNITION,
        AnalysisStage.ANONYMOUS_DIARIZATION,
        AnalysisStage.NOTE_TRANSCRIPTION,
        AnalysisStage.INSTRUMENT_DETECTION,
        AnalysisStage.INSTRUMENT_DIARIZATION,
    }
)


class AdapterRegistrationError(RuntimeError):
    pass


def _registration_issues(
    manifest: AdapterManifest,
    adapter: AnalysisPort,
    registry: StrictLocalAdapterRegistry,
) -> tuple[str, ...]:
    issues = strict_local_adapter_issues(manifest, registry._artifacts)
    issues += _artifact_verification_issues(
        manifest, registry._artifacts, registry._verifications
    )
    if adapter.stage.value not in manifest.supported_stages:
        issues += (f"manifest does not declare stage {adapter.stage.value!r}",)
    if adapter.generator_id != manifest.adapter_id:
        issues += ("adapter generator_id does not match its manifest ID",)
    if adapter.stage in registry._adapters:
        issues += (f"stage {adapter.stage.value!r} already has an adapter",)
    return issues


def _artifact_verification_issues(
    manifest: AdapterManifest,
    artifacts: Mapping[str, ModelArtifact],
    verifications: Mapping[str, ArtifactVerification],
) -> tuple[str, ...]:
    issues: tuple[str, ...] = ()
    for artifact_id in (
        manifest.code_artifact_id,
        *manifest.weight_artifact_ids,
    ):
        artifact = artifacts.get(artifact_id)
        verification = verifications.get(artifact_id)
        if artifact is not None and verification is None:
            issues += (f"artifact {artifact_id!r} has no verification record",)
        elif artifact is not None and verification is not None:
            issues += verification.issues_for(artifact)
    return issues


def _run_conformance(
    manifest: AdapterManifest,
    adapter: AnalysisPort,
) -> AdapterConformanceResult | None:
    noscribe_features = required_features(adapter.stage)
    if not noscribe_features or not noscribe_features.issubset(
        manifest.supported_features
    ):
        return None
    try:
        conformance = run_noscribe_conformance(adapter)
    except Exception as exc:
        raise AdapterRegistrationError(
            f"Adapter conformance probe failed for {adapter.stage.value!r}."
        ) from exc
    conformance_issues = _conformance_issues(manifest, adapter, conformance)
    if conformance_issues:
        raise AdapterRegistrationError("; ".join(conformance_issues))
    return conformance


def _conformance_issues(
    manifest: AdapterManifest,
    adapter: AnalysisPort,
    result: AdapterConformanceResult,
) -> tuple[str, ...]:
    return (
        _conformance_probe_issues(result)
        + _conformance_identity_issues(adapter, result)
        + _conformance_feature_issues(manifest, adapter, result)
        + _conformance_invariant_issues(result)
    )


def _conformance_probe_issues(
    result: AdapterConformanceResult,
) -> tuple[str, ...]:
    issues: list[str] = []
    if not result.passed:
        issues.append("adapter conformance probe did not pass")
    if result.profile_id != NOSCRIBE_PROBE_PROFILE:
        issues.append("adapter conformance profile is unsupported")
    if result.probe_version != NOSCRIBE_PROBE_VERSION:
        issues.append("adapter conformance probe version is unsupported")
    return tuple(issues)


def _conformance_identity_issues(
    adapter: AnalysisPort,
    result: AdapterConformanceResult,
) -> tuple[str, ...]:
    issues: list[str] = []
    if result.adapter_id != adapter.generator_id:
        issues.append("conformance adapter ID does not match the adapter")
    if result.stage is not adapter.stage:
        issues.append("conformance stage does not match the adapter")
    return tuple(issues)


def _conformance_feature_issues(
    manifest: AdapterManifest,
    adapter: AnalysisPort,
    result: AdapterConformanceResult,
) -> tuple[str, ...]:
    issues: list[str] = []
    if not set(result.passed_features).issubset(manifest.supported_features):
        issues.append("probe passed features not declared by the manifest")
    required = {
        AnalysisStage.SPEECH_RECOGNITION: NOSCRIBE_ASR_FEATURES,
        AnalysisStage.ANONYMOUS_DIARIZATION: NOSCRIBE_DIARIZATION_FEATURES,
    }.get(adapter.stage)
    if required is None or not required.issubset(result.passed_features):
        issues.append("adapter conformance features are incomplete")
    return tuple(issues)


def _conformance_invariant_issues(
    result: AdapterConformanceResult,
) -> tuple[str, ...]:
    if NOSCRIBE_PROBE_INVARIANTS.issubset(result.observed_invariants):
        return ()
    return ("adapter conformance invariants are incomplete",)


class StrictLocalAdapterRegistry:
    """Compose only hash/rights-described adapters that run without networking."""

    def __init__(
        self,
        artifacts: Mapping[str, ModelArtifact],
        verifications: Mapping[str, ArtifactVerification],
    ) -> None:
        self._artifacts = dict(artifacts)
        self._verifications = dict(verifications)
        self._adapters: dict[AnalysisStage, AnalysisPort] = {}
        self._manifests: dict[AnalysisStage, AdapterManifest] = {}
        self._conformance: dict[AnalysisStage, AdapterConformanceResult] = {}

    def register(
        self,
        manifest: AdapterManifest,
        adapter: AnalysisPort,
    ) -> None:
        issues = _registration_issues(
            manifest,
            adapter,
            self,
        )
        if issues:
            raise AdapterRegistrationError("; ".join(issues))

        conformance = _run_conformance(manifest, adapter)
        self._adapters[adapter.stage] = adapter
        self._manifests[adapter.stage] = manifest
        if conformance is not None:
            self._conformance[adapter.stage] = conformance

    @property
    def available_stages(self) -> tuple[AnalysisStage, ...]:
        return tuple(sorted(self._adapters, key=lambda stage: stage.value))

    @property
    def available_capability_ids(self) -> tuple[str, ...]:
        capabilities = {
            _CAPABILITY_BY_STAGE[stage]
            for stage in self._adapters
            if stage in _CAPABILITY_BY_STAGE
        }
        if _TONIC_MODEL_STAGES.issubset(self._adapters):
            capabilities.add("strict_local_model_runtime")
        asr_conformance = self._conformance.get(
            AnalysisStage.SPEECH_RECOGNITION
        )
        if asr_conformance is not None and NOSCRIBE_ASR_FEATURES.issubset(
            asr_conformance.passed_features
        ):
            capabilities.add("noscribe_asr_conformance")
        diarization_conformance = self._conformance.get(
            AnalysisStage.ANONYMOUS_DIARIZATION
        )
        if (
            diarization_conformance is not None
            and NOSCRIBE_DIARIZATION_FEATURES.issubset(
                diarization_conformance.passed_features
            )
        ):
            capabilities.add("noscribe_diarization_conformance")
        return tuple(sorted(capabilities))

    def pipeline(self) -> LocalAnalysisPipeline:
        return LocalAnalysisPipeline(self._adapters.values())
