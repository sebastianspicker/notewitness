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
        _validate_job_identity_and_spans(self)
        _validate_job_options(self)
        _validate_job_language_and_diarization(self)
        _validate_job_output(self)
        _validate_optional_artifact_ids(self)
        _validate_adapter_settings(self.adapter_settings)
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
    _validate_beam_size(settings.get("beam_size"))
    _validate_vad_threshold(settings.get("vad_threshold"))
    _validate_adapter_setting_strings(settings)


def _validate_job_identity_and_spans(job: TranscriptionJobSpec) -> None:
    _validate_job_identity(job)
    _validate_job_span_collection(job.spans)
    _validate_job_span_values(job.spans)


def _validate_job_identity(job: TranscriptionJobSpec) -> None:
    identifiers = (job.job_id, job.model_profile_id)
    if any(not isinstance(value, str) or not value for value in identifiers):
        raise ValueError("Transcription jobs require job and model profile IDs.")


def _validate_job_span_collection(spans: object) -> None:
    if not isinstance(spans, tuple) or not spans or len(spans) > MAX_TRANSCRIPTION_SOURCES:
        raise ValueError(f"Transcription jobs require 1-{MAX_TRANSCRIPTION_SOURCES} spans.")
    if any(not isinstance(span, MediaSpan) for span in spans):
        raise ValueError("Transcription jobs require typed media spans.")


def _validate_job_span_values(spans: tuple[MediaSpan, ...]) -> None:
    if any(span.duration_us <= 0 for span in spans):
        raise ValueError("Transcription spans require positive duration.")
    if len(spans) != len(set(spans)):
        raise ValueError("Transcription spans must not contain duplicates.")
    if len({span.source_id for span in spans}) != 1:
        raise ValueError("One transcription job may contain spans from only one source; use a queue for batch work.")


def _validate_job_options(job: TranscriptionJobSpec) -> None:
    _require_enum(job.language_mode, LanguageMode, "language_mode")
    _require_enum(job.diarization_mode, DiarizationMode, "diarization_mode")
    _require_enum(job.disfluency_policy, DisfluencyPolicy, "disfluency_policy")
    _require_enum(job.output_format, TranscriptExportFormat, "output_format")
    _require_bool(job.detect_overlap, "detect_overlap")
    _require_bool(job.visible_timestamps, "visible_timestamps")


def _validate_job_language_and_diarization(job: TranscriptionJobSpec) -> None:
    _validate_requested_language(job.requested_language)
    _validate_language_mode(job)
    _validate_diarization_mode(job)


def _validate_requested_language(value: object) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError("requested_language must be a non-empty string.")


def _validate_language_mode(job: TranscriptionJobSpec) -> None:
    if job.language_mode is LanguageMode.FIXED and not job.requested_language:
        raise ValueError("Fixed language mode requires requested_language.")
    if job.language_mode is not LanguageMode.FIXED and job.requested_language:
        raise ValueError("requested_language is only valid for fixed language mode.")


def _validate_diarization_mode(job: TranscriptionJobSpec) -> None:
    if job.diarization_mode is DiarizationMode.EXACT:
        _validate_exact_speaker_count(job.exact_speaker_count)
    elif job.exact_speaker_count is not None:
        raise ValueError("exact_speaker_count requires exact diarization mode.")


def _validate_exact_speaker_count(value: object) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= MAX_EXACT_SPEAKERS:
        raise ValueError("Exact diarization requires a speaker count from 1 to 10.")


def _validate_job_output(job: TranscriptionJobSpec) -> None:
    if job.pause_threshold_ms is not None and job.pause_threshold_ms not in _PAUSE_THRESHOLDS_MS:
        raise ValueError("Pause threshold must be off, 1000, 2000, or 3000 ms.")
    if not isinstance(job.timestamp_interval_ms, int) or isinstance(job.timestamp_interval_ms, bool) or job.timestamp_interval_ms <= 0:
        raise ValueError("timestamp_interval_ms must be a positive integer.")


def _validate_optional_artifact_ids(job: TranscriptionJobSpec) -> None:
    for artifact_id in (job.model_vocabulary_artifact_id, job.adapter_prompt_artifact_id, job.project_lexicon_id):
        if artifact_id is not None and not artifact_id.strip():
            raise ValueError("Optional vocabulary and prompt IDs must not be empty.")


def _validate_adapter_settings(settings: object) -> None:
    if not isinstance(settings, Mapping):
        raise ValueError("adapter_settings must be a JSON object.")


def _validate_beam_size(beam_size: object) -> None:
    if beam_size is None:
        return
    if not _is_non_boolean_int(beam_size):
        raise ValueError("beam_size must be an integer in [1, 100].")
    if beam_size < 1:
        raise ValueError("beam_size must be an integer in [1, 100].")
    if beam_size > 100:
        raise ValueError("beam_size must be an integer in [1, 100].")


def _validate_vad_threshold(vad_threshold: object) -> None:
    if vad_threshold is None:
        return
    if not _is_non_boolean_number(vad_threshold):
        raise ValueError("vad_threshold must be in [0, 1].")
    if not _is_unit_interval(vad_threshold):
        raise ValueError("vad_threshold must be in [0, 1].")


def _is_non_boolean_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_non_boolean_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_unit_interval(value: int | float) -> bool:
    return 0.0 <= value <= 1.0


def _validate_adapter_setting_strings(settings: Mapping[str, Any]) -> None:
    for key in ("compute_type", "device", "backend"):
        value = settings.get(key)
        if value is not None and (
            not isinstance(value, str) or not value.strip()
        ):
            raise ValueError(f"{key} must be a non-empty string.")
