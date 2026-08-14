"""Compatibility façade for strict, offline JSON-speaking analysis executables."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping

from notewitness.domain.analysis import (
    AnalysisBatch,
    AnalysisRequest,
    AnalysisStage,
)
from notewitness.domain.timeline import MediaSpan
from notewitness.local_tools import (
    BoundedLocalToolRunner,
    LocalTool,
    LocalToolCancelled,
    LocalToolError,
    LocalToolFailure,
)

from . import analysis_cli_identity as _identity
from . import analysis_cli_protocol as _protocol


MAX_JSON_BYTES = _protocol.MAX_JSON_BYTES
MAX_JSON_DEPTH = _protocol.MAX_JSON_DEPTH
MAX_JSON_ITEMS = _protocol.MAX_JSON_ITEMS
MAX_STRING_CHARS = _protocol.MAX_STRING_CHARS
MAX_ARTIFACT_TREE_ENTRIES = _identity.MAX_ARTIFACT_TREE_ENTRIES
MAX_ARTIFACT_TREE_BYTES = _identity.MAX_ARTIFACT_TREE_BYTES
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

AnalysisCLIError = _protocol.AnalysisCLIError
AnalysisCLIError.__module__ = __name__


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
        object.__setattr__(self, "path", _private_analysis_input(Path(self.path)))
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
        object.__setattr__(
            self,
            "working_directory",
            _private_directory(Path(self.working_directory)),
        )
        if not isinstance(self.media, LocalAnalysisSource):
            raise ValueError("Analysis CLI settings require a media input.")
        if not isinstance(self.model, LocalAnalysisSource):
            raise ValueError("Analysis CLI settings require a model artifact.")
        if self.score is not None and not isinstance(self.score, LocalAnalysisSource):
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
            raise ValueError(
                "This adapter does not support the requested analysis stage."
            )
        if (
            not isinstance(version, str)
            or not version
            or len(version) > MAX_STRING_CHARS
        ):
            raise ValueError("Analysis CLI version must be a bounded non-empty string.")
        if (
            not isinstance(generator_id, str)
            or not generator_id
            or len(generator_id) > MAX_STRING_CHARS
        ):
            raise ValueError(
                "Analysis CLI generator_id must be a bounded non-empty string."
            )
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

        _validate_request(request, self)
        request_bytes = _json_bytes(_request_payload(request, self), "request")
        request_sha256 = hashlib.sha256(request_bytes).hexdigest()
        with TemporaryDirectory(
            prefix="analysis-cli-",
            dir=self.settings.working_directory,
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
                raise AnalysisCLIExecutionError(
                    "Analysis CLI process failed.",
                    request_sha256=request_sha256,
                    raw_output=failure.stdout.encode("utf-8"),
                ) from failure
        _require_configured_source_identities(self)
        raw_output = result.stdout.encode("utf-8")
        try:
            batch = _parse_batch(result.stdout, request, self)
        except AnalysisCLIError as exc:
            raise AnalysisCLIExecutionError(
                "Analysis CLI output could not be normalized.",
                request_sha256=request_sha256,
                raw_output=raw_output,
            ) from exc
        return LocalAnalysisCLIExecution(
            batch=batch,
            request_sha256=request_sha256,
            raw_output=raw_output,
            raw_output_sha256=hashlib.sha256(raw_output).hexdigest(),
            duration_ms=result.duration_ms,
            network_isolated=result.network_isolated,
        )

    def replay(self, request: AnalysisRequest, raw_output: bytes) -> AnalysisBatch:
        """Validate retained exact output without invoking the local executable."""

        _validate_request_identity(request, self)
        if not isinstance(raw_output, bytes) or len(raw_output) > MAX_JSON_BYTES:
            raise AnalysisCLIError(
                "Retained analysis output exceeds the bounded contract."
            )
        _require_configured_source_identities(self)
        try:
            raw = raw_output.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AnalysisCLIError(
                "Retained analysis output must be UTF-8 JSON."
            ) from exc
        return _parse_batch(raw, request, self)


def _validate_request(
    request: AnalysisRequest,
    adapter: LocalAnalysisCLIAdapter,
) -> None:
    _validate_request_identity(request, adapter)
    _require_configured_source_identities(adapter)


def _validate_request_identity(
    request: AnalysisRequest,
    adapter: LocalAnalysisCLIAdapter,
) -> None:
    if not isinstance(request, AnalysisRequest):
        raise TypeError("Analysis CLI requests must be AnalysisRequest instances.")
    if request.source_id != adapter.settings.media.source_id:
        raise AnalysisCLIError(
            "Analysis request source does not match the configured media input."
        )


def _require_configured_source_identities(adapter: LocalAnalysisCLIAdapter) -> None:
    _require_source_identity(adapter.settings.media)
    _require_source_identity(adapter.settings.model)
    if adapter.settings.score is not None:
        _require_source_identity(adapter.settings.score)


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
    return _protocol.parse_batch(
        raw,
        request,
        adapter,
        json_value=_json_value,
        unique_object=_unique_object,
        expect_keys=_expect_keys,
        enum=_enum,
        list_value=_list,
        string_tuple=_string_tuple,
        nullable_string=_nullable_string,
        hypothesis=_hypothesis,
    )


def _hypothesis(
    raw: Any,
    request: AnalysisRequest,
    adapter: LocalAnalysisCLIAdapter,
    index: int,
) -> Any:
    return _protocol.hypothesis(
        raw,
        request,
        adapter,
        index,
        expect_keys_with_optional=_expect_keys_with_optional,
        string=_string,
        enum=_enum,
        span=_span,
        nullable_number=_nullable_number,
        nullable_string=_nullable_string,
        nullable_integer_in_range=_nullable_integer_in_range,
        number_tuple=_number_tuple,
        json_value=_json_value,
        mapping=_mapping,
        string_tuple=_string_tuple,
    )


def _span(raw: Any, request: AnalysisRequest, label: str) -> MediaSpan:
    _expect_keys(raw, {"stream_id", "start_us", "duration_us"}, label)
    stream = _string(raw["stream_id"], f"{label}.stream_id")
    start = _integer(raw["start_us"], f"{label}.start_us")
    duration = _integer(raw["duration_us"], f"{label}.duration_us")
    try:
        parsed_span = MediaSpan(request.source_id, stream, start, duration)
    except ValueError as exc:
        raise AnalysisCLIError(f"{label} is invalid.") from exc
    if not any(
        parsed_span.stream_id == allowed.stream_id
        and allowed.start_us <= parsed_span.start_us
        and parsed_span.end_us <= allowed.end_us
        for allowed in request.spans
    ):
        raise AnalysisCLIError(f"{label} lies outside the requested source span.")
    return parsed_span


def _span_payload(span: MediaSpan, *, include_source: bool) -> dict[str, Any]:
    return _protocol.span_payload(span, include_source=include_source)


def _expect_keys(value: Any, expected: set[str], label: str) -> None:
    if set(_mapping(value, label)) != expected:
        raise AnalysisCLIError(f"{label} has unknown or missing keys.")


def _expect_keys_with_optional(
    value: Any,
    required: set[str],
    optional: set[str],
    label: str,
) -> None:
    keys = set(_mapping(value, label))
    if required - keys or keys - required - optional:
        raise AnalysisCLIError(f"{label} has unknown or missing keys.")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    return _protocol.mapping(value, label)


def _list(value: Any, label: str) -> list[Any]:
    return _protocol.list_value(value, label)


def _string(value: Any, label: str) -> str:
    return _protocol.string(value, label)


def _nullable_string(value: Any, label: str) -> str | None:
    return None if value is None else _string(value, label)


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    result = tuple(_string(item, label) for item in _list(value, label))
    if len(result) > MAX_JSON_ITEMS or len(result) != len(set(result)):
        raise AnalysisCLIError(f"{label} must contain bounded unique strings.")
    return result


def _integer(value: Any, label: str) -> int:
    return _protocol.integer(value, label)


def _nullable_number(value: Any, label: str) -> float | None:
    return _protocol.nullable_number(value, label)


def _nullable_integer_in_range(
    value: Any,
    minimum: int,
    maximum: int,
    label: str,
) -> int | None:
    return _protocol.nullable_integer_in_range(value, minimum, maximum, label)


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
            value,
            separators=(",", ":"),
            allow_nan=False,
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
    _protocol.reject_path_like_values(value, label)


def _private_directory(path: Path) -> Path:
    return _identity.private_directory(path)


def _private_analysis_input(path: Path) -> Path:
    return _identity.private_analysis_input(path)


def analysis_artifact_identity(path: str | Path) -> tuple[str, int]:
    return _identity.analysis_artifact_identity(
        path,
        private_input=_private_analysis_input,
        regular_file_identity=_regular_file_identity,
    )


def _regular_file_identity(path: Path) -> tuple[str, int]:
    return _identity._regular_file_identity(path)


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
    return _protocol.unique_object(pairs)
