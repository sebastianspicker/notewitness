from __future__ import annotations

import json
from pathlib import Path
import platform
from tempfile import TemporaryDirectory
import unittest

from notewitness.adapters.ffprobe import FFprobeMediaProbe, MediaProbeError
from notewitness.local_tools import LocalTool, LocalToolIdentityChanged


@unittest.skipUnless(platform.system() == "Darwin", "macOS isolation contract")
class FFprobeAdapterTests(unittest.TestCase):
    def test_parses_bounded_audio_video_metadata(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            media = root / "lesson.m4a"
            media.write_bytes(b"synthetic-media")
            payload = {
                "streams": [
                    {
                        "codec_type": "audio",
                        "codec_name": "aac",
                        "duration": "2.5",
                        "sample_rate": "48000",
                        "channels": 2,
                    },
                    {"codec_type": "video", "codec_name": "h264"},
                ],
                "format": {"duration": "2.5", "format_name": "mov,mp4"},
            }
            tool = _fake_ffprobe(root, json.dumps(payload))

            result = FFprobeMediaProbe(tool).inspect(media)

        self.assertEqual(2_500_000, result.duration_us)
        self.assertEqual(("aac",), result.audio_codecs)
        self.assertEqual(("h264",), result.video_codecs)
        self.assertEqual((48_000,), result.sample_rates_hz)
        self.assertEqual("video", result.kind)

    def test_rejects_media_without_audio_or_duration(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            media = root / "lesson.bin"
            media.write_bytes(b"synthetic-media")
            tool = _fake_ffprobe(
                root,
                json.dumps(
                    {
                        "streams": [{"codec_type": "video", "codec_name": "h264"}],
                        "format": {"format_name": "mov"},
                    }
                ),
            )

            with self.assertRaisesRegex(MediaProbeError, "audio stream"):
                FFprobeMediaProbe(tool).inspect(media)

    def test_rejects_metadata_when_ffprobe_is_replaced_after_startup(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            media = root / "lesson.m4a"
            media.write_bytes(b"synthetic-media")
            tool = _fake_ffprobe(
                root,
                json.dumps(
                    {
                        "streams": [{"codec_type": "audio", "codec_name": "aac"}],
                        "format": {"duration": "2.5", "format_name": "mov"},
                    }
                ),
                mutate_self=True,
            )

            with self.assertRaises(LocalToolIdentityChanged):
                FFprobeMediaProbe(tool).inspect(media)


def _fake_ffprobe(
    root: Path,
    output: str,
    *,
    mutate_self: bool = False,
) -> LocalTool:
    script = root / "ffprobe-fixture"
    source = (
        "#!/usr/bin/python3\n"
        "import pathlib\n"
    )
    if mutate_self:
        source += "pathlib.Path(__file__).write_text('#!/bin/sh\\nexit 0\\n')\n"
    source += f"print({output!r})\n"
    script.write_text(source, encoding="utf-8")
    script.chmod(0o700)
    return LocalTool("ffprobe", script)


if __name__ == "__main__":
    unittest.main()
