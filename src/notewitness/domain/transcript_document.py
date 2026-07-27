"""Strict, normalized transcript records for local research exports.

The document deliberately keeps raw ASR output outside this projection.  It
only references that artifact and the immutable run that produced the
normalized words and segments.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re


MAX_SEGMENTS = 10_000
MAX_WORDS = 100_000
MAX_TEXT_CHARS = 20_000
MAX_IDENTIFIER_CHARS = 256
_LANGUAGE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


def _require_identifier(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value or len(value) > MAX_IDENTIFIER_CHARS:
        raise ValueError(f"{field_name} must be a bounded, non-empty string.")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError(f"{field_name} must not contain whitespace or controls.")


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT_CHARS:
        raise ValueError(f"{field_name} must be a bounded, non-empty string.")
    if "\x00" in value:
        raise ValueError(f"{field_name} must not contain NUL characters.")


def _require_microsecond_range(start_us: object, end_us: object) -> None:
    if (
        not isinstance(start_us, int)
        or isinstance(start_us, bool)
        or not isinstance(end_us, int)
        or isinstance(end_us, bool)
        or start_us < 0
        or end_us <= start_us
    ):
        raise ValueError("Transcript times must be positive, ordered microseconds.")


def _require_language(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not _LANGUAGE.fullmatch(value):
        raise ValueError(f"{field_name} must be a normalized BCP-47 language tag.")


def _require_confidence(value: object, field_name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or value > 1
    ):
        raise ValueError(f"{field_name} must be a finite value from 0 to 1.")


def _require_cluster(value: object) -> None:
    if value is None:
        return
    _require_identifier(value, "anonymous_speaker_cluster")


@dataclass(frozen=True, slots=True)
class TranscriptWord:
    """One normalized word aligned to a single local source stream."""

    word_id: str
    source_id: str
    stream_id: str
    start_us: int
    end_us: int
    text: str
    language: str
    confidence: float
    anonymous_speaker_cluster: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.word_id, "word_id")
        _require_identifier(self.source_id, "source_id")
        _require_identifier(self.stream_id, "stream_id")
        _require_microsecond_range(self.start_us, self.end_us)
        _require_text(self.text, "text")
        _require_language(self.language, "language")
        _require_confidence(self.confidence, "confidence")
        _require_cluster(self.anonymous_speaker_cluster)


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    """A normalized speech segment referencing its ordered word IDs."""

    segment_id: str
    source_id: str
    stream_id: str
    start_us: int
    end_us: int
    text: str
    language: str
    confidence: float
    word_ids: tuple[str, ...]
    anonymous_speaker_cluster: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.segment_id, "segment_id")
        _require_identifier(self.source_id, "source_id")
        _require_identifier(self.stream_id, "stream_id")
        _require_microsecond_range(self.start_us, self.end_us)
        _require_text(self.text, "text")
        _require_language(self.language, "language")
        _require_confidence(self.confidence, "confidence")
        if not isinstance(self.word_ids, tuple):
            raise ValueError("word_ids must be an immutable tuple.")
        if any(not isinstance(word_id, str) or not word_id for word_id in self.word_ids):
            raise ValueError("word_ids must contain non-empty strings.")
        if len(self.word_ids) != len(set(self.word_ids)):
            raise ValueError("word_ids must be unique within each segment.")
        _require_cluster(self.anonymous_speaker_cluster)


@dataclass(frozen=True, slots=True)
class TranscriptDocument:
    """A bounded transcript projection bound to source, run, and raw artifact.

    Segments and each segment's words are ordered by nondecreasing start time;
    overlapping intervals are valid.  The document word sequence follows the
    segment sequence and each segment's ``word_ids`` exactly.
    """

    document_id: str
    source_id: str
    stream_id: str
    raw_artifact_id: str
    run_id: str
    language: str
    segments: tuple[TranscriptSegment, ...]
    words: tuple[TranscriptWord, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.document_id, "document_id")
        _require_identifier(self.source_id, "source_id")
        _require_identifier(self.stream_id, "stream_id")
        _require_identifier(self.raw_artifact_id, "raw_artifact_id")
        _require_identifier(self.run_id, "run_id")
        _require_language(self.language, "language")
        if not isinstance(self.segments, tuple):
            raise ValueError("Transcript document segments must be an immutable tuple.")
        if not isinstance(self.words, tuple):
            raise ValueError("Transcript document words must be an immutable tuple.")
        if len(self.segments) > MAX_SEGMENTS or len(self.words) > MAX_WORDS:
            raise ValueError("Transcript document exceeds its bounded record limit.")
        if any(not isinstance(segment, TranscriptSegment) for segment in self.segments):
            raise ValueError("Transcript documents require typed segments.")
        if any(not isinstance(word, TranscriptWord) for word in self.words):
            raise ValueError("Transcript documents require typed words.")

        segment_ids = tuple(segment.segment_id for segment in self.segments)
        word_ids = tuple(word.word_id for word in self.words)
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("Transcript segment IDs must be unique.")
        if len(word_ids) != len(set(word_ids)):
            raise ValueError("Transcript word IDs must be unique.")
        if set(segment_ids) & set(word_ids):
            raise ValueError("Transcript segment and word IDs must not collide.")

        words_by_id = {word.word_id: word for word in self.words}
        linked_word_ids: list[str] = []
        previous_segment_start: int | None = None
        for segment in self.segments:
            if (
                previous_segment_start is not None
                and segment.start_us < previous_segment_start
            ):
                raise ValueError("Transcript segments must use nondecreasing start times.")
            previous_segment_start = segment.start_us
            if (segment.source_id, segment.stream_id) != (self.source_id, self.stream_id):
                raise ValueError("Every segment must belong to the document source stream.")
            if self.language != "mul" and segment.language != self.language:
                raise ValueError("Every segment must use the document language.")
            previous_word_start: int | None = None
            for word_id in segment.word_ids:
                word = words_by_id.get(word_id)
                if word is None:
                    raise ValueError("Transcript segment references an unknown word ID.")
                if (word.source_id, word.stream_id) != (
                    segment.source_id,
                    segment.stream_id,
                ):
                    raise ValueError("Transcript words must share their segment source stream.")
                if word.language != segment.language:
                    raise ValueError("Transcript words must share their segment language.")
                if word.start_us < segment.start_us or word.end_us > segment.end_us:
                    raise ValueError("Transcript words must be contained by their segment.")
                if previous_word_start is not None and word.start_us < previous_word_start:
                    raise ValueError("Segment words must use nondecreasing start times.")
                previous_word_start = word.start_us
                linked_word_ids.append(word_id)
        if len(linked_word_ids) != len(set(linked_word_ids)):
            raise ValueError("Each normalized word must belong to one segment.")
        if set(linked_word_ids) != set(word_ids):
            raise ValueError("Every normalized word must be linked by a segment.")
        if tuple(linked_word_ids) != word_ids:
            raise ValueError("Document words must follow segment and word-link order.")
