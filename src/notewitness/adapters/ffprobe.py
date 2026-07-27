"""Strict-local FFprobe adapter for descriptive media metadata."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import stat
from typing import Any

from notewitness.local_tools import BoundedLocalToolRunner, LocalTool
from notewitness.media_ingest import MediaMetadata


MAX_MEDIA_DURATION_US = 48 * 60 * 60 * 1_000_000


class MediaProbeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProbedMedia:
    duration_us: int
    byte_count: int
    format_names: tuple[str, ...]
    audio_stream_count: int
    video_stream_count: int
    audio_codecs: tuple[str, ...]
    video_codecs: tuple[str, ...]
    sample_rates_hz: tuple[int, ...]
    channel_counts: tuple[int, ...]

    @property
    def kind(self) -> str:
        return "video" if self.video_stream_count else "audio"

    @property
    def stream_count(self) -> int:
        return self.audio_stream_count + self.video_stream_count

    def as_metadata(self) -> MediaMetadata:
        return MediaMetadata(
            kind=self.kind,
            duration_us=self.duration_us,
            stream_count=self.stream_count,
        )


class FFprobeMediaProbe:
    """Probe only an explicit regular local file under a private directory."""

    def __init__(self, tool: LocalTool) -> None:
        if tool.name != "ffprobe":
            raise ValueError("FFprobeMediaProbe requires the ffprobe tool.")
        self._runner = BoundedLocalToolRunner(tool)

    def probe(self, source_path: Path) -> MediaMetadata:
        return self.inspect(source_path).as_metadata()

    def inspect(self, source_path: str | Path) -> ProbedMedia:
        source = _validated_source(Path(source_path))
        source_before = source.stat()
        result = self._runner.run(
            (
                "-v",
                "error",
                "-show_entries",
                "format=duration,format_name:"
                "stream=codec_type,codec_name,duration,sample_rate,channels",
                "-of",
                "json",
                str(source),
            ),
            working_directory=source.parent,
            timeout_seconds=60,
            deny_network=True,
        )
        if _stat_identity(source.stat()) != _stat_identity(source_before):
            raise MediaProbeError("Media source changed while ffprobe was running.")
        if result.stdout_truncated:
            raise MediaProbeError("FFprobe output exceeded its bounded result size.")
        payload = _load_object(result.stdout)
        streams = payload.get("streams")
        media_format = payload.get("format")
        if not isinstance(streams, list) or not isinstance(media_format, dict):
            raise MediaProbeError("FFprobe did not return streams and format metadata.")

        audio = tuple(
            item
            for item in streams
            if isinstance(item, dict) and item.get("codec_type") == "audio"
        )
        video = tuple(
            item
            for item in streams
            if isinstance(item, dict) and item.get("codec_type") == "video"
        )
        if not audio:
            raise MediaProbeError("Imported teaching media requires an audio stream.")
        duration = _duration_seconds(media_format, (*audio, *video))
        duration_us = round(duration * 1_000_000)
        if not 0 < duration_us <= MAX_MEDIA_DURATION_US:
            raise MediaProbeError("Media duration is outside the supported 48-hour bound.")
        format_names = tuple(
            sorted(
                {
                    value.strip()
                    for value in str(media_format.get("format_name", "")).split(",")
                    if value.strip()
                }
            )
        )
        return ProbedMedia(
            duration_us=duration_us,
            byte_count=source.stat().st_size,
            format_names=format_names,
            audio_stream_count=len(audio),
            video_stream_count=len(video),
            audio_codecs=_strings(audio, "codec_name"),
            video_codecs=_strings(video, "codec_name"),
            sample_rates_hz=_positive_integers(audio, "sample_rate"),
            channel_counts=_positive_integers(audio, "channels"),
        )


def _validated_source(path: Path) -> Path:
    if path.is_symlink():
        raise MediaProbeError("Media probe source must not be a symlink.")
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise MediaProbeError("Media probe source is unavailable.") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
        raise MediaProbeError("Media probe source must be a non-empty regular file.")
    return resolved


def _load_object(raw: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise MediaProbeError(f"FFprobe output duplicated key {key!r}.")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=reject_duplicates)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise MediaProbeError("FFprobe returned invalid JSON.") from exc
    if not isinstance(value, dict):
        raise MediaProbeError("FFprobe JSON must be an object.")
    return value


def _duration_seconds(
    media_format: dict[str, Any],
    streams: tuple[dict[str, Any], ...],
) -> float:
    candidates = (media_format.get("duration"), *(item.get("duration") for item in streams))
    parsed: list[float] = []
    for candidate in candidates:
        try:
            value = float(candidate)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            parsed.append(value)
    if not parsed:
        raise MediaProbeError("FFprobe did not report a positive finite duration.")
    return max(parsed)


def _strings(records: tuple[dict[str, Any], ...], field: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                value
                for record in records
                if isinstance((value := record.get(field)), str) and value
            }
        )
    )


def _positive_integers(
    records: tuple[dict[str, Any], ...], field: str
) -> tuple[int, ...]:
    values: set[int] = set()
    for record in records:
        try:
            value = int(record.get(field))
        except (TypeError, ValueError):
            continue
        if value > 0:
            values.add(value)
    return tuple(sorted(values))


def _stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )
