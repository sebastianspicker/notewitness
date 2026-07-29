#!/usr/bin/env python3
"""Map output from an approved local MT3 decoder into JSON v1 hypotheses.

The model identity points to a JSON decoded-event artifact created locally by a
separately approved MT3 executable.  This bridge never invokes a downloader or
guesses a model/API; it only normalizes the already-decoded local output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from ._protocol import BridgeError, bounded_span, confidence, fail, main_request, model_path, response, seconds_to_us
except ImportError:  # direct execution before package installation
    from _protocol import BridgeError, bounded_span, confidence, fail, main_request, model_path, response, seconds_to_us


def run(argv: list[str]) -> None:
    request = main_request(argv, {"note_transcription", "instrument_detection", "instrument_diarization"})
    if request["parameters"]:
        raise BridgeError("MT3 decoded-event bridge accepts no stage parameters")
    events = _decoded_events(request)
    hypotheses = _hypotheses(request, events)
    response(hypotheses)


def _decoded_events(request: dict[str, object]) -> list[dict[str, object]]:
    try:
        artifact = json.loads(Path(model_path(request)).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BridgeError("model artifact must be local decoded-event JSON") from exc
    events = artifact.get("events") if isinstance(artifact, dict) else None
    if not isinstance(events, list):
        raise BridgeError("decoded-event artifact requires an events array")
    if not all(isinstance(event, dict) for event in events):
        raise BridgeError("decoded event must be an object")
    return events


def _hypotheses(request: dict[str, object], events: list[dict[str, object]]) -> list[dict[str, object]]:
    stage = request["stage"]
    hypotheses: list[dict[str, object]] = []
    tracks: dict[str, str] = {}
    for index, event in enumerate(events):
        event_data = _event_data(request, event)
        if stage == "note_transcription":
            hypotheses.append(_note_hypothesis(index, event, event_data))
        elif stage == "instrument_detection":
            hypotheses.append(_instrument_hypothesis(index, event, event_data))
        else:
            hypotheses.append(_track_hypothesis(index, event, event_data, tracks))
    return hypotheses


def _event_data(request: dict[str, object], event: dict[str, object]) -> tuple[dict[str, object], float | None]:
    start = seconds_to_us(event.get("start_s"), "event start_s")
    end = seconds_to_us(event.get("end_s"), "event end_s")
    return bounded_span(request, start, end), confidence(event.get("confidence"))


def _note_hypothesis(index: int, event: dict[str, object], data: tuple[dict[str, object], float | None]) -> dict[str, object]:
    pitch = event.get("midi_pitch")
    if not isinstance(pitch, int) or isinstance(pitch, bool) or not 0 <= pitch <= 127:
        raise BridgeError("decoded note event requires midi_pitch in [0, 127]")
    item: dict[str, object] = {"hypothesis_id": f"mt3:note:{index:06d}", "span": data[0], "state": "ready", "confidence": data[1], "midi_pitch": pitch, "frequency_hz": None}
    track = event.get("track")
    if track is not None:
        if not isinstance(track, str) or not track:
            raise BridgeError("decoded note track must be a non-empty string")
        item["source_track_id"] = track
    return item


def _instrument_hypothesis(index: int, event: dict[str, object], data: tuple[dict[str, object], float | None]) -> dict[str, object]:
    label = event.get("instrument_label")
    if not isinstance(label, str) or not label:
        raise BridgeError("decoded instrument event requires instrument_label")
    return {"hypothesis_id": f"mt3:instrument:{index:06d}", "span": data[0], "state": "ready", "confidence": data[1], "instrument_label": label}


def _track_hypothesis(index: int, event: dict[str, object], data: tuple[dict[str, object], float | None], tracks: dict[str, str]) -> dict[str, object]:
    track = event.get("track")
    if not isinstance(track, str) or not track:
        raise BridgeError("decoded instrument-diarization event requires track")
    label = event.get("instrument_label")
    if not isinstance(label, str) or not label:
        raise BridgeError("decoded instrument-diarization event requires instrument_label")
    anonymous_track = tracks.setdefault(track, f"track-{len(tracks) + 1:02d}")
    return {"hypothesis_id": f"mt3:track:{index:06d}", "span": data[0], "state": "ready", "confidence": data[1], "instrument_label": label, "anonymous_instrument_track_id": anonymous_track}


def main() -> None:
    try:
        run(sys.argv[1:])
    except BridgeError as exc:
        fail(exc)


if __name__ == "__main__":
    main()
