from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from notewitness.domain.transcript_document import (
    TranscriptDocument,
    TranscriptSegment,
    TranscriptWord,
)


def _word(word_id: str = "word-1", *, start_us: int = 1_100_000) -> TranscriptWord:
    return TranscriptWord(
        word_id=word_id,
        source_id="source-1",
        stream_id="audio-1",
        start_us=start_us,
        end_us=start_us + 200_000,
        text="Grüße",
        language="de-DE",
        confidence=0.9,
        anonymous_speaker_cluster="speaker-a",
    )


def _segment(word_ids: tuple[str, ...] = ("word-1",)) -> TranscriptSegment:
    return TranscriptSegment(
        segment_id="segment-1",
        source_id="source-1",
        stream_id="audio-1",
        start_us=1_000_000,
        end_us=2_000_000,
        text="Grüße Welt",
        language="de-DE",
        confidence=0.8,
        word_ids=word_ids,
        anonymous_speaker_cluster="speaker-a",
    )


def _document(
    segments: tuple[TranscriptSegment, ...] | None = None,
    words: tuple[TranscriptWord, ...] | None = None,
) -> TranscriptDocument:
    return TranscriptDocument(
        document_id="document-1",
        source_id="source-1",
        stream_id="audio-1",
        raw_artifact_id="artifact-raw-1",
        run_id="run-1",
        language="de-DE",
        segments=segments if segments is not None else (_segment(),),
        words=words if words is not None else (_word(),),
    )


class TranscriptDocumentTests(unittest.TestCase):
    def test_normalized_document_is_immutable_and_keeps_provenance(self) -> None:
        document = _document()

        self.assertEqual(document.raw_artifact_id, "artifact-raw-1")
        self.assertEqual(document.run_id, "run-1")
        self.assertEqual(document.words[0].start_us, 1_100_000)
        with self.assertRaises(FrozenInstanceError):
            document.run_id = "other"  # type: ignore[misc]

    def test_empty_and_multilingual_documents_are_explicit(self) -> None:
        empty = TranscriptDocument(
            document_id="document-empty",
            source_id="source-1",
            stream_id="audio-1",
            raw_artifact_id="artifact-raw-empty",
            run_id="run-empty",
            language="und",
            segments=(),
            words=(),
        )
        german_segment = _segment()
        multilingual = TranscriptDocument(
            document_id="document-multilingual",
            source_id="source-1",
            stream_id="audio-1",
            raw_artifact_id="artifact-raw-multilingual",
            run_id="run-multilingual",
            language="mul",
            segments=(german_segment,),
            words=(_word(),),
        )

        self.assertEqual((), empty.segments)
        self.assertEqual("de-DE", multilingual.segments[0].language)

    def test_rejects_cross_segment_word_timing_and_source(self) -> None:
        outside = _word(start_us=2_000_000)
        with self.assertRaisesRegex(ValueError, "contained"):
            _document(words=(outside,))

        cross_source = TranscriptWord(
            word_id="word-1",
            source_id="source-2",
            stream_id="audio-1",
            start_us=1_100_000,
            end_us=1_300_000,
            text="word",
            language="de-DE",
            confidence=0.7,
        )
        with self.assertRaisesRegex(ValueError, "source stream"):
            _document(words=(cross_source,))

    def test_rejects_identifier_collisions_and_unlinked_words(self) -> None:
        collision_segment = TranscriptSegment(
            segment_id="word-1",
            source_id="source-1",
            stream_id="audio-1",
            start_us=1_000_000,
            end_us=2_000_000,
            text="Words",
            language="de-DE",
            confidence=0.8,
            word_ids=("word-1",),
        )
        with self.assertRaisesRegex(ValueError, "must not collide"):
            _document(segments=(collision_segment,))

        unlinked = _word("word-2", start_us=1_400_000)
        with self.assertRaisesRegex(ValueError, "linked"):
            _document(words=(_word(), unlinked))

    def test_rejects_mutable_word_links_and_invalid_confidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "immutable"):
            _segment(["word-1"])  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "0 to 1"):
            TranscriptWord(
                word_id="word-1",
                source_id="source-1",
                stream_id="audio-1",
                start_us=1,
                end_us=2,
                text="word",
                language="en",
                confidence=float("nan"),
            )

    def test_rejects_reversed_segments_words_and_global_word_order(self) -> None:
        second_word = _word("word-2", start_us=2_100_000)
        second_segment = TranscriptSegment(
            segment_id="segment-2",
            source_id="source-1",
            stream_id="audio-1",
            start_us=2_000_000,
            end_us=3_000_000,
            text="second",
            language="de-DE",
            confidence=0.8,
            word_ids=("word-2",),
        )
        with self.assertRaisesRegex(ValueError, "segments"):
            _document(
                segments=(second_segment, _segment()),
                words=(second_word, _word()),
            )

        reversed_words = TranscriptSegment(
            segment_id="segment-1",
            source_id="source-1",
            stream_id="audio-1",
            start_us=1_000_000,
            end_us=2_000_000,
            text="reversed",
            language="de-DE",
            confidence=0.8,
            word_ids=("word-2", "word-1"),
        )
        with self.assertRaisesRegex(ValueError, "Segment words"):
            _document(
                segments=(reversed_words,),
                words=(_word(), _word("word-2", start_us=1_500_000)),
            )

        with self.assertRaisesRegex(ValueError, "Document words"):
            _document(
                segments=(_segment(), second_segment),
                words=(second_word, _word()),
            )


if __name__ == "__main__":
    unittest.main()
