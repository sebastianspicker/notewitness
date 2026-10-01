from __future__ import annotations

import json
from pathlib import Path


def fake_ffprobe(parent: Path, *, has_audio: bool = True) -> Path:
    path = parent / ("ffprobe-good" if has_audio else "ffprobe-no-audio")
    stream = "audio" if has_audio else "video"
    path.write_text(
        "#!/usr/bin/python3\n"
        "print('{\"streams\":[{\"codec_type\":\""
        f"{stream}"
        "\",\"codec_name\":\"fixture\",\"duration\":\"5.0\","
        "\"sample_rate\":\"16000\",\"channels\":1}],"
        "\"format\":{\"duration\":\"5.0\",\"format_name\":\"wav\"}}')\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def fake_analysis_suite(parent: Path) -> Path:
    path = parent / "analysis-suite"
    note = {
        "state": "ready",
        "hypotheses": [{
            "confidence": 0.91, "frequency_hz": 440.0,
            "hypothesis_id": "note:fixture", "midi_pitch": 69.0,
            "span": {"duration_us": 500_000, "start_us": 0, "stream_id": "audio"},
            "state": "ready",
        }],
        "diagnostics": [], "continuation_token": None,
    }
    alignment = {
        "state": "ready",
        "hypotheses": [{
            "confidence": 0.83, "hypothesis_id": "alignment:fixture",
            "outcome": "aligned", "score_id": "score:fixture",
            "score_position": {"bar": 1, "beat": 1.0},
            "source_hypothesis_ids": ["note:fixture"],
            "span": {"duration_us": 500_000, "start_us": 0, "stream_id": "audio"},
            "state": "ready",
        }],
        "diagnostics": [], "continuation_token": None,
    }
    activity = {
        "state": "ready",
        "hypotheses": [{
            "confidence": 0.88, "hypothesis_id": "activity:overlap",
            "kind": "speech_over_music",
            "span": {"duration_us": 900_000, "start_us": 0, "stream_id": "audio"},
            "state": "ready",
        }],
        "diagnostics": [], "continuation_token": None,
    }
    diarization = {
        "state": "ready",
        "hypotheses": [
            {
                "anonymous_cluster_id": "SPEAKER_00", "confidence": 0.86,
                "hypothesis_id": "speaker:one",
                "span": {"duration_us": 600_000, "start_us": 0, "stream_id": "audio"},
                "state": "ready",
            },
            {
                "anonymous_cluster_id": "SPEAKER_01", "confidence": 0.79,
                "hypothesis_id": "speaker:two",
                "span": {"duration_us": 500_000, "start_us": 400_000, "stream_id": "audio"},
                "state": "ready",
            },
        ],
        "diagnostics": [], "continuation_token": None,
    }
    pitch = {
        "state": "ready",
        "hypotheses": [{
            "confidence": 0.84, "frequency_hz": 442.0,
            "hypothesis_id": "pitch:fixture",
            "span": {"duration_us": 100_000, "start_us": 200_000, "stream_id": "audio"},
            "state": "ready",
        }],
        "diagnostics": [], "continuation_token": None,
    }
    instrument = {
        "state": "ready",
        "hypotheses": [{
            "confidence": 0.92, "hypothesis_id": "instrument:fixture",
            "instrument_label": "piano",
            "span": {"duration_us": 800_000, "start_us": 100_000, "stream_id": "audio"},
            "state": "ready",
        }],
        "diagnostics": [], "continuation_token": None,
    }
    path.write_text(
        "#!/usr/bin/python3\n"
        "import json, pathlib, sys\n"
        "request = json.loads(pathlib.Path(sys.argv[2]).read_text())\n"
        f"note = {note!r}\n"
        f"alignment = {alignment!r}\n"
        f"activity = {activity!r}\n"
        f"diarization = {diarization!r}\n"
        f"pitch = {pitch!r}\n"
        f"instrument = {instrument!r}\n"
        "payloads = {'activity_segmentation': activity, "
        "'anonymous_diarization': diarization, "
        "'note_transcription': note, 'continuous_pitch': pitch, "
        "'instrument_detection': instrument, 'score_alignment': alignment}\n"
        "payload = payloads[request['stage']]\n"
        "print(json.dumps(payload))\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def fake_whisper(parent: Path) -> Path:
    path = parent / "whisper-good"
    payload = {
        "language": "de",
        "segments": [{
            "start": 1.0, "end": 2.0, "text": " Noch einmal", "avg_logprob": -0.2,
            "words": [
                {"word": " Noch", "start": 1.0, "end": 1.4, "probability": 0.9},
                {"word": " einmal", "start": 1.4, "end": 1.9, "probability": 0.8},
            ],
        }],
    }
    path.write_text(
        "#!/usr/bin/python3\n"
        "import json, pathlib, sys\n"
        "if '--help' in sys.argv:\n print('fixture whisper')\n raise SystemExit(0)\n"
        "audio = pathlib.Path(sys.argv[1])\n"
        "output = pathlib.Path(sys.argv[sys.argv.index('--output_dir') + 1])\n"
        f"payload = {payload!r}\n"
        "(output / (audio.stem + '.json')).write_text("
        "json.dumps(payload), encoding='utf-8')\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def fake_ffmpeg(parent: Path) -> Path:
    path = parent / "ffmpeg"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path
