from __future__ import annotations

import hashlib
from pathlib import Path
import platform
import stat
from tempfile import TemporaryDirectory
import unittest

from notewitness.adapters.whisper_cli import (
    WhisperCLIAdapter,
    WhisperCLIError,
    WhisperCLISettings,
)
from notewitness.local_tools import LocalTool


@unittest.skipUnless(platform.system() == "Darwin", "macOS isolation contract")
class WhisperCLIAdapterTests(unittest.TestCase):
    def test_runs_explicit_checkpoint_offline_and_normalizes_word_timing(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            media = root / "lesson.wav"
            media.write_bytes(b"synthetic media")
            model = root / "tiny-model.pt"
            model.write_bytes(b"model fixture")
            output = root / "run"
            output.mkdir(mode=0o700)
            tool = _fake_whisper(root, _valid_payload())
            adapter = WhisperCLIAdapter(
                tool,
                WhisperCLISettings(
                    model_checkpoint=model,
                    model_license="MIT-test-fixture",
                    adapter_license="MIT-test-fixture",
                    language="de",
                    threads=2,
                    timeout_seconds=30,
                ),
            )

            result = adapter.transcribe(
                media_path=media,
                output_directory=output,
                source_id="source:lesson",
                stream_id="audio",
                run_id="run:test",
                raw_artifact_id="artifact:raw-test",
                duration_us=5_000_000,
            )

            self.assertEqual("Noch einmal", result.document.segments[0].text)
            self.assertEqual(2, len(result.document.words))
            self.assertEqual("Noch", result.document.words[0].text)
            self.assertTrue(result.network_isolated)
            self.assertEqual(0o600, stat.S_IMODE(result.raw_output_path.stat().st_mode))
            self.assertEqual(64, len(result.model.sha256))
            raw_bytes = result.raw_output_path.read_bytes()
            self.assertEqual(hashlib.sha256(raw_bytes).hexdigest(), result.raw_output.sha256)
            self.assertEqual(len(raw_bytes), result.raw_output.size_bytes)
            self.assertEqual(result.raw_output_path.name, result.raw_output.path_name)

    def test_missing_or_out_of_range_raw_output_fails_loudly(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            media = root / "lesson.wav"
            media.write_bytes(b"synthetic media")
            model = root / "model.pt"
            model.write_bytes(b"model fixture")
            for label, payload in (
                ("missing", None),
                ("outside", _valid_payload(end=8.0)),
            ):
                with self.subTest(label=label):
                    output = root / f"run-{label}"
                    output.mkdir(mode=0o700)
                    adapter = WhisperCLIAdapter(
                        _fake_whisper(root, payload, suffix=label),
                        WhisperCLISettings(
                            model,
                            "LicenseRef-test",
                            "MIT-test-fixture",
                            timeout_seconds=30,
                        ),
                    )
                    with self.assertRaises(WhisperCLIError):
                        adapter.transcribe(
                            media_path=media,
                            output_directory=output,
                            source_id="source:lesson",
                            stream_id="audio",
                            run_id=f"run:{label}",
                            raw_artifact_id=f"artifact:raw-{label}",
                            duration_us=5_000_000,
                        )

    def test_requires_explicit_local_model_and_license(self) -> None:
        with self.assertRaises(ValueError):
            WhisperCLISettings(
                Path("model.pt"), "LicenseRef-test", "MIT-test-fixture"
            )

    def test_rejects_mutated_media_after_whisper_launch(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            media = root / "lesson.wav"
            media.write_bytes(b"synthetic media")
            model = root / "model.pt"
            model.write_bytes(b"model fixture")
            output = root / "run"
            output.mkdir(mode=0o700)
            adapter = WhisperCLIAdapter(
                _fake_whisper(root, _valid_payload(), mutate_media=True),
                WhisperCLISettings(model, "LicenseRef-test", "MIT-test-fixture"),
            )

            with self.assertRaisesRegex(WhisperCLIError, "media source changed"):
                adapter.transcribe(
                    media_path=media,
                    output_directory=output,
                    source_id="source:lesson",
                    stream_id="audio",
                    run_id="run:mutated-media",
                    raw_artifact_id="artifact:raw-mutated-media",
                    duration_us=5_000_000,
                )

    def test_rejects_output_when_whisper_launcher_is_replaced(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            media = root / "lesson.wav"
            media.write_bytes(b"synthetic media")
            model = root / "model.pt"
            model.write_bytes(b"model fixture")
            output = root / "run"
            output.mkdir(mode=0o700)
            adapter = WhisperCLIAdapter(
                _fake_whisper(root, _valid_payload(), mutate_launcher=True),
                WhisperCLISettings(model, "LicenseRef-test", "MIT-test-fixture"),
            )

            with self.assertRaisesRegex(WhisperCLIError, "startup approval"):
                adapter.transcribe(
                    media_path=media,
                    output_directory=output,
                    source_id="source:lesson",
                    stream_id="audio",
                    run_id="run:mutated-launcher",
                    raw_artifact_id="artifact:raw-mutated-launcher",
                    duration_us=5_000_000,
                )

    def test_rejects_output_when_path_selected_ffmpeg_is_replaced(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            media = root / "lesson.wav"
            media.write_bytes(b"synthetic media")
            model = root / "model.pt"
            model.write_bytes(b"model fixture")
            output = root / "run"
            output.mkdir(mode=0o700)
            ffmpeg = _fake_ffmpeg(root)
            adapter = WhisperCLIAdapter(
                _fake_whisper(root, _valid_payload(), mutate_ffmpeg=True),
                WhisperCLISettings(
                    model,
                    "LicenseRef-test",
                    "MIT-test-fixture",
                    ffmpeg_license="MIT-test-fixture",
                ),
                ffmpeg=ffmpeg,
            )

            with self.assertRaisesRegex(WhisperCLIError, "startup approval"):
                adapter.transcribe(
                    media_path=media,
                    output_directory=output,
                    source_id="source:lesson",
                    stream_id="audio",
                    run_id="run:mutated-ffmpeg",
                    raw_artifact_id="artifact:raw-mutated-ffmpeg",
                    duration_us=5_000_000,
                )

    def test_rejects_noncanonical_ffmpeg_path_before_unapproved_sibling_can_run(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            model = root / "model.pt"
            model.write_bytes(b"model fixture")
            approved = root / "approved-ffmpeg"
            approved.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            approved.chmod(0o700)
            marker = root / "unapproved-ran"
            sibling = root / "ffmpeg"
            sibling.write_text(
                f"#!/bin/sh\ntouch {marker}\n",
                encoding="utf-8",
            )
            sibling.chmod(0o700)

            with self.assertRaisesRegex(ValueError, "exact basename 'ffmpeg'"):
                WhisperCLIAdapter(
                    _fake_whisper(root, _valid_payload(), suffix="path-bypass"),
                    WhisperCLISettings(
                        model,
                        "LicenseRef-test",
                        "MIT-test-fixture",
                        ffmpeg_license="MIT-test-fixture",
                    ),
                    ffmpeg=LocalTool("ffmpeg", approved),
                )

            self.assertFalse(marker.exists())

    def test_rejects_reversed_whisper_segments_and_words(self) -> None:
        reversed_segments = _valid_payload()
        reversed_segments["segments"] = [
            _valid_payload()["segments"][0],
            {
                "start": 0.5,
                "end": 0.9,
                "text": " earlier",
                "avg_logprob": -0.2,
                "words": [],
            },
        ]
        reversed_words = _valid_payload()
        reversed_words["segments"][0]["words"] = [
            {"word": " einmal", "start": 1.4, "end": 1.9, "probability": 0.8},
            {"word": " Noch", "start": 1.0, "end": 1.4, "probability": 0.9},
        ]
        for label, payload in (("segments", reversed_segments), ("words", reversed_words)):
            with self.subTest(label=label), TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                root.chmod(0o700)
                media = root / "lesson.wav"
                media.write_bytes(b"synthetic media")
                model = root / "model.pt"
                model.write_bytes(b"model fixture")
                output = root / "run"
                output.mkdir(mode=0o700)
                adapter = WhisperCLIAdapter(
                    _fake_whisper(root, payload, suffix=label),
                    WhisperCLISettings(model, "LicenseRef-test", "MIT-test-fixture"),
                )
                with self.assertRaisesRegex(WhisperCLIError, "nondecreasing"):
                    adapter.transcribe(
                        media_path=media,
                        output_directory=output,
                        source_id="source:lesson",
                        stream_id="audio",
                        run_id=f"run:reversed-{label}",
                        raw_artifact_id=f"artifact:raw-reversed-{label}",
                        duration_us=5_000_000,
                    )


def _valid_payload(*, end: float = 2.0) -> dict[str, object]:
    return {
        "text": "Noch einmal",
        "language": "de",
        "segments": [
            {
                "id": 0,
                "start": 1.0,
                "end": end,
                "text": " Noch einmal",
                "avg_logprob": -0.2,
                "words": [
                    {"word": " Noch", "start": 1.0, "end": 1.4, "probability": 0.9},
                    {
                        "word": " einmal",
                        "start": 1.4,
                        "end": min(end, 1.9),
                        "probability": 0.8,
                    },
                ],
            }
        ],
    }


def _fake_whisper(
    root: Path,
    payload: dict[str, object] | None,
    *,
    suffix: str = "valid",
    mutate_media: bool = False,
    mutate_launcher: bool = False,
    mutate_ffmpeg: bool = False,
) -> LocalTool:
    script = root / f"whisper-{suffix}"
    source = (
        "#!/usr/bin/python3\n"
        "import json, os, pathlib, sys\n"
        "audio = pathlib.Path(sys.argv[1])\n"
        "output = pathlib.Path(sys.argv[sys.argv.index('--output_dir') + 1])\n"
    )
    if mutate_media:
        source += "audio.write_bytes(b'mutated media')\n"
    if mutate_launcher:
        source += "pathlib.Path(__file__).write_text('#!/bin/sh\\nexit 0\\n')\n"
    if mutate_ffmpeg:
        source += (
            "ffmpeg = pathlib.Path(os.environ['PATH'].split(os.pathsep)[0]) / 'ffmpeg'\n"
            "ffmpeg.write_text('#!/bin/sh\\nexit 0\\n')\n"
        )
    if payload is not None:
        source += (
            f"payload = {payload!r}\n"
            "(output / (audio.stem + '.json')).write_text("
            "json.dumps(payload), encoding='utf-8')\n"
        )
    script.write_text(source, encoding="utf-8")
    script.chmod(0o700)
    return LocalTool("whisper", script)


def _fake_ffmpeg(root: Path) -> LocalTool:
    script = root / "ffmpeg"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o700)
    return LocalTool("ffmpeg", script)


if __name__ == "__main__":
    unittest.main()
