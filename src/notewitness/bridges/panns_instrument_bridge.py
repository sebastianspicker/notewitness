#!/usr/bin/env python3
"""Normalize local PANNs framewise instrument activity into temporal spans."""

from __future__ import annotations

import sys
import json
import math
import os
from contextlib import contextmanager
from typing import Any, Iterator

try:
    from ._protocol import BridgeError, bounded_span, confidence, fail, main_request, media_path, model_path, response
except ImportError:  # direct execution before package installation
    from _protocol import BridgeError, bounded_span, confidence, fail, main_request, media_path, model_path, response


PARAMETERS = {
    "window_us",
    "hop_us",
    "activation_threshold",
    "merge_gap_us",
    "instrument_labels",
}
SAMPLE_RATE_HZ = 32_000
FRAME_HOP_SAMPLES = 320
FRAME_HOP_US = FRAME_HOP_SAMPLES * 1_000_000 // SAMPLE_RATE_HZ
FRAME_BIN_US = FRAME_HOP_US
MAX_INSTRUMENT_LABELS = 128
MAX_LABEL_LENGTH = 200


def run(argv: list[str]) -> None:
    request = main_request(argv, {"instrument_detection", "instrument_diarization"})
    parameters = request["parameters"]
    if set(parameters) != PARAMETERS:
        raise BridgeError(
            "PANNs bridge requires window_us, hop_us, activation_threshold, "
            "merge_gap_us, and instrument_labels"
        )
    window_us = _positive_int(parameters["window_us"], "window_us")
    hop_us = _positive_int(parameters["hop_us"], "hop_us")
    gap_us = _nonnegative_int(parameters["merge_gap_us"], "merge_gap_us")
    threshold = confidence(parameters["activation_threshold"])
    if threshold is None:
        raise BridgeError("activation_threshold must be a number in [0, 1]")
    _require_panns_timing(window_us, hop_us)
    if len(request["spans"]) != 1:
        raise BridgeError("PANNs bridge requires exactly one requested source span")
    approved_labels = _approved_instrument_labels(parameters["instrument_labels"])
    framewise, provider_labels = load_sound_event_frames(request)
    selected_indices = _selected_label_indices(provider_labels, approved_labels)
    events = _merge_active_frames(
        request,
        framewise,
        provider_labels,
        selected_indices,
        window_us,
        hop_us,
        threshold,
        gap_us,
    )
    response(events)


def load_sound_event_frames(request: dict[str, Any]) -> tuple[list[Any], list[str]]:
    """Run the pinned PANNs SED API for one already-validated source span."""
    try:
        with _suppress_provider_output():
            import librosa
            from panns_inference import SoundEventDetection, labels

            provider_labels = _provider_labels(labels)
            requested = request["spans"][0]
            audio, sample_rate = librosa.core.load(
                media_path(request),
                sr=SAMPLE_RATE_HZ,
                mono=True,
                offset=requested["start_us"] / 1_000_000,
                duration=requested["duration_us"] / 1_000_000,
            )
            if sample_rate != SAMPLE_RATE_HZ or len(audio) == 0:
                raise BridgeError(
                    "local PANNs source span could not be decoded at 32000 Hz"
                )
            detector = SoundEventDetection(
                checkpoint_path=model_path(request),
                device="cpu",
            )
            framewise = _single_frame_batch(detector.inference(audio[None, :]))
    except ImportError as exc:
        raise BridgeError(
            "panns_inference and librosa are not installed in this local runtime"
        ) from exc
    except BridgeError:
        raise
    except Exception as exc:
        raise BridgeError("local PANNs checkpoint could not run") from exc
    return framewise, provider_labels


@contextmanager
def _suppress_provider_output() -> Iterator[None]:
    """Keep third-party diagnostics outside the one-document stdout protocol."""

    saved: list[tuple[int, int]] = []
    sink: int | None = None
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        for descriptor in (1, 2):
            saved.append((descriptor, os.dup(descriptor)))
        sink = os.open(os.devnull, os.O_WRONLY)
        for descriptor, _saved_descriptor in saved:
            os.dup2(sink, descriptor)
    except OSError as exc:
        _restore_provider_descriptors(saved, suppress_errors=True)
        _close_provider_descriptors(saved, sink)
        raise BridgeError("local PANNs output isolation is unavailable") from exc
    try:
        yield
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            try:
                _restore_provider_descriptors(saved)
            finally:
                _close_provider_descriptors(saved, sink)


def _restore_provider_descriptors(
    saved: list[tuple[int, int]], *, suppress_errors: bool = False
) -> None:
    for descriptor, saved_descriptor in saved:
        try:
            os.dup2(saved_descriptor, descriptor)
        except OSError:
            if not suppress_errors:
                raise


def _close_provider_descriptors(
    saved: list[tuple[int, int]], sink: int | None
) -> None:
    for _descriptor, saved_descriptor in saved:
        os.close(saved_descriptor)
    if sink is not None:
        os.close(sink)


def _require_panns_timing(window_us: int, hop_us: int) -> None:
    if window_us != FRAME_BIN_US or hop_us != FRAME_HOP_US:
        raise BridgeError(
            "window_us and hop_us must both be 10000 for the packaged "
            "32000 Hz PANNs SoundEventDetection bridge"
        )


def _merge_active_frames(request: dict[str, Any], framewise: Any, labels: list[str],
                         selected_indices: tuple[int, ...], window_us: int,
                         hop_us: int, threshold: float, gap_us: int) -> list[dict[str, Any]]:
    framewise = _plain_sequence(framewise)
    if framewise is None:
        raise BridgeError("PANNs framewise_output must be a sequence")
    anchor = request["spans"][0]["start_us"]
    active: dict[str, tuple[int, int, float, int]] = {}
    merged: list[tuple[str, int, int, float, int]] = []
    for frame_index, row in enumerate(framewise):
        interval = _frame_interval(request, anchor, frame_index, window_us, hop_us)
        if interval is None:
            continue
        numeric_row = _numeric_frame_row(row, len(labels))
        _merge_frame_row(
            active,
            merged,
            labels,
            selected_indices,
            numeric_row,
            interval,
            threshold,
            gap_us,
        )
    merged.extend((label, *value) for label, value in active.items())
    return _instrument_hypotheses(request, merged)


def _frame_interval(
    request: dict[str, Any],
    anchor: int,
    frame_index: int,
    window_us: int,
    hop_us: int,
) -> tuple[int, int] | None:
    raw_start = anchor + frame_index * hop_us
    requested = request["spans"][0]
    start = max(raw_start, requested["start_us"])
    end = min(
        raw_start + window_us,
        requested["start_us"] + requested["duration_us"],
    )
    return None if start >= end else (start, end)


def _numeric_frame_row(row: Any, label_count: int) -> list[float]:
    values = _plain_sequence(row)
    if values is None or len(values) != label_count:
        raise BridgeError("PANNs framewise output has an invalid label axis")
    return [_frame_score(score) for score in values]


def _merge_frame_row(
    active: dict[str, tuple[int, int, float, int]],
    merged: list[tuple[str, int, int, float, int]],
    labels: list[str],
    selected_indices: tuple[int, ...],
    scores: list[float],
    interval: tuple[int, int],
    threshold: float,
    gap_us: int,
) -> None:
    start, end = interval
    for label_index in selected_indices:
        numeric_score = scores[label_index]
        if numeric_score < threshold:
            continue
        label = labels[label_index]
        prior = active.get(label)
        if prior is not None and start <= prior[1] + gap_us:
            active[label] = (
                prior[0],
                max(prior[1], end),
                max(prior[2], numeric_score),
                prior[3],
            )
        else:
            if prior is not None:
                merged.append((label, *prior))
            active[label] = (start, end, numeric_score, label_index)


def _instrument_hypotheses(
    request: dict[str, Any], merged: list[tuple[str, int, int, float, int]]
) -> list[dict[str, Any]]:
    track_ids = {
        label: f"instrument-{index + 1:02d}"
        for index, label in enumerate(sorted({item[0] for item in merged}))
    }
    hypotheses: list[dict[str, Any]] = []
    ordered = sorted(merged, key=lambda item: (item[1], item[0]))
    for index, (label, start, end, score, _label_index) in enumerate(ordered):
        item = {
            "hypothesis_id": f"panns:instrument:{index:06d}",
            "span": bounded_span(request, start, end),
            "state": "ready",
            "confidence": score,
            "instrument_label": label,
        }
        # The ID is local to this run and denotes a temporal activity class, not a performer identity.
        if request["stage"] == "instrument_diarization":
            item["anonymous_instrument_track_id"] = track_ids[label]
        hypotheses.append(item)
    return hypotheses


def _frame_score(score: Any) -> float:
    if isinstance(score, bool):
        raise BridgeError("PANNs frame score must be numeric")
    try:
        numeric_score = float(score)
    except (TypeError, ValueError) as exc:
        raise BridgeError("PANNs frame score must be numeric") from exc
    if not math.isfinite(numeric_score) or not 0 <= numeric_score <= 1:
        raise BridgeError("PANNs frame score must be in [0, 1]")
    return numeric_score


def _single_frame_batch(value: Any) -> list[Any]:
    batches = _plain_sequence(value)
    if batches is None or len(batches) != 1:
        raise BridgeError("PANNs framewise output must contain exactly one batch")
    frames = _plain_sequence(batches[0])
    if frames is None:
        raise BridgeError("PANNs framewise output must contain a frame sequence")
    return frames


def _provider_labels(value: Any) -> list[str]:
    labels = _plain_sequence(value)
    if labels is None or not labels or len(labels) > 10_000:
        raise BridgeError("local PANNs labels are unavailable")
    if any(not isinstance(label, str) or not label or len(label) > MAX_LABEL_LENGTH for label in labels):
        raise BridgeError("PANNs label must be a bounded non-empty string")
    if len(set(labels)) != len(labels):
        raise BridgeError("PANNs labels must be unique")
    return labels


def _approved_instrument_labels(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > MAX_INSTRUMENT_LABELS:
        raise BridgeError("instrument_labels must be a non-empty bounded array")
    if any(not isinstance(label, str) or not label or len(label) > MAX_LABEL_LENGTH for label in value):
        raise BridgeError("instrument_labels entries must be bounded non-empty strings")
    if len(set(value)) != len(value):
        raise BridgeError("instrument_labels entries must be unique")
    return tuple(value)


def _selected_label_indices(labels: list[str], approved: tuple[str, ...]) -> tuple[int, ...]:
    indices = {label: index for index, label in enumerate(labels)}
    missing = [label for label in approved if label not in indices]
    if missing:
        raise BridgeError("instrument_labels contains a label absent from the local PANNs checkpoint taxonomy")
    return tuple(indices[label] for label in approved)


def _positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise BridgeError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise BridgeError(f"{label} must be a non-negative integer")
    return value


def _plain_sequence(value: Any) -> list[Any] | None:
    """Accept list-like provider output without importing its numeric runtime."""
    if isinstance(value, (list, tuple)):
        return list(value)
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        converted = tolist()
        return list(converted) if isinstance(converted, (list, tuple)) else None
    return None


if __name__ == "__main__":
    try:
        run(sys.argv[1:])
    except BridgeError as exc:
        fail(exc)
    except (RuntimeError, UnicodeError, json.JSONDecodeError):
        fail(BridgeError("local PANNs provider could not run"))
