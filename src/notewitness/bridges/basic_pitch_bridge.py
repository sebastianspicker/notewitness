#!/usr/bin/env python3
"""Map a locally installed Basic Pitch runtime to analysis-suite JSON v1."""

from __future__ import annotations

import sys
import json
import math

try:
    from ._protocol import BridgeError, MAX_ITEMS, bounded_span, confidence, fail, main_request, media_path, model_path, response, seconds_to_us
except ImportError:  # direct execution before package installation
    from _protocol import BridgeError, MAX_ITEMS, bounded_span, confidence, fail, main_request, media_path, model_path, response, seconds_to_us


def run(argv: list[str]) -> None:
    request = main_request(argv, {"note_transcription"})
    if request["parameters"]:
        raise BridgeError("Basic Pitch bridge accepts no stage parameters")
    try:
        from basic_pitch.inference import predict
    except ImportError as exc:
        raise BridgeError("Basic Pitch is not installed in this local runtime") from exc
    try:
        _model_output, _midi_data, events = predict(media_path(request), model_or_model_path=model_path(request))
    except TypeError:
        # Some local Basic Pitch releases use the positional model argument.
        _model_output, _midi_data, events = predict(media_path(request), model_path(request))
    hypotheses = []
    for index, event in enumerate(events):
        if not isinstance(event, (tuple, list)) or len(event) != 5:
            raise BridgeError("Basic Pitch emitted an unsupported note event")
        start_us = seconds_to_us(event[0], "note start")
        end_us = seconds_to_us(event[1], "note end")
        pitch = event[2]
        if not isinstance(pitch, int) or isinstance(pitch, bool) or not 0 <= pitch <= 127:
            raise BridgeError("Basic Pitch emitted an invalid MIDI pitch")
        bends = _pitch_bends(event[4])
        item = {"hypothesis_id": f"basic-pitch:note:{index:06d}",
                "span": bounded_span(request, start_us, end_us), "state": "ready",
                # Basic Pitch amplitude is not a calibrated confidence score.
                "confidence": None, "midi_pitch": pitch, "frequency_hz": None,
                "amplitude": confidence(event[3]), "pitch_bend_values": bends}
        if bends:
            # Basic Pitch note-event bends are provider-specific semitone offsets.
            item["pitch_bend_unit"] = "basic-pitch:semitone-offset"
        hypotheses.append(item)
    response(hypotheses)


def _pitch_bends(value: object) -> list[float]:
    if isinstance(value, (str, bytes)):
        raise BridgeError("Basic Pitch emitted unsupported pitch bends")
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        value = tolist()
    if not isinstance(value, (list, tuple)) or len(value) > MAX_ITEMS:
        raise BridgeError("Basic Pitch emitted unsupported pitch bends")
    bends: list[float] = []
    for bend in value:
        if isinstance(bend, bool):
            raise BridgeError("Basic Pitch pitch bends must be finite numbers")
        try:
            numeric = float(bend)
        except (TypeError, ValueError) as exc:
            raise BridgeError("Basic Pitch pitch bends must be finite numbers") from exc
        if not math.isfinite(numeric):
            raise BridgeError("Basic Pitch pitch bends must be finite numbers")
        bends.append(numeric)
    return bends


if __name__ == "__main__":
    try:
        run(sys.argv[1:])
    except BridgeError as exc:
        fail(exc)
    except (RuntimeError, UnicodeError, json.JSONDecodeError):
        fail(BridgeError("local Basic Pitch provider could not run"))
