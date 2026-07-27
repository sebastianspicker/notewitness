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
    try:
        artifact = json.loads(Path(model_path(request)).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BridgeError("model artifact must be local decoded-event JSON") from exc
    events = artifact.get("events") if isinstance(artifact, dict) else None
    if not isinstance(events, list):
        raise BridgeError("decoded-event artifact requires an events array")
    stage = request["stage"]
    hypotheses = []
    tracks: dict[str, str] = {}
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise BridgeError("decoded event must be an object")
        start = seconds_to_us(event.get("start_s"), "event start_s")
        end = seconds_to_us(event.get("end_s"), "event end_s")
        span = bounded_span(request, start, end)
        certainty = confidence(event.get("confidence"))
        if stage == "note_transcription":
            pitch = event.get("midi_pitch")
            if not isinstance(pitch, int) or isinstance(pitch, bool) or not 0 <= pitch <= 127:
                raise BridgeError("decoded note event requires midi_pitch in [0, 127]")
            item = {"hypothesis_id": f"mt3:note:{index:06d}", "span": span,
                    "state": "ready", "confidence": certainty, "midi_pitch": pitch,
                    "frequency_hz": None}
            track = event.get("track")
            if track is not None:
                if not isinstance(track, str) or not track:
                    raise BridgeError("decoded note track must be a non-empty string")
                item["source_track_id"] = track
            hypotheses.append(item)
        elif stage == "instrument_detection":
            label = event.get("instrument_label")
            if not isinstance(label, str) or not label:
                raise BridgeError("decoded instrument event requires instrument_label")
            hypotheses.append({"hypothesis_id": f"mt3:instrument:{index:06d}", "span": span,
                               "state": "ready", "confidence": certainty, "instrument_label": label})
        else:
            track = event.get("track")
            if not isinstance(track, str) or not track:
                raise BridgeError("decoded instrument-diarization event requires track")
            label = event.get("instrument_label")
            if not isinstance(label, str) or not label:
                raise BridgeError("decoded instrument-diarization event requires instrument_label")
            if track not in tracks:
                tracks[track] = f"track-{len(tracks) + 1:02d}"
            hypotheses.append({"hypothesis_id": f"mt3:track:{index:06d}", "span": span,
                               "state": "ready", "confidence": certainty,
                               "instrument_label": label,
                               "anonymous_instrument_track_id": tracks[track]})
    response(hypotheses)


def main() -> None:
    try:
        run(sys.argv[1:])
    except BridgeError as exc:
        fail(exc)


if __name__ == "__main__":
    main()
