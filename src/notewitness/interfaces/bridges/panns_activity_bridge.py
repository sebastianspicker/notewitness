#!/usr/bin/env python3
"""Normalize pinned PANNs speech and music activity into reviewable spans."""

from __future__ import annotations

import json
import sys
from typing import Any

try:
    from ._protocol import BridgeError, bounded_span, confidence, fail, main_request, response
    from .panns_instrument_bridge import (
        _frame_score,
        _nonnegative_int,
        _plain_sequence,
        _positive_int,
        _require_panns_timing,
        load_sound_event_frames,
    )
except ImportError:  # direct execution before package installation
    from _protocol import BridgeError, bounded_span, confidence, fail, main_request, response
    from panns_instrument_bridge import (
        _frame_score,
        _nonnegative_int,
        _plain_sequence,
        _positive_int,
        _require_panns_timing,
        load_sound_event_frames,
    )


PARAMETERS = {
    "window_us",
    "hop_us",
    "activation_threshold",
    "merge_gap_us",
    "speech_label",
    "music_label",
}
MAX_LABEL_LENGTH = 200


def run(argv: list[str]) -> None:
    request = main_request(argv, {"activity_segmentation"})
    parameters = request["parameters"]
    if set(parameters) != PARAMETERS:
        raise BridgeError(
            "PANNs activity bridge requires window_us, hop_us, activation_threshold, "
            "merge_gap_us, speech_label, and music_label"
        )
    window_us = _positive_int(parameters["window_us"], "window_us")
    hop_us = _positive_int(parameters["hop_us"], "hop_us")
    gap_us = _nonnegative_int(parameters["merge_gap_us"], "merge_gap_us")
    threshold = confidence(parameters["activation_threshold"])
    if threshold is None:
        raise BridgeError("activation_threshold must be a number in [0, 1]")
    _require_panns_timing(window_us, hop_us)
    if len(request["spans"]) != 1:
        raise BridgeError("PANNs activity bridge requires exactly one requested source span")
    speech_label = _label(parameters["speech_label"], "speech_label")
    music_label = _label(parameters["music_label"], "music_label")
    if speech_label == music_label:
        raise BridgeError("speech_label and music_label must be distinct")
    framewise, labels = load_sound_event_frames(request)
    indices = {label: index for index, label in enumerate(labels)}
    if speech_label not in indices or music_label not in indices:
        raise BridgeError("activity labels are absent from the local PANNs checkpoint taxonomy")
    events = _activity_events(
        request,
        framewise,
        labels,
        indices[speech_label],
        indices[music_label],
        window_us,
        hop_us,
        threshold,
        gap_us,
    )
    response(events)


def _activity_events(
    request: dict[str, Any],
    framewise: Any,
    labels: list[str],
    speech_index: int,
    music_index: int,
    window_us: int,
    hop_us: int,
    threshold: float,
    gap_us: int,
) -> list[dict[str, Any]]:
    frames = _plain_sequence(framewise)
    if frames is None:
        raise BridgeError("PANNs framewise_output must be a sequence")
    requested = request["spans"][0]
    anchor = requested["start_us"]
    limit = anchor + requested["duration_us"]
    current: tuple[str, int, int, float] | None = None
    segments: list[tuple[str, int, int, float]] = []
    for frame_index, row in enumerate(frames):
        row = _plain_sequence(row)
        if row is None or len(row) != len(labels):
            raise BridgeError("PANNs framewise output has an invalid label axis")
        scores = [_frame_score(score) for score in row]
        kind = _activity_kind(
            scores[speech_index] >= threshold,
            scores[music_index] >= threshold,
        )
        raw_start = anchor + frame_index * hop_us
        start = max(raw_start, anchor)
        end = min(raw_start + window_us, limit)
        if start >= end:
            continue
        if kind is None:
            if current is not None and start > current[2] + gap_us:
                segments.append(current)
                current = None
            continue
        score = _activity_score(
            kind,
            scores[speech_index],
            scores[music_index],
        )
        if current is not None and kind == current[0] and start <= current[2] + gap_us:
            current = (kind, current[1], max(current[2], end), max(current[3], score))
            continue
        if current is not None:
            segments.append(current)
        current = (kind, start, end, score)
    if current is not None:
        segments.append(current)
    return [
        {
            "hypothesis_id": f"panns:activity:{index:06d}",
            "span": bounded_span(request, start, end),
            "state": "ready",
            "confidence": score,
            "kind": kind,
        }
        for index, (kind, start, end, score) in enumerate(segments)
    ]


def _activity_kind(speech: bool, music: bool) -> str | None:
    if speech and music:
        return "speech_over_music"
    if speech:
        return "speech"
    if music:
        return "music"
    return None


def _activity_score(kind: str, speech_score: float, music_score: float) -> float:
    if kind == "speech_over_music":
        return min(speech_score, music_score)
    return speech_score if kind == "speech" else music_score


def _label(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_LABEL_LENGTH:
        raise BridgeError(f"{name} must be a bounded non-empty string")
    return value


if __name__ == "__main__":
    try:
        run(sys.argv[1:])
    except BridgeError as exc:
        fail(exc)
    except (RuntimeError, UnicodeError, json.JSONDecodeError):
        fail(BridgeError("local PANNs activity provider could not run"))
