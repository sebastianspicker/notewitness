"""Black-box contract checks for the optional local-provider bridge scripts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BRIDGES = ROOT / "src" / "notewitness" / "bridges"


class ProviderBridgeTests(unittest.TestCase):
    def test_basic_pitch_maps_local_api_note_events(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            fake = directory / "fake"
            (fake / "basic_pitch").mkdir(parents=True)
            (fake / "basic_pitch" / "__init__.py").write_text("")
            (fake / "basic_pitch" / "inference.py").write_text(
                "def predict(audio, model_or_model_path=None):\n"
                "    return None, None, [(0.1, 0.3, 60, 0.8, [-0.2, 0.1]), (0.3, 0.5, 64, 0.6, [])]\n"
            )
            request = _request(directory, "note_transcription")
            output = _run(directory, "basic_pitch_bridge.py", request, fake)
        self.assertEqual("ready", output["state"])
        self.assertEqual([60, 64], [item["midi_pitch"] for item in output["hypotheses"]])
        self.assertEqual(100_000, output["hypotheses"][0]["span"]["start_us"])
        self.assertEqual(0.8, output["hypotheses"][0]["amplitude"])
        self.assertEqual([-0.2, 0.1], output["hypotheses"][0]["pitch_bend_values"])
        self.assertEqual("basic-pitch:semitone-offset", output["hypotheses"][0]["pitch_bend_unit"])
        self.assertNotIn("velocity", output["hypotheses"][0])
        self.assertIsNone(output["hypotheses"][0]["confidence"])
        self.assertNotIn("pitch_bend_unit", output["hypotheses"][1])

    def test_pyannote_uses_current_overlapping_wrapper_and_exact_count(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            fake = directory / "fake"
            (fake / "pyannote" / "audio").mkdir(parents=True)
            (fake / "pyannote" / "__init__.py").write_text("")
            (fake / "pyannote" / "audio" / "__init__.py").write_text(
                "class Turn:\n"
                "    def __init__(self, start, end): self.start, self.end = start, end\n"
                "class Annotation:\n"
                "    def itertracks(self, yield_label=False):\n"
                "        return iter([(Turn(0.0, 1.0), 'x', 'Z'), (Turn(0.4, 1.2), 'y', 'A')])\n"
                "class Output:\n"
                "    speaker_diarization = Annotation()\n"
                "    exclusive_speaker_diarization = object()\n"
                "class Pipeline:\n"
                "    @classmethod\n"
                "    def from_pretrained(cls, artifact): return cls()\n"
                "    def __call__(self, media, num_speakers=None):\n"
                "        if num_speakers != 2: raise ValueError('expected exact count')\n"
                "        return Output()\n"
            )
            request = _request(directory, "anonymous_diarization", parameters={
                "detect_overlap": True, "diarization_mode": "exact", "exact_speaker_count": 2,
            })
            output = _run(directory, "pyannote_bridge.py", request, fake)
        segments = output["hypotheses"]
        self.assertEqual(["speaker-01", "speaker-02"], [item["anonymous_cluster_id"] for item in segments])
        self.assertEqual(400_000, segments[1]["span"]["start_us"])
        self.assertGreater(segments[0]["span"]["start_us"] + segments[0]["span"]["duration_us"], segments[1]["span"]["start_us"])

    def test_pyannote_off_is_empty_without_optional_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            output = _run(directory, "pyannote_bridge.py", _request(directory, "anonymous_diarization", parameters={
                "detect_overlap": False, "diarization_mode": "off", "exact_speaker_count": None,
            }))
        self.assertEqual("not_detected", output["state"])
        self.assertEqual([], output["hypotheses"])
        self.assertEqual(["diarization disabled by request"], output["diagnostics"])

    def test_mt3_decoded_events_maps_notes_instruments_and_overlapping_tracks(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            artifact = directory / "decoded.json"
            artifact.write_text(json.dumps({"events": [
                {"start_s": 0.0, "end_s": 1.0, "midi_pitch": 60, "instrument_label": "piano", "track": "left", "confidence": 0.9},
                {"start_s": 0.5, "end_s": 1.5, "midi_pitch": 67, "instrument_label": "violin", "track": "right", "confidence": 0.7},
            ]}))
            for stage in ("note_transcription", "instrument_detection", "instrument_diarization"):
                request = _request(directory, stage, model=artifact)
                output = _run(directory, "mt3_decoded_events_bridge.py", request)
                self.assertEqual(2, len(output["hypotheses"]))
                self.assertEqual("ready", output["state"])
            self.assertEqual(["track-01", "track-02"], [item["anonymous_instrument_track_id"] for item in output["hypotheses"]])
            self.assertEqual(["piano", "violin"], [item["instrument_label"] for item in output["hypotheses"]])
            note_output = _run(directory, "mt3_decoded_events_bridge.py", _request(directory, "note_transcription", model=artifact))
            self.assertEqual(["left", "right"], [item["source_track_id"] for item in note_output["hypotheses"]])

    def test_panns_merges_framewise_activity_and_keeps_overlapping_instruments(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            fake = directory / "fake"
            _write_fake_panns(fake, "[[.99, .8, .1], [.99, .9, .7], [.99, .2, .8]]")
            parameters = _panns_parameters()
            output = _run(directory, "panns_instrument_bridge.py",
                          _request(directory, "instrument_diarization", parameters=parameters), fake)
            detection = _run(directory, "panns_instrument_bridge.py",
                             _request(directory, "instrument_detection", parameters=parameters), fake)
            load_call = json.loads((directory / "librosa-call.json").read_text())
        self.assertEqual(["piano", "violin"], [item["instrument_label"] for item in output["hypotheses"]])
        self.assertEqual(["instrument-01", "instrument-02"], [item["anonymous_instrument_track_id"] for item in output["hypotheses"]])
        self.assertEqual(20_000, output["hypotheses"][0]["span"]["duration_us"])
        self.assertTrue(all("anonymous_instrument_track_id" not in item for item in detection["hypotheses"]))
        self.assertEqual({"sr": 32000, "mono": True, "offset": 0.0, "duration": 2.0}, load_call)

    def test_panns_clamps_padded_frames_to_a_nonzero_requested_span(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            fake = directory / "fake"
            _write_fake_panns(
                fake,
                "[[.1, .8, .1], [.1, .9, .1], [.1, .1, .8], "
                "[.1, .1, .8], [.1, .9, .9], [.1, .9, .9]]",
            )
            parameters = _panns_parameters()
            output = _run(directory, "panns_instrument_bridge.py", _request(
                directory, "instrument_detection", parameters=parameters,
                start_us=1_000_000, duration_us=1_000_000), fake)
            load_call = json.loads((directory / "librosa-call.json").read_text())
        spans = [item["span"] for item in output["hypotheses"]]
        self.assertEqual([
            {"stream_id": "audio", "start_us": 1_000_000, "duration_us": 20_000},
            {"stream_id": "audio", "start_us": 1_020_000, "duration_us": 40_000},
            {"stream_id": "audio", "start_us": 1_040_000, "duration_us": 20_000},
        ], spans)
        self.assertTrue(all(1_000_000 <= item["start_us"] and
                            item["start_us"] + item["duration_us"] <= 2_000_000
                            for item in spans))
        self.assertEqual(1.0, load_call["offset"])
        self.assertEqual(1.0, load_call["duration"])

    def test_panns_activity_distinguishes_speech_music_and_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            fake = directory / "fake"
            _write_fake_panns(
                fake,
                "[[.8, .1, .1], [.9, .6, .1], [.1, .8, .1]]",
                labels=("Speech", "Music", "Piano"),
            )
            parameters = {
                "window_us": 10_000,
                "hop_us": 10_000,
                "activation_threshold": 0.5,
                "merge_gap_us": 0,
                "speech_label": "Speech",
                "music_label": "Music",
            }
            output = _run(
                directory,
                "panns_activity_bridge.py",
                _request(directory, "activity_segmentation", parameters=parameters),
                fake,
            )
            dispatched = _run_dispatcher_raw(
                directory,
                _request(directory, "activity_segmentation", parameters=parameters),
                fake,
            )
        self.assertEqual(
            ["speech", "speech_over_music", "music"],
            [item["kind"] for item in output["hypotheses"]],
        )
        self.assertTrue(all(item["span"]["duration_us"] == 10_000 for item in output["hypotheses"]))
        self.assertEqual(0.6, output["hypotheses"][1]["confidence"])
        self.assertEqual(0, dispatched.returncode, dispatched.stderr)
        self.assertEqual(output["hypotheses"], json.loads(dispatched.stdout)["hypotheses"])

    def test_panns_suppresses_upstream_stdout_and_rejects_false_frame_timing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            fake = directory / "fake"
            _write_fake_panns(fake, "[[.1, .8, .1]]")
            request = _request(
                directory,
                "instrument_detection",
                parameters=_panns_parameters(),
            )
            result = _run_raw(directory, "panns_instrument_bridge.py", request, fake)
            invalid = _run_raw(
                directory,
                "panns_instrument_bridge.py",
                _request(
                    directory,
                    "instrument_detection",
                    parameters={**_panns_parameters(), "hop_us": 500_000},
                ),
                fake,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        self.assertNotIn("Checkpoint path", result.stdout)
        self.assertEqual("ready", json.loads(result.stdout)["state"])
        self.assertEqual(2, invalid.returncode)
        self.assertIn("must both be 10000", invalid.stderr)

    def test_panns_rejects_unapproved_checkpoint_taxonomy(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            fake = directory / "fake"
            _write_fake_panns(fake, "[[.1, .8, .1]]")
            parameters = {**_panns_parameters(), "instrument_labels": ["piano", "flute"]}
            result = _run_raw(directory, "panns_instrument_bridge.py", _request(
                directory, "instrument_detection", parameters=parameters), fake)
        self.assertEqual(2, result.returncode)
        self.assertEqual(
            "provider bridge failed: instrument_labels contains a label absent from the local "
            "PANNs checkpoint taxonomy\n",
            result.stderr,
        )

    def test_basic_pitch_runtime_failure_is_stable_and_does_not_leak_provider_text(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            fake = directory / "fake"
            (fake / "basic_pitch").mkdir(parents=True)
            (fake / "basic_pitch" / "__init__.py").write_text("")
            (fake / "basic_pitch" / "inference.py").write_text(
                "def predict(audio, model_or_model_path=None):\n"
                "    raise RuntimeError('/private/provider-detail')\n"
            )
            result = _run_raw(directory, "basic_pitch_bridge.py", _request(directory, "note_transcription"), fake)
        self.assertEqual(2, result.returncode)
        self.assertEqual("provider bridge failed: local Basic Pitch provider could not run\n", result.stderr)
        self.assertNotIn("provider-detail", result.stderr)

    def test_panns_runtime_failure_is_a_stable_bridge_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            fake = directory / "fake"
            _write_fake_panns(fake, "raise RuntimeError('/private/provider-detail')", expression=False)
            parameters = _panns_parameters()
            result = _run_raw(directory, "panns_instrument_bridge.py", _request(
                directory, "instrument_detection", parameters=parameters), fake)
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertEqual("provider bridge failed: local PANNs checkpoint could not run\n", result.stderr)
        self.assertNotIn("provider-detail", result.stderr)

    def test_mt3_malformed_utf8_is_a_bounded_bridge_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            artifact = directory / "decoded.json"
            artifact.write_bytes(b"\xff")
            result = _run_raw(directory, "mt3_decoded_events_bridge.py",
                              _request(directory, "note_transcription", model=artifact))
        self.assertEqual(2, result.returncode)
        self.assertEqual("provider bridge failed: model artifact must be local decoded-event JSON\n", result.stderr)

    def test_dispatcher_normalizes_selected_provider_runtime_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            fake = directory / "fake"
            (fake / "basic_pitch").mkdir(parents=True)
            (fake / "basic_pitch" / "__init__.py").write_text("")
            (fake / "basic_pitch" / "inference.py").write_text(
                "def predict(audio, model_or_model_path=None):\n"
                "    raise RuntimeError('/private/provider-detail')\n"
            )
            result = _run_dispatcher_raw(directory, _request(directory, "note_transcription"), fake)
        self.assertEqual(2, result.returncode)
        self.assertEqual("provider bridge failed: local provider bridge could not run\n", result.stderr)
        self.assertNotIn("provider-detail", result.stderr)

    def test_missing_optional_provider_fails_without_download(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            result = _run_raw(directory, "basic_pitch_bridge.py", _request(directory, "note_transcription"))
        self.assertEqual(2, result.returncode)
        self.assertIn("not installed", result.stderr)


def _request(directory: Path, stage: str, *, parameters: dict[str, object] | None = None, model: Path | None = None,
             start_us: int = 0, duration_us: int = 2_000_000) -> dict[str, object]:
    media = directory / "media.wav"
    media.write_bytes(b"media")
    model = model or (directory / "model.bin")
    if not model.exists():
        model.write_bytes(b"model")
    return {"schema_version": 1, "stage": stage, "version": "test", "generator_id": "generator:test",
            "model": _identity("model:test", model), "job_id": "job:test", "source_id": "source:test",
            "media": _identity("source:test", media), "score": None,
            "spans": [{"stream_id": "audio", "start_us": start_us, "duration_us": duration_us}],
            "parameters": parameters or {}, "continuation_token": None}


def _identity(source_id: str, path: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {"source_id": source_id, "path": str(path), "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}


def _panns_parameters() -> dict[str, object]:
    return {
        "window_us": 10_000,
        "hop_us": 10_000,
        "activation_threshold": 0.5,
        "merge_gap_us": 0,
        "instrument_labels": ["piano", "violin"],
    }


def _write_fake_panns(
    fake: Path,
    inference: str,
    *,
    expression: bool = True,
    labels: tuple[str, ...] = ("Speech", "piano", "violin"),
) -> None:
    fake.mkdir()
    (fake / "librosa.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "class Audio:\n"
        "    def __len__(self): return 4\n"
        "    def __getitem__(self, key):\n"
        "        if key != (None, slice(None, None, None)): raise ValueError('expected one batch')\n"
        "        return [['samples']]\n"
        "class Core:\n"
        "    def load(self, path, sr, mono, offset, duration):\n"
        "        Path('librosa-call.json').write_text(json.dumps({\n"
        "            'sr': sr, 'mono': mono, 'offset': offset, 'duration': duration}))\n"
        "        return Audio(), sr\n"
        "core = Core()\n"
    )
    body = f"        return [{inference}]\n" if expression else f"        {inference}\n"
    (fake / "panns_inference.py").write_text(
        f"labels = {list(labels)!r}\n"
        "class SoundEventDetection:\n"
        "    def __init__(self, checkpoint_path, device):\n"
        "        print('Checkpoint path: ' + checkpoint_path)\n"
        "        if device != 'cpu': raise ValueError('expected cpu')\n"
        "        print('Using CPU.')\n"
        "    def inference(self, audio):\n"
        "        if audio != [['samples']]: raise ValueError('expected decoded batch')\n"
        + body
    )


def _run(directory: Path, bridge: str, request: dict[str, object], fake: Path | None = None) -> dict[str, object]:
    result = _run_raw(directory, bridge, request, fake)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def _run_raw(directory: Path, bridge: str, request: dict[str, object], fake: Path | None = None) -> subprocess.CompletedProcess[str]:
    (directory / "request.json").write_text(json.dumps(request))
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if fake is not None:
        environment["PYTHONPATH"] = str(fake)
    return subprocess.run([sys.executable, str(BRIDGES / bridge), "--request", "request.json"], cwd=directory,
                          env=environment, text=True, capture_output=True, check=False)


def _run_dispatcher_raw(directory: Path, request: dict[str, object], fake: Path) -> subprocess.CompletedProcess[str]:
    (directory / "request.json").write_text(json.dumps(request))
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = os.pathsep.join((str(fake), str(ROOT / "src")))
    return subprocess.run([sys.executable, "-m", "notewitness.bridges.dispatcher", "--request", "request.json"], cwd=directory,
                          env=environment, text=True, capture_output=True, check=False)
