#!/usr/bin/env python3
"""Generate a private sample project with synthetic audio and a populated review queue.

The WAV is synthesized at run time (public hygiene forbids committing media), so
the project lives outside the repository checkout.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import tempfile
import wave
from pathlib import Path

from notewitness.projects.initialize import initialize_project
from notewitness.projects.media import ingest_media
from notewitness.projects.store import ProjectStore

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_RATE = 16_000
DURATION_S = 24
GENERATOR_ID = "generator:sample-machine"

# (name, event type, actor, start_s, duration_s, text, confidence)
SUGGESTIONS = (
    (
        "release",
        "speech",
        "actor:teacher",
        1,
        4,
        "Release the C♯ at the end of bar 18; keep the bow moving.",
        0.86,
    ),
    (
        "demonstration",
        "music",
        "actor:teacher",
        6,
        5,
        "Violin demonstration for bars 18–21.",
        0.79,
    ),
    (
        "listen",
        "speech",
        "actor:teacher",
        12,
        4,
        "Listen for the release before repeating bar 21.",
        0.74,
    ),
    (
        "repeat",
        "music",
        "actor:student",
        17,
        5,
        "Compare the next repetition with the C♯ release.",
        0.71,
    ),
)

# (start_s, duration_s, frequency_hz); gaps between tones are silence.
TONES = ((1, 4, 220.0), (6, 5, 440.0), (12, 4, 330.0), (17, 5, 523.25))


def write_wav(path: Path) -> None:
    """Write a mono 16-bit WAV of sine tones separated by silence."""
    frames = bytearray()
    for index in range(SAMPLE_RATE * DURATION_S):
        second = index / SAMPLE_RATE
        sample = 0.0
        for start, length, frequency in TONES:
            if start <= second < start + length:
                sample = 0.3 * math.sin(2 * math.pi * frequency * second)
        frames += struct.pack("<h", int(sample * 32767))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(bytes(frames))


def check_destination(destination: Path) -> Path:
    resolved = destination.resolve()
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"destination already exists: {destination}")
    if resolved == ROOT or ROOT in resolved.parents:
        raise ValueError(f"destination must be outside the repository: {resolved}")
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="new project directory")
    args = parser.parse_args()
    try:
        destination = check_destination(args.destination)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    initialize_project(destination, name="Sample lesson (synthetic audio)")
    with tempfile.TemporaryDirectory() as scratch:
        # Ingest refuses symlinked paths, and temp roots may be symlinks.
        wav_path = Path(scratch).resolve() / "sample-lesson.wav"
        write_wav(wav_path)
        imported = ingest_media(destination, wav_path, create_restricted_rights=True)

    def publish(payload: dict[str, object]) -> None:
        payload["actors"].extend([
            {"id": "actor:teacher", "role": "teacher", "visibility": "restricted", "instrument_role": "violin"},
            {"id": "actor:student", "role": "student", "visibility": "restricted", "instrument_role": "violin"},
        ])
        payload["generators"].append(
            {
                "id": GENERATOR_ID,
                "kind": "machine",
                "name": "NoteWitness synthetic sample",
                "version": "0.1.0",
                "model": "synthetic-sample",
                "weight_hash_state": "not_applicable:synthetic-sample",
            }
        )
        for name, event_type, actor_id, start, length, text, confidence in SUGGESTIONS:
            payload["targets"].append(
                {
                    "id": f"target:sample-{name}",
                    "source_id": imported.source_id,
                    "selector": {
                        "stream_id": "audio",
                        "start_us": start * 1_000_000,
                        "duration_us": length * 1_000_000,
                        "spatial": None,
                    },
                    "musical_selector": None,
                    "alignment_state": "unknown",
                }
            )
            payload["events"].append(
                {
                    "id": f"event:sample-{name}",
                    "type": event_type,
                    "scope": "evidence",
                    "actor_id": actor_id,
                    "target_ids": [f"target:sample-{name}"],
                    "body": {"format": "text", "value": text},
                    "alternatives": [],
                    "generator_id": GENERATOR_ID,
                    "rights_id": imported.rights_id,
                    "layer": "normalized_hypothesis",
                    "confidence": {"kind": "adapter_reported", "value": confidence},
                    "review_status": "machine_suggested",
                }
            )

    ProjectStore(destination).mutate(publish)
    print(
        json.dumps(
            {
                "project": str(destination),
                "media_source": imported.source_id,
                "suggestions": len(SUGGESTIONS),
                "open_with": f"PYTHONPATH=src python3 -m notewitness workbench {destination}",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
