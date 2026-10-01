"""Human review, recovery, and project-lexicon transcription records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from notewitness.core.time import MediaSpan
from notewitness.core.transcription.shared import (
    _require_bool,
    _require_enum,
)
from notewitness.core.transcription._review_validation import (
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
