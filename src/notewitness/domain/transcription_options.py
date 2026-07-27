"""Transcription option snapshots and deterministic batch queue contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any
import unicodedata

from notewitness.domain.timeline import MediaSpan
from notewitness.domain.transcription_shared import (
    _freeze_settings,
    _json_ready,
    _require_bool,
    _require_enum,
)


MAX_TRANSCRIPTION_SOURCES = 256
MAX_TRANSCRIPTION_QUEUE_JOBS = 256
MAX_EXACT_SPEAKERS = 10
_PAUSE_THRESHOLDS_MS = frozenset({1_000, 2_000, 3_000})


class LanguageMode(StrEnum):
    FIXED = "fixed"
    AUTO = "auto"
    MULTILINGUAL = "multilingual"


class DiarizationMode(StrEnum):
    OFF = "off"
    AUTO = "auto"
    EXACT = "exact"


class DisfluencyPolicy(StrEnum):
    INCLUDE = "include"
    SUPPRESS = "suppress"


class TranscriptExportFormat(StrEnum):
    HTML = "html"
    TEXT = "text"
    WEBVTT = "webvtt"


@dataclass(frozen=True, slots=True)
class TranscriptionJobSpec:
    """One effective option snapshot shared by GUI, CLI, and batch runners."""

    job_id: str
    spans: tuple[MediaSpan, ...]
    model_profile_id: str
    language_mode: LanguageMode
    requested_language: str | None
    diarization_mode: DiarizationMode
    exact_speaker_count: int | None
    detect_overlap: bool
    disfluency_policy: DisfluencyPolicy
    pause_threshold_ms: int | None
    visible_timestamps: bool
    timestamp_interval_ms: int
    output_format: TranscriptExportFormat
    model_vocabulary_artifact_id: str | None = None
    adapter_prompt_artifact_id: str | None = None
    project_lexicon_id: str | None = None
    adapter_settings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, str) or not isinstance(
            self.model_profile_id, str
        ) or not self.job_id or not self.model_profile_id:
            raise ValueError("Transcription jobs require job and model profile IDs.")
        if (
            not isinstance(self.spans, tuple)
            or any(not isinstance(span, MediaSpan) for span in self.spans)
            or not self.spans
            or len(self.spans) > MAX_TRANSCRIPTION_SOURCES
        ):
            raise ValueError(
                f"Transcription jobs require 1-{MAX_TRANSCRIPTION_SOURCES} spans."
            )
        if any(span.duration_us <= 0 for span in self.spans):
            raise ValueError("Transcription spans require positive duration.")
        if len(self.spans) != len(set(self.spans)):
            raise ValueError("Transcription spans must not contain duplicates.")
        if len({span.source_id for span in self.spans}) != 1:
            raise ValueError(
                "One transcription job may contain spans from only one source; "
                "use a queue for batch work."
            )
        _require_enum(self.language_mode, LanguageMode, "language_mode")
        _require_enum(self.diarization_mode, DiarizationMode, "diarization_mode")
        _require_enum(self.disfluency_policy, DisfluencyPolicy, "disfluency_policy")
        _require_enum(self.output_format, TranscriptExportFormat, "output_format")
        _require_bool(self.detect_overlap, "detect_overlap")
        _require_bool(self.visible_timestamps, "visible_timestamps")
        if self.requested_language is not None and (
            not isinstance(self.requested_language, str)
            or not self.requested_language.strip()
        ):
            raise ValueError("requested_language must be a non-empty string.")
        if self.language_mode is LanguageMode.FIXED and not self.requested_language:
            raise ValueError("Fixed language mode requires requested_language.")
        if self.language_mode is not LanguageMode.FIXED and self.requested_language:
            raise ValueError(
                "requested_language is only valid for fixed language mode."
            )
        if self.diarization_mode is DiarizationMode.EXACT:
            if (
                not isinstance(self.exact_speaker_count, int)
                or isinstance(self.exact_speaker_count, bool)
                or not 1 <= self.exact_speaker_count <= MAX_EXACT_SPEAKERS
            ):
                raise ValueError(
                    "Exact diarization requires a speaker count from 1 to 10."
                )
        elif self.exact_speaker_count is not None:
            raise ValueError("exact_speaker_count requires exact diarization mode.")
        if (
            self.pause_threshold_ms is not None
            and self.pause_threshold_ms not in _PAUSE_THRESHOLDS_MS
        ):
            raise ValueError("Pause threshold must be off, 1000, 2000, or 3000 ms.")
        if (
            not isinstance(self.timestamp_interval_ms, int)
            or isinstance(self.timestamp_interval_ms, bool)
            or self.timestamp_interval_ms <= 0
        ):
            raise ValueError("timestamp_interval_ms must be a positive integer.")
        for artifact_id in (
            self.model_vocabulary_artifact_id,
            self.adapter_prompt_artifact_id,
            self.project_lexicon_id,
        ):
            if artifact_id is not None and not artifact_id.strip():
                raise ValueError("Optional vocabulary and prompt IDs must not be empty.")
        if not isinstance(self.adapter_settings, Mapping):
            raise ValueError("adapter_settings must be a JSON object.")
        _validate_common_adapter_settings(self.adapter_settings)
        object.__setattr__(
            self,
            "adapter_settings",
            _freeze_settings(self.adapter_settings, "adapter_settings"),
        )

    def as_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True, slots=True)
class TranscriptionQueueItem:
    job: TranscriptionJobSpec
    output_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.job, TranscriptionJobSpec):
            raise ValueError("Queue items require a validated transcription job.")
        if not isinstance(self.output_name, str):
            raise ValueError("Queue output_name must be a string.")
        if (
            not self.output_name
            or self.output_name in {".", ".."}
            or "/" in self.output_name
            or "\\" in self.output_name
        ):
            raise ValueError(
                "Queue output_name must be one safe filename, not a path."
            )
        expected_suffix = {
            TranscriptExportFormat.HTML: ".html",
            TranscriptExportFormat.TEXT: ".txt",
            TranscriptExportFormat.WEBVTT: ".vtt",
        }[self.job.output_format]
        if not self.output_name.casefold().endswith(expected_suffix):
            raise ValueError(
                f"Queue output_name must end in {expected_suffix!r} for the job."
            )


@dataclass(frozen=True, slots=True)
class TranscriptionQueuePlan:
    """A bounded, deterministic, sequential queue like noScribe's batch flow."""

    queue_id: str
    items: tuple[TranscriptionQueueItem, ...]
    output_root: str = "exports/transcripts"

    def __post_init__(self) -> None:
        if not self.queue_id:
            raise ValueError("Transcription queues require an ID.")
        if not isinstance(self.output_root, str) or not self.output_root:
            raise ValueError("Transcription queues require an output root.")
        root = PurePosixPath(self.output_root)
        if (
            root.is_absolute()
            or "\\" in self.output_root
            or any(
                part in {"", ".", ".."} or ":" in part for part in root.parts
            )
        ):
            raise ValueError(
                "Transcription queue output_root must be a safe relative path."
            )
        if (
            not isinstance(self.items, tuple)
            or any(not isinstance(item, TranscriptionQueueItem) for item in self.items)
            or not self.items
            or len(self.items) > MAX_TRANSCRIPTION_QUEUE_JOBS
        ):
            raise ValueError(
                "Transcription queues require 1-"
                f"{MAX_TRANSCRIPTION_QUEUE_JOBS} jobs."
            )
        job_ids = tuple(item.job.job_id for item in self.items)
        if len(job_ids) != len(set(job_ids)):
            raise ValueError("Transcription queue job IDs must be unique.")
        output_names = tuple(
            unicodedata.normalize("NFC", item.output_name).casefold()
            for item in self.items
        )
        if len(output_names) != len(set(output_names)):
            raise ValueError("Transcription queue output names must be unique.")

    @property
    def jobs(self) -> tuple[TranscriptionJobSpec, ...]:
        return tuple(item.job for item in self.items)


def _validate_common_adapter_settings(settings: Mapping[str, Any]) -> None:
    beam_size = settings.get("beam_size")
    if beam_size is not None and (
        not isinstance(beam_size, int)
        or isinstance(beam_size, bool)
        or not 1 <= beam_size <= 100
    ):
        raise ValueError("beam_size must be an integer in [1, 100].")
    vad_threshold = settings.get("vad_threshold")
    if vad_threshold is not None and (
        isinstance(vad_threshold, bool)
        or not isinstance(vad_threshold, (int, float))
        or not 0.0 <= vad_threshold <= 1.0
    ):
        raise ValueError("vad_threshold must be in [0, 1].")
    for key in ("compute_type", "device", "backend"):
        value = settings.get(key)
        if value is not None and (
            not isinstance(value, str) or not value.strip()
        ):
            raise ValueError(f"{key} must be a non-empty string.")
