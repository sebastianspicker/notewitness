"""Stable public façade for research-transcription domain contracts.

Implementation is divided by options, run provenance, review records, and export
projections.  Imports from this module remain the supported compatibility surface.
"""

from notewitness.domain.transcription_export import (
    transcript_export_losses,
    transcript_export_preflight,
)
from notewitness.domain.transcription_options import (
    MAX_EXACT_SPEAKERS,
    MAX_TRANSCRIPTION_QUEUE_JOBS,
    MAX_TRANSCRIPTION_SOURCES,
    DiarizationMode,
    DisfluencyPolicy,
    LanguageMode,
    TranscriptExportFormat,
    TranscriptionJobSpec,
    TranscriptionQueueItem,
    TranscriptionQueuePlan,
)
from notewitness.domain.transcription_review import (
    MAX_LEXICON_ENTRIES,
    ProjectLexicon,
    ProjectLexiconEntry,
    SpeakerCorrection,
    SpeakerCorrectionKind,
    SpeakerResultSpanAssignment,
    TranscriptCorrection,
    TranscriptDraftCheckpoint,
    TranscriptEditorPreferences,
    TranscriptReplacementPreview,
)
from notewitness.domain.transcription_run import (
    CanonicalTranscriptEvidence,
    DetectedLanguage,
    ResolvedModelProfile,
    ResolvedRunArtifact,
    SourceChecksum,
    TranscriptionRunManifest,
    TranscriptionRunLedger,
    TranscriptionRunState,
    transcription_settings_sha256,
)
from notewitness.domain.transcription_shared import (
    MAX_SETTINGS,
    FrozenJsonObject,
)


__all__ = (
    "MAX_EXACT_SPEAKERS",
    "MAX_LEXICON_ENTRIES",
    "MAX_SETTINGS",
    "MAX_TRANSCRIPTION_QUEUE_JOBS",
    "MAX_TRANSCRIPTION_SOURCES",
    "CanonicalTranscriptEvidence",
    "DetectedLanguage",
    "DiarizationMode",
    "DisfluencyPolicy",
    "FrozenJsonObject",
    "LanguageMode",
    "ProjectLexicon",
    "ProjectLexiconEntry",
    "ResolvedModelProfile",
    "ResolvedRunArtifact",
    "SourceChecksum",
    "SpeakerCorrection",
    "SpeakerCorrectionKind",
    "SpeakerResultSpanAssignment",
    "TranscriptCorrection",
    "TranscriptDraftCheckpoint",
    "TranscriptEditorPreferences",
    "TranscriptExportFormat",
    "TranscriptReplacementPreview",
    "TranscriptionJobSpec",
    "TranscriptionQueueItem",
    "TranscriptionQueuePlan",
    "TranscriptionRunManifest",
    "TranscriptionRunLedger",
    "TranscriptionRunState",
    "transcript_export_losses",
    "transcript_export_preflight",
    "transcription_settings_sha256",
)
