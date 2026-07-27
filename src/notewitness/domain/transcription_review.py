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
        if not self.result_cluster_id:
            raise ValueError("Speaker result-span assignments require a result cluster ID.")
        if not self.spans:
            raise ValueError("Speaker result-span assignments require at least one span.")
        if any(not isinstance(span, MediaSpan) for span in self.spans):
            raise ValueError("Speaker result-span assignments must contain MediaSpan values.")
        if len(self.spans) != len(set(self.spans)):
            raise ValueError("Speaker result-span assignment spans must be unique.")


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
        if not self.correction_id or not self.cluster_ids or not self.author_id:
            raise ValueError(
                "Speaker corrections require identity, clusters, and author."
            )
        if not self.reason:
            raise ValueError("Speaker corrections require a reason.")
        if not self.parent_revision_ids:
            raise ValueError("Speaker corrections require a parent revision.")
        if len(self.parent_revision_ids) != len(set(self.parent_revision_ids)):
            raise ValueError("Speaker correction parent revisions must be unique.")
        _validate_timestamp(self.created_at, "created_at", required=True)
        if len(self.cluster_ids) != len(set(self.cluster_ids)):
            raise ValueError("Speaker correction cluster IDs must be unique.")
        if not self.result_cluster_ids or any(
            not cluster_id for cluster_id in self.result_cluster_ids
        ):
            raise ValueError("Speaker corrections require result cluster IDs.")
        if len(self.result_cluster_ids) != len(set(self.result_cluster_ids)):
            raise ValueError("Speaker result cluster IDs must be unique.")
        if any(not isinstance(span, MediaSpan) for span in self.spans):
            raise ValueError("Speaker correction spans must contain MediaSpan values.")
        if len(self.spans) != len(set(self.spans)):
            raise ValueError("Speaker correction spans must be unique.")
        if not self.result_span_assignments:
            raise ValueError(
                "Speaker corrections require explicit result-to-span assignments."
            )
        if any(
            not isinstance(assignment, SpeakerResultSpanAssignment)
            for assignment in self.result_span_assignments
        ):
            raise ValueError(
                "Speaker result-span assignments must be SpeakerResultSpanAssignment values."
            )
        assigned_result_ids = tuple(
            assignment.result_cluster_id for assignment in self.result_span_assignments
        )
        if len(assigned_result_ids) != len(set(assigned_result_ids)):
            raise ValueError("Every result cluster may have only one span assignment.")
        if set(assigned_result_ids) != set(self.result_cluster_ids):
            raise ValueError(
                "Speaker result-span assignments must reference exactly result cluster IDs."
            )
        assigned_spans = tuple(
            span
            for assignment in self.result_span_assignments
            for span in assignment.spans
        )
        if len(assigned_spans) != len(set(assigned_spans)):
            raise ValueError(
                "A speaker-correction span may not map to multiple result clusters."
            )
        if set(assigned_spans) != set(self.spans):
            raise ValueError(
                "Speaker correction spans must exactly match result-span assignments."
            )
        if self.kind is SpeakerCorrectionKind.ASSIGN:
            if (
                len(self.cluster_ids) != 1
                or self.result_cluster_ids != self.cluster_ids
                or not self.actor_id
                or len(self.result_span_assignments) != 1
            ):
                raise ValueError(
                    "Speaker assignment requires one unchanged cluster, one result, "
                    "and an actor ID."
                )
        elif self.kind is SpeakerCorrectionKind.MERGE:
            if (
                len(self.cluster_ids) < 2
                or len(self.result_cluster_ids) != 1
                or self.result_cluster_ids[0] in self.cluster_ids
                or len(self.result_span_assignments) != 1
            ):
                raise ValueError(
                    "Speaker merge requires two inputs, one new result, and one assignment."
                )
        elif self.kind is SpeakerCorrectionKind.SPLIT:
            if (
                len(self.cluster_ids) != 1
                or len(self.result_cluster_ids) < 2
                or set(self.result_cluster_ids) & set(self.cluster_ids)
                or not self.spans
            ):
                raise ValueError(
                    "Speaker split requires one input, new result clusters, and spans."
                )
            self._validate_non_overlapping_split_assignments()

    def _validate_non_overlapping_split_assignments(self) -> None:
        """Reject ambiguous temporal ownership while replaying a cluster split."""

        spans_by_stream: dict[tuple[str, str], list[MediaSpan]] = {}
        for assignment in self.result_span_assignments:
            for span in assignment.spans:
                spans_by_stream.setdefault((span.source_id, span.stream_id), []).append(span)
        for stream_spans in spans_by_stream.values():
            ordered_spans = sorted(stream_spans, key=lambda span: (span.start_us, span.end_us))
            previous_end_us = -1
            for span in ordered_spans:
                if span.start_us < previous_end_us:
                    raise ValueError(
                        "Speaker split assignments must not overlap on the same source stream."
                    )
                previous_end_us = span.end_us


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
