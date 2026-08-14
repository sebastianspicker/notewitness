"""Human review, recovery, and project-lexicon transcription records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from notewitness.domain.timeline import MediaSpan
from notewitness.domain.transcription_shared import (
    _finite_number_in_range,
    _require_bool,
    _require_enum,
    _validate_timestamp,
)
from notewitness.domain._transcription_review_validation import (
    validate_speaker_correction,
    validate_speaker_result_span_assignment,
)


MAX_LEXICON_ENTRIES = 10_000


class SpeakerCorrectionKind(StrEnum):
    ASSIGN = "assign"
    MERGE = "merge"
    SPLIT = "split"


@dataclass(frozen=True, slots=True)
class SpeakerResultSpanAssignment:
    """The source-time material reassigned to one resulting speaker cluster.

    The record is deliberately explicit instead of relying on positional span
    lists: a replay engine can reconstruct a split without interpreting a UI
    gesture or a transient diarization-cluster order.
    """

    result_cluster_id: str
    spans: tuple[MediaSpan, ...]

    def __post_init__(self) -> None:
        validate_speaker_result_span_assignment(self)


@dataclass(frozen=True, slots=True)
class SpeakerCorrection:
    correction_id: str
    kind: SpeakerCorrectionKind
    cluster_ids: tuple[str, ...]
    result_cluster_ids: tuple[str, ...]
    actor_id: str | None
    spans: tuple[MediaSpan, ...]
    result_span_assignments: tuple[SpeakerResultSpanAssignment, ...]
    author_id: str
    reason: str
    parent_revision_ids: tuple[str, ...]
    created_at: str

    def __post_init__(self) -> None:
        _require_enum(self.kind, SpeakerCorrectionKind, "kind")
        validate_speaker_correction(self, SpeakerResultSpanAssignment)


@dataclass(frozen=True, slots=True)
class TranscriptCorrection:
    correction_id: str
    source_word_ids: tuple[str, ...]
    replacement_text: str
    author_id: str
    reason: str
    parent_revision_ids: tuple[str, ...]
    created_at: str

    def __post_init__(self) -> None:
        if not self.correction_id or not self.source_word_ids or not self.author_id:
            raise ValueError("Transcript corrections require IDs, words, and author.")
        if len(self.source_word_ids) != len(set(self.source_word_ids)):
            raise ValueError("Transcript correction word IDs must be unique.")
        if not self.reason or not self.parent_revision_ids:
            raise ValueError(
                "Transcript corrections require a reason and parent revision."
            )
        if len(self.parent_revision_ids) != len(set(self.parent_revision_ids)):
            raise ValueError("Transcript correction parent revisions must be unique.")
        _validate_timestamp(self.created_at, "created_at", required=True)


@dataclass(frozen=True, slots=True)
class TranscriptReplacementPreview:
    """Non-mutating search/replace preview over canonical word IDs."""

    preview_id: str
    query: str
    replacement_text: str
    matched_word_ids: tuple[str, ...]
    case_sensitive: bool

    def __post_init__(self) -> None:
        if not self.preview_id or not self.query:
            raise ValueError("Replacement previews require an ID and query.")
        if not self.matched_word_ids:
            raise ValueError("Replacement previews require at least one match.")
        if len(self.matched_word_ids) != len(set(self.matched_word_ids)):
            raise ValueError("Replacement preview word IDs must be unique.")
        _require_bool(self.case_sensitive, "case_sensitive")


@dataclass(frozen=True, slots=True)
class TranscriptEditorPreferences:
    follow_selection: bool = True
    playback_speed: float = 1.0
    zoom: float = 1.0

    def __post_init__(self) -> None:
        _require_bool(self.follow_selection, "follow_selection")
        if not _finite_number_in_range(self.playback_speed, 0.25, 3.0):
            raise ValueError("playback_speed must be in [0.25, 3.0].")
        if not _finite_number_in_range(self.zoom, 0.5, 4.0):
            raise ValueError("zoom must be in [0.5, 4.0].")


@dataclass(frozen=True, slots=True)
class TranscriptDraftCheckpoint:
    run_id: str
    revision_id: str
    last_word_id: str | None
    partial: bool
    created_at: str

    def __post_init__(self) -> None:
        if not self.run_id or not self.revision_id:
            raise ValueError("Draft checkpoints require run and revision IDs.")
        _require_bool(self.partial, "partial")
        _validate_timestamp(self.created_at, "created_at", required=True)


@dataclass(frozen=True, slots=True)
class ProjectLexiconEntry:
    written_form: str
    spoken_variants: tuple[str, ...]
    language_code: str | None = None

    def __post_init__(self) -> None:
        if not self.written_form:
            raise ValueError("Lexicon entries require a written form.")
        if len(self.spoken_variants) != len(set(self.spoken_variants)):
            raise ValueError("Lexicon spoken variants must be unique.")


@dataclass(frozen=True, slots=True)
class ProjectLexicon:
    lexicon_id: str
    version: str
    entries: tuple[ProjectLexiconEntry, ...]

    def __post_init__(self) -> None:
        if not self.lexicon_id or not self.version:
            raise ValueError("Project lexicons require identity and version.")
        if len(self.entries) > MAX_LEXICON_ENTRIES:
            raise ValueError(
                f"Project lexicons are limited to {MAX_LEXICON_ENTRIES} entries."
            )
        written_forms = tuple(entry.written_form.casefold() for entry in self.entries)
        if len(written_forms) != len(set(written_forms)):
            raise ValueError("Project lexicon written forms must be unique.")
