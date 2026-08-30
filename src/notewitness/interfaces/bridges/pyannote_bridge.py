#!/usr/bin/env python3
"""Map a local pyannote pipeline artifact to anonymous diarization hypotheses."""

from __future__ import annotations

import sys

try:
    from ._protocol import BridgeError, bounded_span, fail, main_request, media_path, model_path, response, seconds_to_us
except ImportError:  # direct execution before package installation
    from _protocol import BridgeError, bounded_span, fail, main_request, media_path, model_path, response, seconds_to_us


def run(argv: list[str]) -> None:
    request = main_request(argv, {"anonymous_diarization"})
    mode, count = _diarization_settings(request["parameters"])
    if mode == "off":
        response([], ["diarization disabled by request"])
        return
    raw = _raw_diarization(request, mode, count)
    response(_hypotheses(request, raw))


def _diarization_settings(parameters: object) -> tuple[str, int | None]:
    expected = {"detect_overlap", "diarization_mode", "exact_speaker_count"}
    if not isinstance(parameters, dict) or set(parameters) != expected:
        raise BridgeError("pyannote bridge requires the diarization stage parameters")
    mode = parameters["diarization_mode"]
    count = parameters["exact_speaker_count"]
    if not isinstance(parameters["detect_overlap"], bool) or mode not in {"off", "auto", "exact"}:
        raise BridgeError("pyannote diarization parameters are invalid")
    _validate_speaker_count(mode, count)
    return mode, count


def _validate_speaker_count(mode: object, count: object) -> None:
    if mode == "off":
        if count is not None:
            raise BridgeError("disabled diarization must not include a speaker count")
    elif mode == "exact":
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 10:
            raise BridgeError("exact diarization requires exact_speaker_count in [1, 10]")
    elif count is not None:
        raise BridgeError("automatic diarization must not include a speaker count")


def _raw_diarization(request: dict[str, object], mode: str, count: int | None) -> list[tuple[int, int, str]]:
    try:
        from pyannote.audio import Pipeline
    except ImportError as exc:
        raise BridgeError("pyannote.audio is not installed in this local runtime") from exc
    try:
        pipeline = Pipeline.from_pretrained(model_path(request))
        result = (pipeline(media_path(request), num_speakers=count)
                  if mode == "exact" else pipeline(media_path(request)))
    except Exception as exc:
        raise BridgeError("local pyannote pipeline could not run") from exc
    return _annotation_turns(result)


def _annotation_turns(result: object) -> list[tuple[int, int, str]]:
    raw: list[tuple[int, int, str]] = []
    try:
        # Community-1 wraps both overlapping and exclusive diarization.  Only
        # the overlapping annotation is admissible as speaker evidence here.
        annotation = getattr(result, "speaker_diarization", None)
        if annotation is None and isinstance(result, dict):
            annotation = result.get("speaker_diarization")
        if annotation is None:
            annotation = result  # Older Pipeline output is an Annotation.
        iterator = annotation.itertracks(yield_label=True)
        for turn, _track, label in iterator:
            raw.append((seconds_to_us(turn.start, "turn start"), seconds_to_us(turn.end, "turn end"), str(label)))
    except (AttributeError, TypeError) as exc:
        raise BridgeError("pyannote returned an unsupported diarization annotation") from exc
    return raw


def _hypotheses(request: dict[str, object], raw: list[tuple[int, int, str]]) -> list[dict[str, object]]:
    labels = {label for _start, _end, label in raw}
    ordered = sorted(labels, key=lambda label: (min(start for start, _end, item in raw if item == label), label))
    mapping = {label: f"speaker-{index + 1:02d}" for index, label in enumerate(ordered)}
    return [
        {"hypothesis_id": f"pyannote:segment:{index:06d}",
         "span": bounded_span(request, start, end), "state": "ready", "confidence": None,
         "anonymous_cluster_id": mapping[label]}
        for index, (start, end, label) in enumerate(raw)
    ]


if __name__ == "__main__":
    try:
        run(sys.argv[1:])
    except BridgeError as exc:
        fail(exc)
