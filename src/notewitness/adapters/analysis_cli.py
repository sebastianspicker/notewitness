"""Strict adapter for explicit, offline JSON-speaking analysis executables."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping

from notewitness.domain.analysis import (
    ActivityHypothesis,
    AnalysisBatch,
    AnalysisRequest,
    AnalysisResult,
    AnalysisStage,
    AnalysisState,
    AlignmentOutcome,
    InstrumentHypothesis,
    NoteHypothesis,
    PitchPointHypothesis,
    ScoreAlignmentHypothesis,
    SpeakerSegmentHypothesis,
)
from notewitness.domain.lesson import ActivityKind
from notewitness.domain.timeline import MediaSpan
from notewitness.local_tools import (
    BoundedLocalToolRunner,
    LocalTool,
    LocalToolCancelled,
    LocalToolError,
    LocalToolFailure,
)


MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_ITEMS = 50_000
MAX_STRING_CHARS = 4_096
MAX_ARTIFACT_TREE_ENTRIES = 200_000
MAX_ARTIFACT_TREE_BYTES = 128 * 1024 * 1024 * 1024
_SUPPORTED_STAGES = frozenset(
    {
        AnalysisStage.ACTIVITY_SEGMENTATION,
        AnalysisStage.ANONYMOUS_DIARIZATION,
        AnalysisStage.NOTE_TRANSCRIPTION,
        AnalysisStage.CONTINUOUS_PITCH,
        AnalysisStage.INSTRUMENT_DETECTION,
        AnalysisStage.INSTRUMENT_DIARIZATION,
        AnalysisStage.SCORE_ALIGNMENT,
    }
)


class AnalysisCLIError(RuntimeError):
    """The local executable violated the bounded analysis JSON protocol."""


class AnalysisCLIExecutionError(AnalysisCLIError):
    """A failed execution carrying bounded stdout for private recovery only."""

    def __init__(
        self,
        message: str,
        *,
        request_sha256: str,
        raw_output: bytes,
    ) -> None:
        super().__init__(message)
        self.request_sha256 = request_sha256
        self.raw_output = raw_output
        self.raw_output_sha256 = hashlib.sha256(raw_output).hexdigest()


class AnalysisCLICancelled(AnalysisCLIError):
    """The caller cancelled a running analysis executable."""


@dataclass(frozen=True, slots=True)
class LocalAnalysisSource:
    """Runtime-owned input identity, never supplied through request parameters."""

    source_id: str
    path: Path
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id:
            raise ValueError("Analysis inputs require a source ID.")
        path = _private_analysis_input(Path(self.path))
        object.__setattr__(self, "path", path)
        if (
            not isinstance(self.sha256, str)
            or len(self.sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.sha256)
        ):
            raise ValueError("Analysis input sha256 must be a lowercase digest.")
        if (
            not isinstance(self.size_bytes, int)
            or isinstance(self.size_bytes, bool)
            or self.size_bytes <= 0
        ):
            raise ValueError("Analysis input size_bytes must be positive.")


@dataclass(frozen=True, slots=True)
class LocalAnalysisCLISettings:
    working_directory: Path
    media: LocalAnalysisSource
    model: LocalAnalysisSource
    model_license: str
    adapter_license: str
    timeout_seconds: int = 3_600
    score: LocalAnalysisSource | None = None
    score_license: str | None = None

    def __post_init__(self) -> None:
        directory = _private_directory(Path(self.working_directory))
        object.__setattr__(self, "working_directory", directory)
        if not isinstance(self.media, LocalAnalysisSource):
            raise ValueError("Analysis CLI settings require a media input.")
        if not isinstance(self.model, LocalAnalysisSource):
            raise ValueError("Analysis CLI settings require a model artifact.")
        if self.score is not None and not isinstance(
            self.score, LocalAnalysisSource
        ):
            raise ValueError("score must be a LocalAnalysisSource or None.")
        if (self.score is None) != (self.score_license is None):
            raise ValueError("score and score_license must be configured together.")
        if (
            not isinstance(self.timeout_seconds, int)
            or isinstance(self.timeout_seconds, bool)
            or not 1 <= self.timeout_seconds <= 43_200
        ):
            raise ValueError("timeout_seconds must be an integer in [1, 43200].")
        for value, label in (
            (self.model_license, "model_license"),
            (self.adapter_license, "adapter_license"),
        ):
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > MAX_STRING_CHARS
            ):
                raise ValueError(f"{label} must be a bounded non-empty string.")
        if self.score_license is not None and (
            not isinstance(self.score_license, str)
            or not self.score_license.strip()
            or len(self.score_license) > MAX_STRING_CHARS
        ):
            raise ValueError("score_license must be a bounded non-empty string.")


@dataclass(frozen=True, slots=True)
class LocalAnalysisCLIExecution:
    batch: AnalysisBatch
    request_sha256: str
    raw_output: bytes
    raw_output_sha256: str
    duration_ms: int
    network_isolated: bool


class LocalAnalysisCLIAdapter:
    """One fixed executable and stage; output remains machine suggestion evidence."""

    def __init__(
        self,
        tool: LocalTool,
        *,
        stage: AnalysisStage,
        version: str,
        generator_id: str,
        settings: LocalAnalysisCLISettings,
    ) -> None:
        if not isinstance(tool, LocalTool):
            raise ValueError("LocalAnalysisCLIAdapter requires one explicit LocalTool.")
        if stage not in _SUPPORTED_STAGES:
            raise ValueError("This adapter does not support the requested analysis stage.")
        if not isinstance(version, str) or not version or len(version) > MAX_STRING_CHARS:
            raise ValueError("Analysis CLI version must be a bounded non-empty string.")
        if (
            not isinstance(generator_id, str)
            or not generator_id
            or len(generator_id) > MAX_STRING_CHARS
        ):
            raise ValueError("Analysis CLI generator_id must be a bounded non-empty string.")
        self.tool = tool
        self.stage = stage
        self.version = version
        self.generator_id = generator_id
        self.settings = settings
        self.name = tool.name
        self._runner = BoundedLocalToolRunner(tool)

    def analyze(
        self,
        request: AnalysisRequest,
        *,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> AnalysisBatch:
        return self.execute(
            request,
            cancellation_requested=cancellation_requested,
        ).batch

    def require_executable_identity(self) -> None:
        """Require the exact executable approved when the adapter was built."""

        try:
            self.tool.require_unchanged()
        except LocalToolError as exc:
            raise AnalysisCLIError(
                "Analysis executable changed after adapter construction."
            ) from exc

    def execute(
        self,
        request: AnalysisRequest,
        *,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> LocalAnalysisCLIExecution:
        """Run once and retain exact UTF-8 JSON bytes for provenance storage."""

        if not isinstance(request, AnalysisRequest):
            raise TypeError("Analysis CLI requests must be AnalysisRequest instances.")
        if request.source_id != self.settings.media.source_id:
            raise AnalysisCLIError(
                "Analysis request source does not match the configured media input."
            )
        _require_source_identity(self.settings.media)
        _require_source_identity(self.settings.model)
        if self.settings.score is not None:
            _require_source_identity(self.settings.score)
        payload = _request_payload(request, self)
        request_bytes = _json_bytes(payload, "request")
        with TemporaryDirectory(
            prefix="analysis-cli-", dir=self.settings.working_directory
        ) as raw:
            workdir = Path(raw)
            workdir.chmod(0o700)
            request_file = workdir / "request.json"
            request_file.write_bytes(request_bytes)
            request_file.chmod(0o600)
            self.require_executable_identity()
            failure: LocalToolFailure | None = None
            try:
                result = self._runner.run(
                    ("--request", "request.json"),
                    working_directory=workdir,
                    timeout_seconds=self.settings.timeout_seconds,
                    deny_network=True,
                    cancellation_requested=cancellation_requested,
                )
            except LocalToolCancelled as exc:
                raise AnalysisCLICancelled(
                    "Analysis CLI process was cancelled."
                ) from exc
            except LocalToolFailure as exc:
                failure = exc
            finally:
                self.require_executable_identity()
            if failure is not None:
                raw_output = failure.stdout.encode("utf-8")
                raise AnalysisCLIExecutionError(
                    "Analysis CLI process failed.",
                    request_sha256=hashlib.sha256(request_bytes).hexdigest(),
                    raw_output=raw_output,
                ) from failure
        _require_source_identity(self.settings.media)
        _require_source_identity(self.settings.model)
        if self.settings.score is not None:
            _require_source_identity(self.settings.score)
        raw_output = result.stdout.encode("utf-8")
        try:
            batch = _parse_batch(result.stdout, request, self)
        except AnalysisCLIError as exc:
            raise AnalysisCLIExecutionError(
                "Analysis CLI output could not be normalized.",
                request_sha256=hashlib.sha256(request_bytes).hexdigest(),
                raw_output=raw_output,
            ) from exc
        return LocalAnalysisCLIExecution(
            batch=batch,
            request_sha256=hashlib.sha256(request_bytes).hexdigest(),
            raw_output=raw_output,
            raw_output_sha256=hashlib.sha256(raw_output).hexdigest(),
            duration_ms=result.duration_ms,
            network_isolated=result.network_isolated,
        )

    def replay(self, request: AnalysisRequest, raw_output: bytes) -> AnalysisBatch:
        """Validate retained exact output without invoking the local executable."""
        if not isinstance(request, AnalysisRequest):
            raise TypeError("Analysis CLI requests must be AnalysisRequest instances.")
        if request.source_id != self.settings.media.source_id:
            raise AnalysisCLIError(
                "Analysis request source does not match the configured media input."
            )
        if not isinstance(raw_output, bytes) or len(raw_output) > MAX_JSON_BYTES:
            raise AnalysisCLIError("Retained analysis output exceeds the bounded contract.")
        _require_source_identity(self.settings.media)
        _require_source_identity(self.settings.model)
        if self.settings.score is not None:
            _require_source_identity(self.settings.score)
        try:
            raw = raw_output.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AnalysisCLIError("Retained analysis output must be UTF-8 JSON.") from exc
        return _parse_batch(raw, request, self)


def _request_payload(
    request: AnalysisRequest,
    adapter: LocalAnalysisCLIAdapter,
) -> dict[str, Any]:
    parameters = _json_value(request.parameters, "parameters")
    _reject_path_like_values(parameters, "parameters")
    return {
        "schema_version": 1,
        "stage": adapter.stage.value,
        "version": adapter.version,
        "generator_id": adapter.generator_id,
        "model": _source_payload(adapter.settings.model),
        "job_id": request.job_id,
        "source_id": request.source_id,
        "media": _source_payload(adapter.settings.media),
        "score": (
            _source_payload(adapter.settings.score)
            if adapter.settings.score is not None
            else None
        ),
        "spans": [_span_payload(span, include_source=False) for span in request.spans],
        "parameters": parameters,
        "continuation_token": request.continuation_token,
    }


def _parse_batch(
    raw: str,
    request: AnalysisRequest,
    adapter: LocalAnalysisCLIAdapter,
) -> AnalysisBatch:
    if len(raw.encode("utf-8")) > MAX_JSON_BYTES:
        raise AnalysisCLIError("Analysis CLI JSON output exceeds the bounded contract.")
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_object)
        payload = _json_value(payload, "output")
    except (json.JSONDecodeError, AnalysisCLIError) as exc:
        raise AnalysisCLIError("Analysis CLI did not emit valid JSON.") from exc
    _expect_keys(
        payload,
        {"state", "hypotheses", "diagnostics", "continuation_token"},
        "output",
    )
    state = _enum(AnalysisState, payload["state"], "output.state")
    hypotheses_raw = _list(payload["hypotheses"], "output.hypotheses")
    if len(hypotheses_raw) > MAX_JSON_ITEMS:
        raise AnalysisCLIError("Analysis CLI returned too many hypotheses.")
    diagnostics = _string_tuple(payload["diagnostics"], "output.diagnostics")
    continuation = _nullable_string(
        payload["continuation_token"], "output.continuation_token"
    )
    hypotheses = tuple(
        _hypothesis(item, request, adapter, index)
        for index, item in enumerate(hypotheses_raw)
    )
    try:
        return AnalysisBatch(
            AnalysisResult(
                stage=adapter.stage,
                state=state,
                hypothesis_ids=tuple(item.hypothesis_id for item in hypotheses),
                diagnostics=diagnostics,
                continuation_token=continuation,
            ),
            hypotheses,
        )
    except ValueError as exc:
        raise AnalysisCLIError("Analysis CLI output violates the analysis contract.") from exc


def _hypothesis(
    raw: Any,
    request: AnalysisRequest,
    adapter: LocalAnalysisCLIAdapter,
    index: int,
) -> Any:
    label = f"output.hypotheses[{index}]"
    common = {"hypothesis_id", "span", "state", "confidence"}
    stage_fields: dict[AnalysisStage, set[str]] = {
        AnalysisStage.ACTIVITY_SEGMENTATION: {"kind"},
        AnalysisStage.ANONYMOUS_DIARIZATION: {"anonymous_cluster_id"},
        AnalysisStage.NOTE_TRANSCRIPTION: {"midi_pitch", "frequency_hz"},
        AnalysisStage.CONTINUOUS_PITCH: {"frequency_hz"},
        AnalysisStage.INSTRUMENT_DETECTION: {"instrument_label"},
        AnalysisStage.INSTRUMENT_DIARIZATION: {
            "instrument_label",
            "anonymous_instrument_track_id",
        },
        AnalysisStage.SCORE_ALIGNMENT: {
            "outcome",
            "score_id",
            "score_position",
            "source_hypothesis_ids",
        },
    }
    optional_fields: dict[AnalysisStage, set[str]] = {
        AnalysisStage.NOTE_TRANSCRIPTION: {
            "amplitude",
            "pitch_bend_unit",
            "pitch_bend_values",
            "source_track_id",
            "velocity",
        },
        AnalysisStage.INSTRUMENT_DETECTION: {
            "anonymous_instrument_track_id",
        },
    }
    _expect_keys_with_optional(
        raw,
        common | stage_fields[adapter.stage],
        optional_fields.get(adapter.stage, set()),
        label,
    )
    hypothesis_id = _string(raw["hypothesis_id"], f"{label}.hypothesis_id")
    state = _enum(AnalysisState, raw["state"], f"{label}.state")
    span = _span(raw["span"], request, f"{label}.span")
    confidence = _nullable_number(raw["confidence"], f"{label}.confidence")
    try:
        if adapter.stage is AnalysisStage.ACTIVITY_SEGMENTATION:
            kind_raw = raw["kind"]
            kind = None if kind_raw is None else _enum(ActivityKind, kind_raw, f"{label}.kind")
            return ActivityHypothesis(
                hypothesis_id,
                span,
                state,
                kind,
                confidence,
                adapter.generator_id,
            )
        if adapter.stage is AnalysisStage.ANONYMOUS_DIARIZATION:
            return SpeakerSegmentHypothesis(
                hypothesis_id,
                span,
                state,
                _nullable_string(
                    raw["anonymous_cluster_id"],
                    f"{label}.anonymous_cluster_id",
                ),
                None,
                confidence,
                adapter.generator_id,
            )
        if adapter.stage is AnalysisStage.NOTE_TRANSCRIPTION:
            return NoteHypothesis(
                hypothesis_id,
                span,
                state,
                _nullable_number(raw["midi_pitch"], f"{label}.midi_pitch"),
                _nullable_number(
                    raw["frequency_hz"], f"{label}.frequency_hz"
                ),
                confidence,
                adapter.generator_id,
                _nullable_string(
                    raw.get("source_track_id"), f"{label}.source_track_id"
                ),
                _nullable_number(raw.get("amplitude"), f"{label}.amplitude"),
                _nullable_integer_in_range(
                    raw.get("velocity"), 0, 127, f"{label}.velocity"
                ),
                _number_tuple(
                    raw.get("pitch_bend_values", []),
                    f"{label}.pitch_bend_values",
                ),
                _nullable_string(
                    raw.get("pitch_bend_unit"), f"{label}.pitch_bend_unit"
                ),
            )
        if adapter.stage is AnalysisStage.CONTINUOUS_PITCH:
            return PitchPointHypothesis(
                hypothesis_id,
                span,
                state,
                _nullable_number(
                    raw["frequency_hz"], f"{label}.frequency_hz"
                ),
                confidence,
                adapter.generator_id,
            )
        if adapter.stage in {
            AnalysisStage.INSTRUMENT_DETECTION,
            AnalysisStage.INSTRUMENT_DIARIZATION,
        }:
            return InstrumentHypothesis(
                hypothesis_id,
                span,
                state,
                _nullable_string(
                    raw["instrument_label"], f"{label}.instrument_label"
                ),
                None,
                confidence,
                adapter.generator_id,
                _nullable_string(
                    raw.get("anonymous_instrument_track_id"),
                    f"{label}.anonymous_instrument_track_id",
                ),
            )
        outcome = _enum(AlignmentOutcome, raw["outcome"], f"{label}.outcome")
        score_position = raw["score_position"]
        if score_position is not None:
            score_position = _mapping(
                _json_value(score_position, f"{label}.score_position"),
                f"{label}.score_position",
            )
        return ScoreAlignmentHypothesis(
            hypothesis_id,
            span,
            state,
            outcome,
            _nullable_string(raw["score_id"], f"{label}.score_id"),
            score_position,
            _string_tuple(
                raw["source_hypothesis_ids"],
                f"{label}.source_hypothesis_ids",
            ),
            confidence,
            adapter.generator_id,
        )
    except ValueError as exc:
        raise AnalysisCLIError(f"{label} violates the typed hypothesis contract.") from exc


def _span(raw: Any, request: AnalysisRequest, label: str) -> MediaSpan:
    _expect_keys(raw, {"stream_id", "start_us", "duration_us"}, label)
    stream = _string(raw["stream_id"], f"{label}.stream_id")
    start = _integer(raw["start_us"], f"{label}.start_us")
    duration = _integer(raw["duration_us"], f"{label}.duration_us")
    try:
        span = MediaSpan(request.source_id, stream, start, duration)
    except ValueError as exc:
        raise AnalysisCLIError(f"{label} is invalid.") from exc
    if not any(
        span.stream_id == allowed.stream_id
        and allowed.start_us <= span.start_us
        and span.end_us <= allowed.end_us
        for allowed in request.spans
    ):
        raise AnalysisCLIError(f"{label} lies outside the requested source span.")
    return span


def _span_payload(span: MediaSpan, *, include_source: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "stream_id": span.stream_id,
        "start_us": span.start_us,
        "duration_us": span.duration_us,
    }
    if include_source:
        payload["source_id"] = span.source_id
    return payload


def _expect_keys(value: Any, expected: set[str], label: str) -> None:
    mapping = _mapping(value, label)
    if set(mapping) != expected:
        raise AnalysisCLIError(f"{label} has unknown or missing keys.")


def _expect_keys_with_optional(
    value: Any,
    required: set[str],
    optional: set[str],
    label: str,
) -> None:
    mapping = _mapping(value, label)
    keys = set(mapping)
    if required - keys or keys - required - optional:
        raise AnalysisCLIError(f"{label} has unknown or missing keys.")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise AnalysisCLIError(f"{label} must be an object.")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise AnalysisCLIError(f"{label} must be an array.")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_STRING_CHARS:
        raise AnalysisCLIError(f"{label} must be a bounded non-empty string.")
    return value


def _nullable_string(value: Any, label: str) -> str | None:
    return None if value is None else _string(value, label)


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    result = tuple(_string(item, label) for item in _list(value, label))
    if len(result) > MAX_JSON_ITEMS or len(result) != len(set(result)):
        raise AnalysisCLIError(f"{label} must contain bounded unique strings.")
    return result


def _integer(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise AnalysisCLIError(f"{label} must be a non-negative integer.")
    return value


def _nullable_number(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise AnalysisCLIError(f"{label} must be a finite number or null.")
    return float(value)


def _nullable_integer_in_range(
    value: Any,
    minimum: int,
    maximum: int,
    label: str,
) -> int | None:
    if value is None:
        return None
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise AnalysisCLIError(
            f"{label} must be an integer in [{minimum}, {maximum}] or null."
        )
    return value


def _number_tuple(value: Any, label: str) -> tuple[float, ...]:
    items = _list(value, label)
    if len(items) > MAX_JSON_ITEMS:
        raise AnalysisCLIError(f"{label} contains too many values.")
    result: list[float] = []
    for item in items:
        parsed = _nullable_number(item, label)
        if parsed is None:
            raise AnalysisCLIError(f"{label} must contain only finite numbers.")
        result.append(parsed)
    return tuple(result)


def _enum(enum_type: Any, value: Any, label: str) -> Any:
    try:
        return enum_type(_string(value, label))
    except ValueError as exc:
        raise AnalysisCLIError(f"{label} has an unsupported value.") from exc


def _json_bytes(value: Any, label: str) -> bytes:
    try:
        encoded = json.dumps(
            value, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AnalysisCLIError(f"{label} is not JSON-safe.") from exc
    if len(encoded) > MAX_JSON_BYTES:
        raise AnalysisCLIError(f"{label} exceeds the bounded JSON contract.")
    return encoded


def _json_value(value: Any, label: str, depth: int = 0) -> Any:
    if depth > MAX_JSON_DEPTH:
        raise AnalysisCLIError(f"{label} exceeds the JSON nesting limit.")
    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str) and len(value) > MAX_STRING_CHARS:
            raise AnalysisCLIError(f"{label} contains an oversized string.")
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AnalysisCLIError(f"{label} contains a non-finite number.")
        return value
    if isinstance(value, Mapping):
        if len(value) > MAX_JSON_ITEMS:
            raise AnalysisCLIError(f"{label} contains too many items.")
        return {
            _string(key, f"{label} key"): _json_value(item, label, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_JSON_ITEMS:
            raise AnalysisCLIError(f"{label} contains too many items.")
        return [_json_value(item, label, depth + 1) for item in value]
    raise AnalysisCLIError(f"{label} is not JSON-safe.")


def _reject_path_like_values(value: Any, label: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if "path" in key.lower() or "media" in key.lower():
                raise AnalysisCLIError(f"{label} must not contain media paths.")
            _reject_path_like_values(item, label)
    elif isinstance(value, list):
        for item in value:
            _reject_path_like_values(item, label)
    elif isinstance(value, str) and (value.startswith("/") or value.startswith("~")):
        raise AnalysisCLIError(f"{label} must not contain filesystem paths.")


def _private_directory(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        mode = stat.S_IMODE(resolved.stat().st_mode)
    except OSError as exc:
        raise ValueError("working_directory must exist.") from exc
    if not resolved.is_dir() or mode & 0o077:
        raise ValueError("working_directory must deny group and other access.")
    return resolved


def _private_analysis_input(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("Analysis input paths must be absolute non-symlinks.")
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise ValueError("Analysis input path is unavailable.") from exc
    if (
        not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode))
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise ValueError(
            "Analysis inputs must be owner-private regular files or directories."
        )
    return resolved


def analysis_artifact_identity(path: str | Path) -> tuple[str, int]:
    """Hash one owner-private file or an explicit symlink-free model tree."""

    resolved = _private_analysis_input(Path(path))
    if resolved.is_file():
        return _regular_file_identity(resolved)
    digest = hashlib.sha256(b"notewitness-model-tree-v1\0")
    total_size = 0
    entry_count = 0
    pending = [resolved]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise ValueError("Analysis artifact directory could not be read.") from exc
        directories: list[Path] = []
        for entry in entries:
            entry_count += 1
            if entry_count > MAX_ARTIFACT_TREE_ENTRIES:
                raise ValueError("Analysis artifact directory has too many entries.")
            try:
                metadata = entry.lstat()
            except OSError as exc:
                raise ValueError("Analysis artifact entry became unavailable.") from exc
            if (
                stat.S_ISLNK(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise ValueError(
                    "Analysis artifact trees must be owner-private and symlink-free."
                )
            relative = entry.relative_to(resolved).as_posix().encode(
                "utf-8", errors="surrogateescape"
            )
            if stat.S_ISDIR(metadata.st_mode):
                digest.update(b"D\0" + relative + b"\0")
                directories.append(entry)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(
                    "Analysis artifact trees may contain only files and directories."
                )
            file_digest, file_size = _regular_file_identity(entry)
            total_size += file_size
            if total_size > MAX_ARTIFACT_TREE_BYTES:
                raise ValueError("Analysis artifact directory exceeds the byte bound.")
            digest.update(
                b"F\0"
                + relative
                + b"\0"
                + str(file_size).encode("ascii")
                + b"\0"
                + file_digest.encode("ascii")
                + b"\0"
            )
        pending.extend(reversed(directories))
    if entry_count == 0 or total_size <= 0:
        raise ValueError("Analysis artifact directory must contain model bytes.")
    return digest.hexdigest(), total_size


def _regular_file_identity(path: Path) -> tuple[str, int]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("Analysis artifact file could not be opened safely.") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) & 0o077
        ):
            raise ValueError("Analysis artifact files must be owner-private.")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = lambda item: (
        item.st_dev,
        item.st_ino,
        item.st_mode,
        item.st_size,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )
    if identity(before) != identity(after):
        raise ValueError("Analysis artifact file changed while it was read.")
    return digest.hexdigest(), before.st_size


def _source_payload(source: LocalAnalysisSource) -> dict[str, Any]:
    return {
        "source_id": source.source_id,
        "path": os.fspath(source.path),
        "sha256": source.sha256,
        "size_bytes": source.size_bytes,
    }


def _require_source_identity(source: LocalAnalysisSource) -> None:
    try:
        digest, size = analysis_artifact_identity(source.path)
    except (OSError, ValueError) as exc:
        raise AnalysisCLIError("Configured analysis input became unavailable.") from exc
    if size != source.size_bytes:
        raise AnalysisCLIError("Configured analysis input size changed.")
    if digest != source.sha256:
        raise AnalysisCLIError("Configured analysis input checksum changed.")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AnalysisCLIError(f"Analysis CLI output duplicates key {key!r}.")
        result[key] = value
    return result
