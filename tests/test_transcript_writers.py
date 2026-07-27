from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest

from notewitness.domain.transcript_document import (
    TranscriptDocument,
    TranscriptSegment,
    TranscriptWord,
)
from notewitness.transcript_writers import (
    TranscriptPublicationError,
    publish_new_private_text,
    render_html,
    render_txt,
    render_webvtt,
)


def _document(text: str = "Hello <script>alert('x')</script> & Grüße") -> TranscriptDocument:
    word = TranscriptWord(
        word_id="word-1",
        source_id="source-1",
        stream_id="audio-1",
        start_us=1_100_100,
        end_us=1_400_100,
        text="Hello",
        language="en",
        confidence=0.9,
        anonymous_speaker_cluster="speaker-a",
    )
    segment = TranscriptSegment(
        segment_id="segment-1",
        source_id="source-1",
        stream_id="audio-1",
        start_us=1_000_100,
        end_us=2_000_100,
        text=text,
        language="en",
        confidence=0.8,
        word_ids=(word.word_id,),
        anonymous_speaker_cluster="speaker-a",
    )
    return TranscriptDocument(
        document_id="document-1",
        source_id="source-1",
        stream_id="audio-1",
        raw_artifact_id="raw-1",
        run_id="run-1",
        language="en",
        segments=(segment,),
        words=(word,),
    )


class TranscriptWriterTests(unittest.TestCase):
    def test_serializers_have_deterministic_exact_basics(self) -> None:
        document = _document("Hello\nGrüße")

        self.assertEqual(
            render_txt(document),
            "[00:00:01.000] [speaker-a] Hello Grüße\n",
        )
        vtt = render_webvtt(document)
        self.assertEqual(
            vtt,
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.001\n"
            "[speaker-a] Hello Grüße\n",
        )
        html = render_html(document)
        self.assertIn('<html lang="en">', html)
        self.assertIn('data-start-us="1000100"', html)
        self.assertIn("Hello Grüße", html)

    def test_html_and_webvtt_escape_untrusted_markup(self) -> None:
        document = _document()

        html = render_html(document)
        vtt = render_webvtt(document)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&amp;", html)
        self.assertNotIn("<script>", vtt)
        self.assertIn("&lt;script&gt;", vtt)

    def test_timestamp_and_pause_options_change_text_and_html_exports(self) -> None:
        original = _document("First phrase")
        second = TranscriptSegment(
            segment_id="segment-2",
            source_id=original.source_id,
            stream_id=original.stream_id,
            start_us=5_000_100,
            end_us=6_000_100,
            text="Second phrase",
            language=original.language,
            confidence=0.75,
            word_ids=(),
            anonymous_speaker_cluster="speaker-b",
        )
        document = replace(original, segments=(*original.segments, second))

        plain = render_txt(
            document,
            visible_timestamps=False,
            pause_threshold_ms=2_000,
        )
        self.assertNotIn("[00:", plain)
        self.assertIn("[PAUSE 3.000 s]", plain)
        self.assertEqual(
            2,
            render_txt(document, timestamp_interval_ms=3_000).count("[00:"),
        )
        self.assertNotIn(
            "<time ",
            render_html(document, visible_timestamps=False),
        )

    def test_private_publication_refuses_public_parent_overwrite_and_symlink(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            private = root / "private"
            private.mkdir(mode=0o700)
            private.chmod(0o700)
            target = private / "transcript.txt"

            result = publish_new_private_text(target, "Grüße\n")
            self.assertEqual(result, target)
            self.assertEqual(target.read_text(encoding="utf-8"), "Grüße\n")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            with self.assertRaisesRegex(TranscriptPublicationError, "replace"):
                publish_new_private_text(target, "replacement")

            public = root / "public"
            public.mkdir(mode=0o755)
            public.chmod(0o755)
            with self.assertRaisesRegex(TranscriptPublicationError, "deny group"):
                publish_new_private_text(public / "transcript.txt", "text")

            linked = root / "linked"
            linked.symlink_to(private, target_is_directory=True)
            with self.assertRaisesRegex(TranscriptPublicationError, "non-symlink"):
                publish_new_private_text(linked / "transcript.txt", "text")


if __name__ == "__main__":
    unittest.main()
