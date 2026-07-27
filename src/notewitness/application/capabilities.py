"""Single truthful inventory of the executable alpha's capabilities."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Protocol


class CapabilityLevel(StrEnum):
    AVAILABLE = "available"
    PROJECTION_AVAILABLE = "projection_available"
    CONTRACT_READY = "contract_ready"
    RESEARCH_GATE = "research_gate"
    EXCLUDED = "excluded"


@dataclass(frozen=True, slots=True)
class Capability:
    capability_id: str
    area: str
    phase: str
    level: CapabilityLevel
    local_first: bool
    summary: str
    code_surface: str
    limitation: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _capability(
    capability_id: str,
    area: str,
    phase: str,
    level: CapabilityLevel,
    summary: str,
    code_surface: str,
    limitation: str | None = None,
    *,
    local_first: bool = True,
) -> Capability:
    return Capability(
        capability_id=capability_id,
        area=area,
        phase=phase,
        level=level,
        local_first=local_first,
        summary=summary,
        code_surface=code_surface,
        limitation=limitation,
    )


CAPABILITIES = (
    _capability(
        "private_project_init", "foundation", "v0.1", CapabilityLevel.AVAILABLE,
        "Create owner-only offline project storage.", "notewitness.project",
    ),
    _capability(
        "evidence_graph", "foundation", "v0.1", CapabilityLevel.AVAILABLE,
        "Validate events, targets, relations, rights, provenance, and revisions.",
        "notewitness.evidence",
    ),
    _capability(
        "lesson_notes_artifact", "tonic_parity", "v0.1", CapabilityLevel.PROJECTION_AVAILABLE,
        "Project a private transcript, evidence summary, tasks, and exact-time bookmarks.",
        "notewitness.application.lesson_notes",
        "Projects existing graph evidence; automatic engines are separate capabilities.",
    ),
    _capability(
        "one_action_capture", "tonic_parity", "v0.1", CapabilityLevel.AVAILABLE,
        "Record in the browser and ingest an owner-private checksum-addressed source.",
        "notewitness.presentation.workbench_server",
        "Requires browser MediaRecorder support and explicit microphone permission.",
    ),
    _capability(
        "speech_music_timestamps", "tonic_parity", "v0.1", CapabilityLevel.PROJECTION_AVAILABLE,
        "Expose correctable speech, music, humming, overlap, silence, and other spans.",
        "notewitness.domain.lesson.ActivitySegment",
        "A PANNs speech/music bridge is available; humming and silence require another explicit "
        "engine, and every model still requires corpus evaluation.",
    ),
    _capability(
        "speaker_transcript", "tonic_parity", "v0.1", CapabilityLevel.PROJECTION_AVAILABLE,
        "Project role-attributed transcript turns linked to exact source spans.",
        "notewitness.domain.lesson.TranscriptTurn",
        "Automatic clusters remain anonymous until a human confirms a project actor.",
    ),
    _capability(
        "full_music_transcript", "tonic_parity", "v0.1", CapabilityLevel.PROJECTION_AVAILABLE,
        "Project speech, music, humming, overlap, note, and pitch events in one chronology.",
        "notewitness.domain.lesson.FullTranscriptEntry",
        "Automatic note and pitch entries appear only after a local adapter emits them.",
    ),
    _capability(
        "evidence_summary", "tonic_parity", "v0.1", CapabilityLevel.PROJECTION_AVAILABLE,
        "Summarize only explicit relations, score spans, and review states.",
        "notewitness.domain.lesson.LessonSummary",
        "No generative narrative is fabricated when evidence is absent.",
    ),
    _capability(
        "practice_checklist", "tonic_parity", "v0.1", CapabilityLevel.PROJECTION_AVAILABLE,
        "Project explicit task relations into source-linked checklist items.",
        "notewitness.domain.lesson.PracticeTask",
    ),
    _capability(
        "practice_plan", "tonic_parity", "v0.1", CapabilityLevel.PROJECTION_AVAILABLE,
        "Order explicit assignment evidence into a human-confirmed local practice plan.",
        "notewitness.domain.lesson.PracticePlan",
        "Durations and repetitions are not invented when the lesson did not specify them.",
    ),
    _capability(
        "lesson_statistics", "tonic_parity", "v0.1", CapabilityLevel.AVAILABLE,
        "Compute descriptive activity, transcript, bookmark, relation, and task statistics.",
        "notewitness.domain.lesson.LessonStatistics",
        "Statistics explicitly exclude grades and teaching- or performance-quality scores.",
    ),
    _capability(
        "source_time_playback", "tonic_parity", "v0.1", CapabilityLevel.PROJECTION_AVAILABLE,
        "Resolve every note, turn, task, and relation view to canonical media time.",
        "notewitness.domain.timeline.EvidenceAnchor",
        "A desktop playback engine remains a presentation integration.",
    ),
    _capability(
        "local_playback_backend", "tonic_parity", "v0.1", CapabilityLevel.AVAILABLE,
        "Play and seek private project media through bounded loopback byte ranges.",
        "notewitness.presentation.workbench_server",
        "Codec support depends on the local browser; arbitrary filesystem paths are refused.",
    ),
    _capability(
        "lesson_history", "tonic_parity", "v0.2", CapabilityLevel.CONTRACT_READY,
        "Index authorized local lessons and resolve longitudinal neighbors.",
        "notewitness.application.ports.LessonCatalogPort",
        "No durable catalog has been chosen before the SQLite migration.",
    ),
    _capability(
        "attempt_comparison", "music_evidence", "v0.2", CapabilityLevel.CONTRACT_READY,
        "Represent repeats and revisions for synchronized A/B review.",
        "notewitness.domain.lesson.LessonProgress",
        "Signal-level alignment and audition UI are not implemented.",
    ),
    _capability(
        "local_media_ingest", "analysis", "v0.1", CapabilityLevel.AVAILABLE,
        "Copy and register immutable checksum-addressed media under explicit rights.",
        "notewitness.media_ingest.ingest_media",
        "The operator-facing path requires a locally discovered ffprobe executable.",
    ),
    _capability(
        "explicit_local_whisper_adapter",
        "analysis",
        "v0.1",
        CapabilityLevel.AVAILABLE,
        "Run a bounded local Whisper CLI with an explicit checkpoint and no network.",
        "notewitness.adapters.whisper_cli.WhisperCLIAdapter",
        "Execution remains conditional on local tools, a checkpoint, and declared licenses.",
    ),
    _capability(
        "activity_segmentation", "analysis", "v0.1", CapabilityLevel.CONTRACT_READY,
        "Classify coupled speech/music/humming/overlap spans without destructive separation.",
        "notewitness.domain.analysis.AnalysisStage.ACTIVITY_SEGMENTATION",
        "The packaged PANNs bridge emits speech, music, and overlap; no evaluated model is bundled.",
    ),
    _capability(
        "local_asr", "analysis", "v0.1", CapabilityLevel.CONTRACT_READY,
        "Emit word-timed speech hypotheses using an explicit local adapter.",
        "notewitness.domain.analysis.AnalysisStage.SPEECH_RECOGNITION",
        "The Whisper path is implemented; executable, checkpoint, and licenses are external.",
    ),
    _capability(
        "anonymous_diarization", "analysis", "v0.1", CapabilityLevel.CONTRACT_READY,
        "Emit project-local anonymous speaker clusters for manual role confirmation.",
        "notewitness.domain.analysis.AnalysisStage.ANONYMOUS_DIARIZATION",
        "Supports off, automatic, exact 1-10, and overlap requests through an explicit engine; "
        "persistent voice identity is excluded.",
    ),
    _capability(
        "local_lesson_digest", "analysis", "v0.1", CapabilityLevel.AVAILABLE,
        "Propose conservative evidence-linked explicit practice assignments locally.",
        "notewitness.application.pedagogical_digest.suggest_practice_relations",
        "The deterministic prefix rules do not summarize or infer learner state; suggestions "
        "require transcript and relation review before entering practice views.",
    ),
    _capability(
        "note_detection", "music_evidence", "v0.1", CapabilityLevel.CONTRACT_READY,
        "Represent continuous-time note hypotheses with provenance and uncertainty.",
        "notewitness.domain.analysis.NoteHypothesis",
        "Requires an explicit, separately evaluated local analysis executable and model.",
    ),
    _capability(
        "instrument_detection", "music_evidence", "v0.1", CapabilityLevel.CONTRACT_READY,
        "Represent time-bounded instrument hypotheses with optional, reviewable attribution.",
        "notewitness.domain.analysis.InstrumentHypothesis",
        "Requires an evaluated local engine; performer identity is never inferred from audio.",
    ),
    _capability(
        "instrument_diarization",
        "music_evidence",
        "v0.2",
        CapabilityLevel.AVAILABLE,
        "Represent time-bounded instrument activity with anonymous run-local track IDs.",
        "notewitness.domain.analysis.AnalysisStage.INSTRUMENT_DIARIZATION",
        "Class-activity tracks do not prove performer identity or separate simultaneous "
        "instances of the same instrument class.",
    ),
    _capability(
        "continuous_pitch", "music_evidence", "v0.2", CapabilityLevel.CONTRACT_READY,
        "Represent pitch contours where quantized notation would mislead.",
        "notewitness.domain.analysis.AnalysisStage.CONTINUOUS_PITCH",
        "The adapter path is implemented; estimator choice and corpus validation are external.",
    ),
    _capability(
        "onset_beat_chord", "music_evidence", "v0.2", CapabilityLevel.CONTRACT_READY,
        "Route optional onset, beat, and chord evidence only when needed.",
        "notewitness.domain.analysis.AnalysisStage.ONSET_BEAT_CHORD",
    ),
    _capability(
        "score_alignment", "music_evidence", "v0.2", CapabilityLevel.CONTRACT_READY,
        "Expose aligned, unknown, failed, and not-alignable score mappings.",
        "notewitness.domain.analysis.AnalysisStage.SCORE_ALIGNMENT",
        "Requires an explicit local score, engine, model, and separate license records.",
    ),
    _capability(
        "explicit_local_analysis_adapter",
        "runtime",
        "v0.1",
        CapabilityLevel.AVAILABLE,
        "Run bounded JSON-speaking diarization and music engines with network denied.",
        "notewitness.adapters.analysis_cli.LocalAnalysisCLIAdapter",
        "Tools, models, scores, versions, and licenses must be supplied explicitly.",
    ),
    _capability(
        "bounded_resumable_jobs", "runtime", "v0.1", CapabilityLevel.AVAILABLE,
        "Persist leases, cancellation, checkpoints, raw replay, and atomic publication.",
        "notewitness.application.resumable_analysis",
        "The prototype runs local workers from the CLI; no background daemon is installed.",
    ),
    _capability(
        "research_transcription_options",
        "noscribe_parity",
        "v0.1",
        CapabilityLevel.CONTRACT_READY,
        "Snapshot source ranges, model profile, language, diarization, "
        "overlap, pauses, disfluencies, timestamps, and output.",
        "notewitness.domain.transcription.TranscriptionJobSpec",
    ),
    _capability(
        "transcription_language_modes", "noscribe_parity", "v0.1", CapabilityLevel.CONTRACT_READY,
        "Distinguish fixed, automatic, and multilingual requests from detected language evidence.",
        "notewitness.domain.transcription.LanguageMode",
    ),
    _capability(
        "transcription_speaker_options", "noscribe_parity", "v0.1", CapabilityLevel.CONTRACT_READY,
        "Support diarization off, automatic, or exact 1-10 speakers plus optional overlap.",
        "notewitness.domain.transcription.DiarizationMode",
    ),
    _capability(
        "noscribe_asr_conformance",
        "noscribe_parity",
        "v0.1",
        CapabilityLevel.CONTRACT_READY,
        "Require an ASR adapter probe for timestamps, confidence, languages, "
        "profiles, and disfluencies.",
        "notewitness.application.adapter_registry",
        "A generic speech-recognition stage does not satisfy this capability.",
    ),
    _capability(
        "noscribe_diarization_conformance",
        "noscribe_parity",
        "v0.1",
        CapabilityLevel.CONTRACT_READY,
        "Require a diarization adapter probe for off, auto, exact-count, "
        "anonymous, and overlap modes.",
        "notewitness.application.adapter_registry",
        "A generic diarization stage does not satisfy this capability.",
    ),
    _capability(
        "transcript_correction_workspace",
        "noscribe_parity",
        "v0.1",
        CapabilityLevel.AVAILABLE,
        "Review, attribute, accept, and revise transcript or music evidence graphically.",
        "notewitness.application.workbench",
        "Saved edits are append-only; unfinished browser drafts are not persisted.",
    ),
    _capability(
        "transcription_run_manifest",
        "noscribe_parity",
        "v0.1",
        CapabilityLevel.AVAILABLE,
        "Validate source checksums, exact artifacts, effective settings, "
        "language probabilities, partial state, and retry lineage.",
        "notewitness.application.transcription_runtime.LocalTranscriptionRuntime",
        "The prototype writes completed-run manifests; resumable retry storage is separate.",
    ),
    _capability(
        "html_text_vtt_transcript_exports",
        "noscribe_parity",
        "v0.1",
        CapabilityLevel.AVAILABLE,
        "Preflight HTML, text, and WebVTT output through rights and deterministic loss gates.",
        "notewitness.application.transcript_export",
        "Writers are local and tested; wider qualitative-tool interchange remains future work.",
    ),
    _capability(
        "project_domain_lexicon", "research_extension", "v0.1", CapabilityLevel.CONTRACT_READY,
        "Keep project terminology separate from model tokenizers and adapter prompts.",
        "notewitness.domain.transcription.ProjectLexicon",
        "This is a NoteWitness extension, not a noScribe parity claim.",
    ),
    _capability(
        "synchronized_timeline", "presentation", "v0.1", CapabilityLevel.PROJECTION_AVAILABLE,
        "Present source, activity, transcript, music, episode, and research lanes.",
        "notewitness.presentation.timeline",
        "The browser workbench renders the projection; it is not a native desktop shell.",
    ),
    _capability(
        "graphical_workbench", "presentation", "v0.1", CapabilityLevel.AVAILABLE,
        "Serve a loopback-only lesson editor, overview, timeline, transport, and tools.",
        "notewitness.presentation.workbench_server",
        "Requires a modern local browser; remote access and multi-user serving are excluded.",
    ),
    _capability(
        "human_review", "research", "v0.1", CapabilityLevel.AVAILABLE,
        "Keep machine suggestions separate from append-only human acceptance and correction.",
        "notewitness.application.transcript_review_service",
        "The graphical workbench covers acceptance and revision; contestation is future work.",
    ),
    _capability(
        "codebooks_cases_memos", "research", "v0.3", CapabilityLevel.CONTRACT_READY,
        "Represent controlled codes, cases, memos, and evidence queries separately.",
        "notewitness.domain.research",
    ),
    _capability(
        "annotation_exchange", "research", "v0.3", CapabilityLevel.CONTRACT_READY,
        "Quarantine unknown packages and preserve divergent revision parents.",
        "notewitness.domain.governance.AnnotationPackageManifest",
    ),
    _capability(
        "participant_requests", "governance", "v0.3", CapabilityLevel.CONTRACT_READY,
        "Track access, correction, withdrawal, and erasure with verified scope.",
        "notewitness.domain.governance.ParticipantRequest",
    ),
    _capability(
        "deletion_impact", "governance", "v0.3", CapabilityLevel.CONTRACT_READY,
        "Require a human-readable, authorized impact plan before hard deletion.",
        "notewitness.domain.governance.DeletionImpactPlan",
    ),
    _capability(
        "model_ledger", "models", "v0.1", CapabilityLevel.CONTRACT_READY,
        "Track code and weights separately with hashes, licenses, size, and network use.",
        "notewitness.domain.models",
    ),
    _capability(
        "strict_local_model_runtime", "models", "v0.1", CapabilityLevel.CONTRACT_READY,
        "Require installed analysis workers whose runtime network requirement is none.",
        "notewitness.domain.models.NetworkRequirement.NONE",
        "No ASR, diarization, MIR, or instrument model is bundled.",
    ),
    _capability(
        "explicit_model_install", "models", "v0.1", CapabilityLevel.CONTRACT_READY,
        "Require license presentation and user confirmation before model installation.",
        "notewitness.domain.models.ModelInstallPlan",
        "No installer downloads artifacts in this alpha.",
    ),
    _capability(
        "loss_aware_exports", "interop", "v0.1-v0.3", CapabilityLevel.CONTRACT_READY,
        "Preflight transcript, EAF, QDPX, JAMS, MIDI, notation, JSON-LD, and RO-Crate outputs.",
        "notewitness.domain.interop",
        "Format writers and round-trip fixtures remain release gates.",
    ),
    _capability(
        "benchmark_protocol", "evaluation", "v0.3", CapabilityLevel.CONTRACT_READY,
        "Stratify results and retain unsupported, failed, interrupted, and not-alignable cases.",
        "notewitness.domain.evaluation",
    ),
    _capability(
        "offline_metronome", "utilities", "v0.1", CapabilityLevel.AVAILABLE,
        "Generate bounded, drift-resistant bar, beat, and subdivision click schedules.",
        "notewitness.domain.utilities.MetronomePlan",
        "The graphical workbench renders the schedule through Web Audio.",
    ),
    _capability(
        "metronome_audio_output", "utilities", "v0.1", CapabilityLevel.AVAILABLE,
        "Render scheduled clicks through the browser's local Web Audio clock.",
        "notewitness.presentation.workbench_assets.app.js",
        "Requires a browser audio context and an available local output device.",
    ),
    _capability(
        "offline_tuner", "utilities", "v0.1", CapabilityLevel.AVAILABLE,
        "Convert local frequency estimates into note, octave, and cents-offset readings.",
        "notewitness.domain.utilities.tuner_reading",
        "The graphical workbench supplies an ephemeral microphone estimator.",
    ),
    _capability(
        "live_pitch_input", "utilities", "v0.1", CapabilityLevel.AVAILABLE,
        "Estimate ephemeral pitch locally and map it to note and cents readings.",
        "notewitness.presentation.workbench_assets.app.js",
        "Requires microphone permission; samples are not uploaded or retained.",
    ),
    _capability(
        "openai_relation_suggestions", "optional_remote", "v0.3", CapabilityLevel.AVAILABLE,
        "Send only explicitly selected, rights-authorized text through the gated endpoint.",
        "notewitness.providers.openai_responses",
        "This is optional remote processing and never part of strict local mode.",
        local_first=False,
    ),
    _capability(
        "automated_grading", "excluded", "through_v0.3", CapabilityLevel.EXCLUDED,
        "No grading, ranking, talent, engagement, emotion, or teaching-quality score.",
        "RESEARCH_REPORT.md#explicit-non-goals-through-the-research-mvp",
    ),
    _capability(
        "live_surveillance", "excluded", "through_v0.3", CapabilityLevel.EXCLUDED,
        "No always-on classroom surveillance or real-time intervention.",
        "RESEARCH_REPORT.md#explicit-non-goals-through-the-research-mvp",
    ),
    _capability(
        "persistent_voice_identity", "excluded", "all", CapabilityLevel.EXCLUDED,
        "No cross-project voiceprints or automatic identity inference.",
        "notewitness.domain.governance",
    ),
)

TONIC_LOCAL_PROFILE = (
    "private_project_init",
    "evidence_graph",
    "one_action_capture",
    "activity_segmentation",
    "local_asr",
    "anonymous_diarization",
    "local_lesson_digest",
    "lesson_notes_artifact",
    "practice_checklist",
    "source_time_playback",
    "local_playback_backend",
    "graphical_workbench",
)

NOTEWITNESS_V01_PROFILE = (
    *TONIC_LOCAL_PROFILE,
    "note_detection",
    "instrument_detection",
    "instrument_diarization",
    "strict_local_model_runtime",
    "full_music_transcript",
    "practice_plan",
    "lesson_statistics",
    "offline_metronome",
    "metronome_audio_output",
    "offline_tuner",
    "live_pitch_input",
)

NOSCRIBE_RESEARCH_PROFILE = (
    "private_project_init",
    "evidence_graph",
    "local_media_ingest",
    "local_asr",
    "anonymous_diarization",
    "noscribe_asr_conformance",
    "noscribe_diarization_conformance",
    "bounded_resumable_jobs",
    "research_transcription_options",
    "transcription_language_modes",
    "transcription_speaker_options",
    "transcript_correction_workspace",
    "transcription_run_manifest",
    "source_time_playback",
    "local_playback_backend",
    "html_text_vtt_transcript_exports",
)

PROFILES = {
    "tonic-local": TONIC_LOCAL_PROFILE,
    "notewitness-v0.1": NOTEWITNESS_V01_PROFILE,
    "noscribe-research": NOSCRIBE_RESEARCH_PROFILE,
}


class CapabilityRegistry(Protocol):
    @property
    def available_capability_ids(self) -> tuple[str, ...]: ...


def capability_manifest() -> dict[str, Any]:
    counts = {
        level.value: sum(item.level is level for item in CAPABILITIES)
        for level in CapabilityLevel
    }
    return {
        "manifest_version": "0.2.0",
        "default_network_mode": "offline",
        "counts": counts,
        "capabilities": [item.as_dict() for item in CAPABILITIES],
    }


def profile_readiness(
    profile: str, registry: CapabilityRegistry | None = None
) -> dict[str, Any]:
    if profile not in PROFILES:
        raise ValueError(f"Unknown production profile: {profile!r}.")
    by_id = {item.capability_id: item for item in CAPABILITIES}
    required_ids = PROFILES[profile]
    required = tuple(by_id[item_id] for item_id in required_ids)
    installed = set(registry.available_capability_ids if registry else ())
    built_in_levels = {
        CapabilityLevel.AVAILABLE,
        CapabilityLevel.PROJECTION_AVAILABLE,
    }
    missing = tuple(
        item.capability_id
        for item in required
        if item.level not in built_in_levels and item.capability_id not in installed
    )
    return {
        "profile": profile,
        "ready": not missing,
        "required": list(required_ids),
        "missing": list(missing),
        "note": (
            "Implemented surfaces are listed separately from runtime-probed external "
            "engines; profile parity is not claimed while required capabilities are missing."
        ),
    }
