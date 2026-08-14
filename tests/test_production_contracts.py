from __future__ import annotations

from dataclasses import replace
import socket
import unittest
from unittest.mock import patch

from notewitness.application.adapter_registry import (
    AdapterRegistrationError,
    StrictLocalAdapterRegistry,
)
from notewitness.application.capabilities import (
    CapabilityLevel,
    capability_manifest,
    profile_readiness,
)
from notewitness.application.runtime_registry import (
    BackendRegistrationError,
    RuntimeCapabilityRegistry,
)
from notewitness.domain.analysis import (
    AnalysisBatch,
    AnalysisRequest,
    AnalysisResult,
    AnalysisStage,
    AnalysisState,
    SpeakerSegmentHypothesis,
    SpeechSegmentHypothesis,
    WordHypothesis,
)
from notewitness.domain.evaluation import (
    BenchmarkObservation,
    BenchmarkOutcome,
)
from notewitness.domain.governance import (
    PackageImportDecision,
    PackageTrustState,
)
from notewitness.domain.interop import (
    ExportFormat,
    ExportPreflight,
    LossSeverity,
    ProjectionLoss,
)
from notewitness.domain.models import (
    AdapterManifest,
    ArtifactVerification,
    ArtifactKind,
    ModelInstallPlan,
    ModelArtifact,
    NetworkRequirement,
    strict_local_adapter_issues,
)
from notewitness.domain.timeline import MediaSpan


class StubSpeechAdapter:
    stage = AnalysisStage.SPEECH_RECOGNITION
    name = "verified-test-adapter"
    version = "1"
    generator_id = "adapter:asr"

    def analyze(self, request: object) -> object:
        raise AssertionError("Registration tests do not execute the adapter.")


class ConformingSpeechAdapter(StubSpeechAdapter):
    def analyze(self, request: AnalysisRequest) -> AnalysisBatch:
        mode = request.parameters.get("language_mode")
        profile = str(request.parameters.get("model_profile_id", "precise"))
        disfluencies = request.parameters.get("disfluency_policy") == "include"
        utterances = (
            (("en", "uh legato"), ("de", "bitte"))
            if mode == "multilingual"
            else (("en", "uh legato" if disfluencies else "legato"),)
        )
        hypotheses: list[SpeechSegmentHypothesis | WordHypothesis] = []
        duration = request.spans[0].duration_us // len(utterances)
        for index, (language, text) in enumerate(utterances):
            span = MediaSpan(
                request.source_id,
                request.spans[0].stream_id,
                request.spans[0].start_us + index * duration,
                duration,
            )
            identity = f"{profile}:{mode}:{index}:{request.job_id}"
            word = WordHypothesis(
                hypothesis_id=f"word:{identity}",
                span=span,
                state=AnalysisState.READY,
                text=text,
                language=language,
                anonymous_speaker_cluster="SPEAKER_00",
                confidence=0.9,
                generator_id=self.generator_id,
            )
            segment = SpeechSegmentHypothesis(
                hypothesis_id=f"segment:{identity}",
                span=span,
                state=AnalysisState.READY,
                text=text,
                language=language,
                word_hypothesis_ids=(word.hypothesis_id,),
                confidence=0.8,
                generator_id=self.generator_id,
            )
            hypotheses.extend((segment, word))
        return AnalysisBatch(
            result=AnalysisResult(
                stage=self.stage,
                state=AnalysisState.READY,
                hypothesis_ids=tuple(item.hypothesis_id for item in hypotheses),
                diagnostics=(),
            ),
            hypotheses=tuple(hypotheses),
        )


class InvariantBreakingSpeechAdapter(ConformingSpeechAdapter):
    def __init__(self, violation: str) -> None:
        self.violation = violation

    def analyze(self, request: AnalysisRequest) -> AnalysisBatch:
        if self.violation == "network":
            socket.socket()
        if self.violation == "network_caught":
            try:
                socket.socket()
            except RuntimeError:
                pass
        batch = super().analyze(request)
        if self.violation == "typed_output":
            return object()  # type: ignore[return-value]
        if self.violation == "source_time":
            first, *remaining = batch.hypotheses
            outside = replace(
                first,
                span=MediaSpan("source:outside", "audio", 0, 1_000),
            )
            return AnalysisBatch(batch.result, (outside, *remaining))
        if self.violation == "bounded_output":
            words = tuple(
                WordHypothesis(
                    hypothesis_id=f"word:unbounded:{index}",
                    span=request.spans[0],
                    state=AnalysisState.READY,
                    text="legato",
                    language="en",
                    anonymous_speaker_cluster="SPEAKER_00",
                    confidence=0.9,
                    generator_id=self.generator_id,
                )
                for index in range(65)
            )
            return AnalysisBatch(
                AnalysisResult(
                    self.stage,
                    AnalysisState.READY,
                    tuple(word.hypothesis_id for word in words),
                    (),
                ),
                words,
            )
        return batch


class ProbeFailingSpeechAdapter(ConformingSpeechAdapter):
    def __init__(self) -> None:
        self.probe_error = RuntimeError("test probe failure")

    def analyze(self, request: AnalysisRequest) -> AnalysisBatch:
        raise self.probe_error


class ConformingDiarizationAdapter:
    stage = AnalysisStage.ANONYMOUS_DIARIZATION
    name = "verified-diarization-test-adapter"
    version = "1"
    generator_id = "adapter:diarization"

    def analyze(self, request: AnalysisRequest) -> AnalysisBatch:
        mode = request.parameters.get("diarization_mode")
        if mode == "off":
            return AnalysisBatch(
                AnalysisResult(self.stage, AnalysisState.NOT_APPLICABLE, (), ()),
                (),
            )
        count = (
            int(request.parameters["exact_speaker_count"])
            if mode == "exact"
            else 2
        )
        speakers: list[SpeakerSegmentHypothesis] = []
        for index in range(count):
            if mode == "auto":
                start_us = 0 if index == 0 else 800_000
                duration_us = 1_200_000
            else:
                start_us = index * 100_000
                duration_us = 100_000
            speakers.append(
                SpeakerSegmentHypothesis(
                    hypothesis_id=f"speaker:{mode}:{count}:{index}",
                    span=MediaSpan(
                        request.source_id,
                        request.spans[0].stream_id,
                        start_us,
                        duration_us,
                    ),
                    state=AnalysisState.READY,
                    anonymous_cluster_id=f"SPEAKER_{index:02d}",
                    confirmed_actor_id=None,
                    confidence=0.8,
                    generator_id=self.generator_id,
                )
            )
        return AnalysisBatch(
            result=AnalysisResult(
                stage=self.stage,
                state=AnalysisState.READY,
                hypothesis_ids=tuple(item.hypothesis_id for item in speakers),
                diagnostics=(),
            ),
            hypotheses=tuple(speakers),
        )


class ProductionContractTests(unittest.TestCase):
    def test_capability_manifest_has_unique_owned_surfaces(self) -> None:
        manifest = capability_manifest()
        items = manifest["capabilities"]
        ids = [item["capability_id"] for item in items]

        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(item["code_surface"] for item in items))
        self.assertGreater(manifest["counts"][CapabilityLevel.AVAILABLE.value], 0)

    def test_profiles_distinguish_implemented_surfaces_from_external_engines(self) -> None:
        readiness = profile_readiness("tonic-local")

        self.assertFalse(readiness["ready"])
        for capability_id in (
            "activity_segmentation",
            "local_asr",
            "anonymous_diarization",
        ):
            self.assertIn(capability_id, readiness["missing"])
        for capability_id in (
            "one_action_capture",
            "local_playback_backend",
            "graphical_workbench",
            "local_lesson_digest",
        ):
            self.assertNotIn(capability_id, readiness["missing"])
        self.assertNotIn("note_detection", readiness["required"])

        extended = profile_readiness("notewitness-v0.1")
        for capability_id in (
            "note_detection",
            "instrument_detection",
            "strict_local_model_runtime",
        ):
            self.assertIn(capability_id, extended["missing"])
        self.assertNotIn("live_pitch_input", extended["missing"])

        noscribe = profile_readiness("noscribe-research")
        for capability_id in (
            "noscribe_asr_conformance",
            "noscribe_diarization_conformance",
            "research_transcription_options",
        ):
            self.assertIn(capability_id, noscribe["missing"])
        for capability_id in (
            "local_media_ingest",
            "transcript_correction_workspace",
            "transcription_run_manifest",
            "html_text_vtt_transcript_exports",
        ):
            self.assertNotIn(capability_id, noscribe["missing"])

        registry = RuntimeCapabilityRegistry()
        registry.register_backend("lesson_history", "catalog:test", lambda: True)
        with_catalog = profile_readiness("tonic-local", registry=registry)
        self.assertNotIn("one_action_capture", with_catalog["missing"])

    def test_strict_local_adapter_rejects_runtime_network_artifact(self) -> None:
        code = ModelArtifact(
            artifact_id="artifact:code",
            kind=ArtifactKind.CODE,
            name="Adapter",
            version="1",
            source_url="https://example.invalid/adapter",
            sha256="a" * 64,
            license_expression="Apache-2.0",
            size_bytes=10,
            network_requirement=NetworkRequirement.NONE,
            intended_purposes=("speech transcription",),
            known_limitations=(),
            redistribution_permitted=True,
            commercial_use_permitted=True,
        )
        weights = ModelArtifact(
            artifact_id="artifact:weights",
            kind=ArtifactKind.WEIGHTS,
            name="Weights",
            version="1",
            source_url="https://example.invalid/weights",
            sha256="b" * 64,
            license_expression="LicenseRef-test",
            size_bytes=20,
            network_requirement=NetworkRequirement.RUNTIME,
            intended_purposes=("speech transcription",),
            known_limitations=(),
            redistribution_permitted=False,
            commercial_use_permitted=None,
        )
        manifest = AdapterManifest(
            adapter_id="adapter:asr",
            code_artifact_id=code.artifact_id,
            weight_artifact_ids=(weights.artifact_id,),
            supported_stages=("speech_recognition",),
            supported_hardware=("cpu",),
            intended_domain="music lessons",
        )

        issues = strict_local_adapter_issues(
            manifest, {code.artifact_id: code, weights.artifact_id: weights}
        )
        self.assertEqual(
            ("artifact 'artifact:weights' requires runtime networking",), issues
        )

    def test_verified_adapter_is_the_only_path_to_analysis_readiness(self) -> None:
        code = ModelArtifact(
            artifact_id="artifact:code",
            kind=ArtifactKind.CODE,
            name="Adapter",
            version="1",
            source_url="https://example.invalid/adapter",
            sha256="a" * 64,
            license_expression="Apache-2.0",
            size_bytes=10,
            network_requirement=NetworkRequirement.NONE,
            intended_purposes=("speech transcription",),
            known_limitations=(),
            redistribution_permitted=True,
            commercial_use_permitted=True,
        )
        weights = ModelArtifact(
            artifact_id="artifact:weights",
            kind=ArtifactKind.WEIGHTS,
            name="Weights",
            version="1",
            source_url="https://example.invalid/weights",
            sha256="b" * 64,
            license_expression="LicenseRef-test",
            size_bytes=20,
            network_requirement=NetworkRequirement.NONE,
            intended_purposes=("speech transcription",),
            known_limitations=(),
            redistribution_permitted=False,
            commercial_use_permitted=None,
        )
        artifacts = {code.artifact_id: code, weights.artifact_id: weights}
        manifest = AdapterManifest(
            adapter_id="adapter:asr",
            code_artifact_id=code.artifact_id,
            weight_artifact_ids=(weights.artifact_id,),
            supported_stages=(AnalysisStage.SPEECH_RECOGNITION.value,),
            supported_hardware=("cpu",),
            intended_domain="music lessons",
        )
        verifications = {
            code.artifact_id: ArtifactVerification(
                code.artifact_id, code.sha256, code.size_bytes
            ),
            weights.artifact_id: ArtifactVerification(
                weights.artifact_id, weights.sha256, weights.size_bytes
            ),
        }
        registry = StrictLocalAdapterRegistry(artifacts, verifications)
        registry.register(manifest, StubSpeechAdapter())  # type: ignore[arg-type]
        runtime = RuntimeCapabilityRegistry(registry)

        self.assertEqual(
            ("local_asr",),
            runtime.available_capability_ids,
        )
        self.assertNotIn(
            "local_asr", profile_readiness("tonic-local", runtime)["missing"]
        )
        self.assertIn(
            "noscribe_asr_conformance",
            profile_readiness("noscribe-research", runtime)["missing"],
        )

        conforming_manifest = AdapterManifest(
            adapter_id="adapter:asr",
            code_artifact_id=code.artifact_id,
            weight_artifact_ids=(weights.artifact_id,),
            supported_stages=(AnalysisStage.SPEECH_RECOGNITION.value,),
            supported_hardware=("cpu",),
            intended_domain="music lessons",
            supported_features=(
                "auto_language",
                "disfluency_policy",
                "fixed_language",
                "model_profiles",
                "multilingual_language",
                "segment_timestamps",
                "word_confidence",
                "word_timestamps",
            ),
        )
        conforming_registry = StrictLocalAdapterRegistry(artifacts, verifications)
        conforming_registry.register(
            conforming_manifest,
            ConformingSpeechAdapter(),  # type: ignore[arg-type]
        )
        failing_adapter = ProbeFailingSpeechAdapter()
        failing_registry = StrictLocalAdapterRegistry(artifacts, verifications)
        with self.assertRaises(AdapterRegistrationError) as probe_failure:
            failing_registry.register(
                conforming_manifest, failing_adapter  # type: ignore[arg-type]
            )
        self.assertEqual(
            "Adapter conformance probe failed for 'speech_recognition'.",
            str(probe_failure.exception),
        )
        self.assertIs(failing_adapter.probe_error, probe_failure.exception.__cause__)
        self.assertEqual((), failing_registry.available_stages)
        self.assertEqual((), failing_registry.available_capability_ids)
        self.assertEqual({}, failing_registry._manifests)
        self.assertEqual({}, failing_registry._conformance)

        diarization_manifest = AdapterManifest(
            adapter_id="adapter:diarization",
            code_artifact_id=code.artifact_id,
            weight_artifact_ids=(weights.artifact_id,),
            supported_stages=(AnalysisStage.ANONYMOUS_DIARIZATION.value,),
            supported_hardware=("cpu",),
            intended_domain="music lessons",
            supported_features=(
                "anonymous_clusters",
                "auto_speaker_count",
                "diarization_off",
                "exact_speaker_count_1_10",
                "overlap_detection",
            ),
        )
        conforming_registry.register(
            diarization_manifest,
            ConformingDiarizationAdapter(),  # type: ignore[arg-type]
        )
        self.assertEqual(
            (
                AnalysisStage.ANONYMOUS_DIARIZATION,
                AnalysisStage.SPEECH_RECOGNITION,
            ),
            conforming_registry.available_stages,
        )
        self.assertEqual(
            (
                "anonymous_diarization",
                "local_asr",
                "noscribe_asr_conformance",
                "noscribe_diarization_conformance",
            ),
            conforming_registry.available_capability_ids,
        )
        stub_failure_registry = StrictLocalAdapterRegistry(artifacts, verifications)
        with self.assertRaisesRegex(AdapterRegistrationError, "probe failed"):
            stub_failure_registry.register(
                conforming_manifest,
                StubSpeechAdapter(),  # type: ignore[arg-type]
            )
        self.assertEqual({}, stub_failure_registry._manifests)
        self.assertEqual({}, stub_failure_registry._conformance)
        socket_constructor = socket.socket
        for violation in (
            "bounded_output",
            "network",
            "network_caught",
            "source_time",
            "typed_output",
        ):
            with self.subTest(violation=violation):
                violation_registry = StrictLocalAdapterRegistry(
                    artifacts, verifications
                )
                with self.assertRaisesRegex(
                    AdapterRegistrationError, "probe failed"
                ):
                    violation_registry.register(
                        conforming_manifest,
                        InvariantBreakingSpeechAdapter(violation),
                    )
                self.assertEqual({}, violation_registry._manifests)
                self.assertEqual({}, violation_registry._conformance)
        self.assertIs(socket_constructor, socket.socket)

        signature_registry = StrictLocalAdapterRegistry(artifacts, verifications)
        with self.assertRaisesRegex(TypeError, "conformance_probe"):
            signature_registry.register(
                conforming_manifest,
                StubSpeechAdapter(),  # type: ignore[arg-type]
                conformance_probe=lambda _adapter: object(),  # type: ignore[call-arg]
            )
        self.assertEqual({}, signature_registry._manifests)
        self.assertEqual({}, signature_registry._conformance)

        preflight_manifest = replace(
            conforming_manifest, supported_stages=()
        )
        preflight_adapter = StubSpeechAdapter()
        preflight_adapter.generator_id = "adapter:other"
        incomplete_verifications = {
            weights.artifact_id: ArtifactVerification(
                weights.artifact_id, "c" * 64, 0
            )
        }
        preflight_registry = StrictLocalAdapterRegistry(
            artifacts, incomplete_verifications
        )
        missing_artifact_registry = StrictLocalAdapterRegistry(
            artifacts, verifications
        )
        with patch(
            "notewitness.application.adapter_registry.run_noscribe_conformance",
            side_effect=AssertionError("preflight must not run a conformance probe"),
        ) as probe:
            with self.assertRaises(AdapterRegistrationError) as artifact_failure:
                missing_artifact_registry.register(
                    replace(
                        conforming_manifest,
                        code_artifact_id="artifact:missing",
                    ),
                    StubSpeechAdapter(),  # type: ignore[arg-type]
                )
            with self.assertRaises(AdapterRegistrationError) as preflight_failure:
                preflight_registry.register(
                    preflight_manifest,
                    preflight_adapter,  # type: ignore[arg-type]
                )
            duplicate_adapter = StubSpeechAdapter()
            duplicate_adapter.generator_id = "adapter:other"
            with self.assertRaises(AdapterRegistrationError) as duplicate_failure:
                registry.register(
                    preflight_manifest,
                    duplicate_adapter,  # type: ignore[arg-type]
                )
        self.assertEqual(0, probe.call_count)
        self.assertEqual(
            "missing artifact 'artifact:missing'", str(artifact_failure.exception)
        )
        self.assertEqual(
            "adapter declares no supported analysis stages; "
            "artifact 'artifact:code' has no verification record; "
            "artifact 'artifact:weights' checksum is not verified; "
            "artifact 'artifact:weights' size is not verified; "
            "manifest does not declare stage 'speech_recognition'; "
            "adapter generator_id does not match its manifest ID",
            str(preflight_failure.exception),
        )
        self.assertEqual((), missing_artifact_registry.available_stages)
        self.assertEqual((), missing_artifact_registry.available_capability_ids)
        self.assertEqual({}, missing_artifact_registry._manifests)
        self.assertEqual({}, missing_artifact_registry._conformance)
        self.assertEqual((), preflight_registry.available_stages)
        self.assertEqual((), preflight_registry.available_capability_ids)
        self.assertEqual({}, preflight_registry._manifests)
        self.assertEqual({}, preflight_registry._conformance)
        self.assertEqual(
            "adapter declares no supported analysis stages; "
            "manifest does not declare stage 'speech_recognition'; "
            "adapter generator_id does not match its manifest ID; "
            "stage 'speech_recognition' already has an adapter",
            str(duplicate_failure.exception),
        )
        self.assertEqual(
            (AnalysisStage.SPEECH_RECOGNITION,), registry.available_stages
        )
        self.assertEqual(("local_asr",), registry.available_capability_ids)
        self.assertEqual(
            {AnalysisStage.SPEECH_RECOGNITION: manifest}, registry._manifests
        )
        self.assertEqual({}, registry._conformance)

    def test_runtime_components_require_allowlisted_successful_unique_probes(self) -> None:
        registry = RuntimeCapabilityRegistry()
        registry.register_component(
            "transcription_run_manifest", "manifest-store:test", lambda: True
        )

        self.assertEqual(
            ("transcription_run_manifest",), registry.available_capability_ids
        )
        with self.assertRaisesRegex(BackendRegistrationError, "already"):
            registry.register_component(
                "transcription_run_manifest", "manifest-store:other", lambda: True
            )
        with self.assertRaisesRegex(BackendRegistrationError, "not a local"):
            registry.register_component(
                "automated_grading", "unsafe:test", lambda: True
            )
        with self.assertRaisesRegex(BackendRegistrationError, "did not pass"):
            registry.register_component(
                "research_transcription_options", "options:test", lambda: False
            )

    def test_model_install_requires_unique_artifacts_license_and_confirmation(self) -> None:
        pending = ModelInstallPlan(
            artifact_ids=("artifact:code", "artifact:weights"),
            total_size_bytes=30,
            requires_network=True,
            licenses_presented=True,
            user_confirmed=False,
        )
        confirmed = ModelInstallPlan(
            artifact_ids=pending.artifact_ids,
            total_size_bytes=pending.total_size_bytes,
            requires_network=pending.requires_network,
            licenses_presented=True,
            user_confirmed=True,
        )

        self.assertFalse(pending.executable)
        self.assertTrue(confirmed.executable)
        with self.assertRaisesRegex(ValueError, "unique"):
            ModelInstallPlan(
                artifact_ids=("artifact:weights", "artifact:weights"),
                total_size_bytes=40,
                requires_network=True,
                licenses_presented=True,
                user_confirmed=True,
            )

    def test_export_blocks_unacknowledged_or_blocking_loss(self) -> None:
        blocking = ProjectionLoss(
            field="participant_identity",
            reason="destination cannot preserve the access restriction",
            severity=LossSeverity.BLOCKING,
            affected_record_ids=("actor:student",),
        )
        preflight = ExportPreflight(
            export_format=ExportFormat.EAF,
            destination="lesson.eaf",
            selected_record_ids=("event:instruction",),
            rights_authorized=True,
            losses=(blocking,),
            loss_preview_acknowledged=True,
        )

        self.assertFalse(preflight.executable)
        with self.assertRaisesRegex(ValueError, "booleans"):
            ExportPreflight(
                export_format=ExportFormat.WEBVTT,
                destination="lesson.vtt",
                selected_record_ids=("event:instruction",),
                rights_authorized="false",  # type: ignore[arg-type]
                losses=(),
                loss_preview_acknowledged="false",  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(ValueError, "selected"):
            ExportPreflight(
                export_format=ExportFormat.WEBVTT,
                destination="lesson.vtt",
                selected_record_ids=(),
                rights_authorized=True,
                losses=(),
                loss_preview_acknowledged=True,
            )

    def test_unknown_annotation_package_cannot_be_silently_authorized(self) -> None:
        with self.assertRaises(ValueError):
            PackageImportDecision(
                state=PackageTrustState.AUTHORIZED,
                mapped_role=None,
                reasons=(),
            )

    def test_benchmark_failures_remain_first_class_observations(self) -> None:
        observation = BenchmarkObservation(
            case_id="case:1",
            metric_id="metric:onset-f1",
            outcome=BenchmarkOutcome.UNSUPPORTED,
            value=None,
            failure_reason="polyphonic attribution unsupported",
        )

        self.assertEqual(BenchmarkOutcome.UNSUPPORTED, observation.outcome)
        with self.assertRaises(ValueError):
            BenchmarkObservation(
                case_id="case:2",
                metric_id="metric:onset-f1",
                outcome=BenchmarkOutcome.FAILED,
                value=None,
            )


if __name__ == "__main__":
    unittest.main()
