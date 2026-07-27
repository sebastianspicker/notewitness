from __future__ import annotations

from dataclasses import replace
import json
import unittest

from notewitness.domain.timeline import MediaSpan
from notewitness.domain.transcription import (
    CanonicalTranscriptEvidence,
    DetectedLanguage,
    DiarizationMode,
    DisfluencyPolicy,
    LanguageMode,
    ResolvedModelProfile,
    ResolvedRunArtifact,
    SourceChecksum,
    SpeakerCorrection,
    SpeakerCorrectionKind,
    SpeakerResultSpanAssignment,
    TranscriptExportFormat,
    TranscriptReplacementPreview,
    TranscriptionJobSpec,
    TranscriptionQueueItem,
    TranscriptionQueuePlan,
    TranscriptionRunManifest,
    TranscriptionRunLedger,
    TranscriptionRunState,
    transcript_export_losses,
    transcript_export_preflight,
    transcription_settings_sha256,
)


def job_spec(**overrides: object) -> TranscriptionJobSpec:
    values: dict[str, object] = {
        "job_id": "job:transcription",
        "spans": (MediaSpan("source:lesson", "audio", 0, 60_000_000),),
        "model_profile_id": "profile:precise",
        "language_mode": LanguageMode.FIXED,
        "requested_language": "de",
        "diarization_mode": DiarizationMode.EXACT,
        "exact_speaker_count": 2,
        "detect_overlap": True,
        "disfluency_policy": DisfluencyPolicy.INCLUDE,
        "pause_threshold_ms": 1_000,
        "visible_timestamps": True,
        "timestamp_interval_ms": 60_000,
        "output_format": TranscriptExportFormat.HTML,
        "model_vocabulary_artifact_id": "artifact:model-vocabulary",
        "adapter_prompt_artifact_id": "artifact:adapter-prompt",
        "project_lexicon_id": "lexicon:lesson",
    }
    values.update(overrides)
    return TranscriptionJobSpec(**values)  # type: ignore[arg-type]


class ResearchTranscriptionContractTests(unittest.TestCase):
    def test_exact_diarization_is_limited_to_one_through_ten(self) -> None:
        for invalid in (0, 11, True):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    job_spec(exact_speaker_count=invalid)

    def test_language_request_and_resolution_are_separate(self) -> None:
        with self.assertRaises(ValueError):
            job_spec(language_mode=LanguageMode.FIXED, requested_language=None)
        automatic = job_spec(
            language_mode=LanguageMode.AUTO,
            requested_language=None,
            diarization_mode=DiarizationMode.AUTO,
            exact_speaker_count=None,
        )
        detected = DetectedLanguage("de", 0.91, automatic.spans[0])

        self.assertEqual(LanguageMode.AUTO, automatic.language_mode)
        self.assertEqual("de", detected.language_code)
        self.assertEqual(0.91, detected.probability)

        unknown_probability = DetectedLanguage("de", None, automatic.spans[0])
        self.assertIsNone(unknown_probability.probability)

    def test_vtt_reports_rendering_loss_without_mutating_job(self) -> None:
        job = job_spec(output_format=TranscriptExportFormat.WEBVTT)

        losses = transcript_export_losses(job)

        self.assertEqual(
            {"pause_threshold_ms", "detect_overlap", "visible_timestamps"},
            {loss.field for loss in losses},
        )
        self.assertTrue(job.detect_overlap)
        self.assertEqual(1_000, job.pause_threshold_ms)

    def test_persisted_raw_values_cannot_bypass_typed_option_checks(self) -> None:
        for field_name, value in (
            ("language_mode", "fixed"),
            ("diarization_mode", "exact"),
            ("disfluency_policy", "include"),
            ("output_format", "webvtt"),
            ("detect_overlap", "false"),
            ("visible_timestamps", 1),
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, field_name):
                    job_spec(**{field_name: value})

    def test_model_vocabulary_prompt_and_project_lexicon_are_distinct(self) -> None:
        job = job_spec()

        self.assertNotEqual(
            job.model_vocabulary_artifact_id, job.adapter_prompt_artifact_id
        )
        self.assertNotEqual(job.adapter_prompt_artifact_id, job.project_lexicon_id)

    def test_settings_are_deeply_immutable_and_json_serializable(self) -> None:
        original = {
            "beam_size": 5,
            "vad": {"threshold": 0.35},
            "languages": ["de", "en"],
        }
        job = job_spec(adapter_settings=original)
        original["beam_size"] = 10
        original["vad"]["threshold"] = 0.9  # type: ignore[index]

        self.assertEqual(5, job.adapter_settings["beam_size"])
        self.assertEqual(0.35, job.adapter_settings["vad"]["threshold"])
        with self.assertRaises(TypeError):
            job.adapter_settings["beam_size"] = 7  # type: ignore[index]
        with self.assertRaises(TypeError):
            job.adapter_settings["vad"]["threshold"] = 0.5
        json.dumps(job.as_dict(), allow_nan=False, sort_keys=True)
        with self.assertRaisesRegex(ValueError, "beam_size"):
            job_spec(adapter_settings={"beam_size": 0})
        with self.assertRaisesRegex(ValueError, "vad_threshold"):
            job_spec(adapter_settings={"vad_threshold": 1.5})

    def test_sequential_queue_is_bounded_and_has_unique_jobs(self) -> None:
        first = job_spec()
        second = job_spec(job_id="job:second")

        queue = TranscriptionQueuePlan(
            "queue:one",
            (
                TranscriptionQueueItem(first, "first.html"),
                TranscriptionQueueItem(second, "second.html"),
            ),
        )

        self.assertEqual((first, second), queue.jobs)
        self.assertEqual("exports/transcripts", queue.output_root)
        with self.assertRaisesRegex(ValueError, "unique"):
            TranscriptionQueuePlan(
                "queue:duplicate",
                (
                    TranscriptionQueueItem(first, "first.html"),
                    TranscriptionQueueItem(first, "second.html"),
                ),
            )
        with self.assertRaisesRegex(ValueError, "output names"):
            TranscriptionQueuePlan(
                "queue:case-collision",
                (
                    TranscriptionQueueItem(first, "Lesson.html"),
                    TranscriptionQueueItem(second, "lesson.html"),
                ),
            )
        with self.assertRaisesRegex(ValueError, "output_root"):
            TranscriptionQueuePlan(
                "queue:unsafe-root",
                (TranscriptionQueueItem(first, "first.html"),),
                output_root="../outside",
            )
        with self.assertRaisesRegex(ValueError, "only one source"):
            job_spec(
                spans=(
                    first.spans[0],
                    MediaSpan("source:other", "audio", 0, 1_000),
                )
            )

    def test_run_manifest_snapshots_exact_artifacts_and_recovery_lineage(self) -> None:
        job = job_spec()
        code = ResolvedRunArtifact(
            "artifact:code", "a" * 64, 1_024, "AGPL-3.0-or-later"
        )
        model = ResolvedRunArtifact(
            "artifact:model", "b" * 64, 2_048, "LicenseRef-model"
        )
        effective_settings = {"beam_size": 5, "compute_type": "int8"}
        profile = ResolvedModelProfile(
            profile_id="profile:precise@artifact:model",
            requested_profile_id=job.model_profile_id,
            adapter_id="adapter:local-asr",
            model_artifact_ids=(model.artifact_id,),
            effective_settings_sha256=transcription_settings_sha256(
                effective_settings
            ),
        )
        manifest = TranscriptionRunManifest(
            run_id="run:retry",
            job=job,
            adapter_id="adapter:local-asr",
            adapter_version="1.2.3",
            resolved_model_profile=profile,
            code_artifact=code,
            model_artifacts=(model,),
            source_checksums=(SourceChecksum("source:lesson", "c" * 64),),
            detected_languages=(DetectedLanguage("de", 0.91, job.spans[0]),),
            effective_settings=effective_settings,
            runtime_fingerprint_sha256="d" * 64,
            state=TranscriptionRunState.CANCELLED,
            partial=True,
            started_at="2026-07-18T10:00:00+00:00",
            finished_at="2026-07-18T10:01:00+00:00",
            retry_parent_run_id="run:first",
        )

        self.assertEqual("artifact:code", manifest.code_artifact_id)
        self.assertEqual(("artifact:model",), manifest.model_artifact_ids)
        self.assertEqual("profile:precise", manifest.job.model_profile_id)
        self.assertEqual(
            "profile:precise@artifact:model", manifest.resolved_model_profile_id
        )
        self.assertTrue(manifest.partial)
        with self.assertRaises(TypeError):
            manifest.effective_settings["beam_size"] = 10  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "failure code"):
            TranscriptionRunManifest(
                run_id="run:failed",
                job=job,
                adapter_id="adapter:local-asr",
                adapter_version="1.2.3",
                resolved_model_profile=ResolvedModelProfile(
                    profile_id="profile:precise@artifact:model",
                    requested_profile_id=job.model_profile_id,
                    adapter_id="adapter:local-asr",
                    model_artifact_ids=(model.artifact_id,),
                    effective_settings_sha256=transcription_settings_sha256({}),
                ),
                code_artifact=code,
                model_artifacts=(model,),
                source_checksums=(SourceChecksum("source:lesson", "c" * 64),),
                detected_languages=(),
                effective_settings={},
                runtime_fingerprint_sha256="d" * 64,
                state=TranscriptionRunState.FAILED,
                partial=False,
            )

    def test_manifest_rejects_secrets_and_out_of_span_language_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "secret settings"):
            job_spec(adapter_settings={"api_token": "must-not-enter-manifest"})

        job = job_spec()
        with self.assertRaisesRegex(ValueError, "requested input span"):
            TranscriptionRunManifest(
                run_id="run:bad-language-span",
                job=job,
                adapter_id="adapter:local-asr",
                adapter_version="1",
                resolved_model_profile=ResolvedModelProfile(
                    profile_id="profile:precise@artifact:model",
                    requested_profile_id=job.model_profile_id,
                    adapter_id="adapter:local-asr",
                    model_artifact_ids=("artifact:model",),
                    effective_settings_sha256=transcription_settings_sha256({}),
                ),
                code_artifact=ResolvedRunArtifact(
                    "artifact:code", "a" * 64, 1, "AGPL-3.0-or-later"
                ),
                model_artifacts=(
                    ResolvedRunArtifact(
                        "artifact:model", "b" * 64, 2, "LicenseRef-model"
                    ),
                ),
                source_checksums=(SourceChecksum("source:lesson", "c" * 64),),
                detected_languages=(
                    DetectedLanguage(
                        "de",
                        0.5,
                        MediaSpan("source:lesson", "audio", 70_000_000, 1_000),
                    ),
                ),
                effective_settings={},
                runtime_fingerprint_sha256="d" * 64,
                state=TranscriptionRunState.QUEUED,
                partial=False,
            )

    def test_run_ledger_requires_acyclic_immutable_retry_ancestry(self) -> None:
        job = job_spec()
        code = ResolvedRunArtifact(
            "artifact:code", "a" * 64, 1_024, "AGPL-3.0-or-later"
        )
        model = ResolvedRunArtifact(
            "artifact:model", "b" * 64, 2_048, "LicenseRef-model"
        )
        profile = ResolvedModelProfile(
            profile_id="profile:precise@artifact:model",
            requested_profile_id=job.model_profile_id,
            adapter_id="adapter:local-asr",
            model_artifact_ids=(model.artifact_id,),
            effective_settings_sha256=transcription_settings_sha256({}),
        )
        root = TranscriptionRunManifest(
            run_id="run:root",
            job=job,
            adapter_id="adapter:local-asr",
            adapter_version="1",
            resolved_model_profile=profile,
            code_artifact=code,
            model_artifacts=(model,),
            source_checksums=(SourceChecksum("source:lesson", "c" * 64),),
            detected_languages=(),
            effective_settings={},
            runtime_fingerprint_sha256="d" * 64,
            state=TranscriptionRunState.QUEUED,
            partial=False,
        )
        retry = replace(root, run_id="run:retry", retry_parent_run_id=root.run_id)

        ledger = TranscriptionRunLedger((retry, root))

        self.assertEqual(2, len(ledger.manifests))
        with self.assertRaisesRegex(ValueError, "parents must exist"):
            TranscriptionRunLedger(
                (replace(retry, retry_parent_run_id="run:missing"),)
            )
        with self.assertRaisesRegex(ValueError, "acyclic"):
            TranscriptionRunLedger(
                (
                    replace(root, retry_parent_run_id="run:retry"),
                    retry,
                )
            )
        with self.assertRaisesRegex(ValueError, "preserve"):
            TranscriptionRunLedger(
                (root, replace(retry, job=job_spec(job_id="job:other")))
            )
        with self.assertRaisesRegex(ValueError, "runtime fingerprint"):
            TranscriptionRunLedger(
                (
                    root,
                    replace(retry, runtime_fingerprint_sha256="e" * 64),
                )
            )
        with self.assertRaisesRegex(ValueError, "active"):
            replace(root, state=TranscriptionRunState.TRANSCRIBING)
        with self.assertRaisesRegex(ValueError, "requested profile"):
            replace(
                root,
                resolved_model_profile=replace(
                    profile, requested_profile_id="profile:fast"
                ),
            )

    def test_raw_evidence_and_human_edits_remain_separate_records(self) -> None:
        evidence = CanonicalTranscriptEvidence(
            evidence_id="transcript-evidence:one",
            run_id="run:one",
            raw_response_artifact_id="artifact:raw-output",
            raw_response_sha256="d" * 64,
            raw_response_size_bytes=100,
            normalized_transcript_artifact_id="artifact:normalized-output",
            normalized_transcript_sha256="e" * 64,
            normalized_transcript_size_bytes=80,
            normalizer_id="normalizer:one",
            segment_hypothesis_ids=("segment:one",),
            word_hypothesis_ids=("word:one",),
            speaker_hypothesis_ids=("speaker:one",),
            overlap_event_ids=("event:overlap",),
            silence_event_ids=("event:silence",),
            partial=False,
        )
        preview = TranscriptReplacementPreview(
            preview_id="preview:one",
            query="C sharp",
            replacement_text="C♯",
            matched_word_ids=("word:one",),
            case_sensitive=False,
        )
        reviewed_span = MediaSpan("source:lesson", "audio", 0, 1_000_000)
        correction = SpeakerCorrection(
            correction_id="correction:speaker",
            kind=SpeakerCorrectionKind.MERGE,
            cluster_ids=("speaker:one", "speaker:two"),
            result_cluster_ids=("speaker:merged",),
            actor_id="actor:teacher",
            spans=(reviewed_span,),
            result_span_assignments=(
                SpeakerResultSpanAssignment(
                    "speaker:merged", (reviewed_span,)
                ),
            ),
            author_id="actor:researcher",
            reason="Reviewed as one speaker",
            parent_revision_ids=("revision:before-speaker-merge",),
            created_at="2026-07-18T10:02:00+00:00",
        )

        self.assertEqual("artifact:raw-output", evidence.raw_response_artifact_id)
        self.assertEqual(("word:one",), preview.matched_word_ids)
        self.assertEqual(SpeakerCorrectionKind.MERGE, correction.kind)
        with self.assertRaisesRegex(ValueError, "result cluster"):
            replace(correction, result_cluster_ids=())

    def test_speaker_split_assignments_are_replayable_and_unambiguous(self) -> None:
        first = MediaSpan("source:lesson", "audio", 0, 1_000_000)
        second = MediaSpan("source:lesson", "audio", 1_000_000, 1_000_000)
        correction = SpeakerCorrection(
            correction_id="correction:split",
            kind=SpeakerCorrectionKind.SPLIT,
            cluster_ids=("speaker:source",),
            result_cluster_ids=("speaker:teacher", "speaker:student"),
            actor_id=None,
            spans=(first, second),
            result_span_assignments=(
                SpeakerResultSpanAssignment("speaker:teacher", (first,)),
                SpeakerResultSpanAssignment("speaker:student", (second,)),
            ),
            author_id="actor:researcher",
            reason="Audibly distinct speakers",
            parent_revision_ids=("revision:before-split",),
            created_at="2026-07-18T10:02:00+00:00",
        )

        replay = {
            assignment.result_cluster_id: assignment.spans
            for assignment in correction.result_span_assignments
        }
        self.assertEqual((first,), replay["speaker:teacher"])
        self.assertEqual((second,), replay["speaker:student"])
        with self.assertRaisesRegex(ValueError, "exactly result cluster"):
            replace(
                correction,
                result_span_assignments=(correction.result_span_assignments[0],),
            )
        with self.assertRaisesRegex(ValueError, "exactly match"):
            replace(
                correction,
                result_span_assignments=(
                    SpeakerResultSpanAssignment("speaker:teacher", (first,)),
                    SpeakerResultSpanAssignment(
                        "speaker:student",
                        (MediaSpan("source:lesson", "audio", 3_000_000, 1_000),),
                    ),
                ),
            )

    def test_export_preflight_uses_evidence_not_only_detector_settings(self) -> None:
        job = job_spec(
            output_format=TranscriptExportFormat.WEBVTT,
            detect_overlap=False,
            pause_threshold_ms=None,
            visible_timestamps=False,
        )
        evidence = CanonicalTranscriptEvidence(
            evidence_id="transcript-evidence:manual-overlap",
            run_id="run:one",
            raw_response_artifact_id="artifact:raw-output",
            raw_response_sha256="d" * 64,
            raw_response_size_bytes=100,
            normalized_transcript_artifact_id="artifact:normalized-output",
            normalized_transcript_sha256="e" * 64,
            normalized_transcript_size_bytes=80,
            normalizer_id="normalizer:one",
            segment_hypothesis_ids=(),
            word_hypothesis_ids=(),
            speaker_hypothesis_ids=(),
            overlap_event_ids=("event:manual-overlap",),
            silence_event_ids=(),
            partial=False,
        )
        code = ResolvedRunArtifact(
            "artifact:code", "a" * 64, 1_024, "AGPL-3.0-or-later"
        )
        model = ResolvedRunArtifact(
            "artifact:model", "b" * 64, 2_048, "LicenseRef-model"
        )
        manifest = TranscriptionRunManifest(
            run_id="run:one",
            job=job,
            adapter_id="adapter:local-asr",
            adapter_version="1",
            resolved_model_profile=ResolvedModelProfile(
                profile_id="profile:precise@artifact:model",
                requested_profile_id=job.model_profile_id,
                adapter_id="adapter:local-asr",
                model_artifact_ids=(model.artifact_id,),
                effective_settings_sha256=transcription_settings_sha256({}),
            ),
            code_artifact=code,
            model_artifacts=(model,),
            source_checksums=(SourceChecksum("source:lesson", "c" * 64),),
            detected_languages=(),
            effective_settings={},
            runtime_fingerprint_sha256="d" * 64,
            state=TranscriptionRunState.QUEUED,
            partial=False,
        )

        blocked = transcript_export_preflight(
            manifest,
            evidence,
            destination="lesson.vtt",
            selected_record_ids=("event:manual-overlap",),
            rights_authorized=False,
            loss_preview_acknowledged=False,
        )
        allowed = transcript_export_preflight(
            manifest,
            evidence,
            destination="lesson.vtt",
            selected_record_ids=("event:manual-overlap",),
            rights_authorized=True,
            loss_preview_acknowledged=True,
        )

        self.assertEqual(("detect_overlap",), tuple(loss.field for loss in blocked.losses))
        self.assertEqual(
            ("event:manual-overlap",), blocked.losses[0].affected_record_ids
        )
        self.assertFalse(blocked.executable)
        self.assertTrue(allowed.executable)
        with self.assertRaisesRegex(ValueError, "canonical evidence"):
            transcript_export_preflight(
                manifest,
                evidence,
                destination="lesson.vtt",
                selected_record_ids=("event:not-selected-evidence",),
                rights_authorized=True,
                loss_preview_acknowledged=True,
            )
        with self.assertRaisesRegex(ValueError, "booleans"):
            transcript_export_preflight(
                manifest,
                evidence,
                destination="lesson.vtt",
                selected_record_ids=("event:manual-overlap",),
                rights_authorized="false",  # type: ignore[arg-type]
                loss_preview_acknowledged="false",  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(ValueError, "exported run"):
            transcript_export_preflight(
                replace(manifest, run_id="run:unrelated"),
                evidence,
                destination="lesson.vtt",
                selected_record_ids=("event:manual-overlap",),
                rights_authorized=True,
                loss_preview_acknowledged=True,
            )


if __name__ == "__main__":
    unittest.main()
