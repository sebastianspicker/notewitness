"""Private runtime-configuration parsing for the local workbench executor."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import json
import math
import os
from pathlib import Path
import stat
from typing import Any, Mapping

from notewitness.adapters.analysis_cli import LocalAnalysisSource
from notewitness.adapters.whisper_cli import WhisperCLISettings
from notewitness.application.workbench_processing import WorkbenchProcessingError
from notewitness.domain.analysis import AnalysisStage
from notewitness._local_tool_discovery import validated_trusted_path
from notewitness.local_tools import LocalTool


MAX_RUNTIME_CONFIG_BYTES = 64 * 1024
_DEFAULT_ANALYSIS_STAGES = (
    AnalysisStage.ACTIVITY_SEGMENTATION,
    AnalysisStage.ANONYMOUS_DIARIZATION,
    AnalysisStage.NOTE_TRANSCRIPTION,
    AnalysisStage.CONTINUOUS_PITCH,
    AnalysisStage.INSTRUMENT_DETECTION,
)


class WorkbenchRuntimeConfigurationError(WorkbenchProcessingError):
    """An owner-supplied runtime configuration is unsafe or incomplete."""


@dataclass(frozen=True, slots=True)
class _TranscriptionProfile:
    whisper: LocalTool
    ffprobe: LocalTool
    ffmpeg: LocalTool
    settings: WhisperCLISettings
    checkpoint_sha256: str
    checkpoint_size_bytes: int


@dataclass(frozen=True, slots=True)
class _AnalysisProviderProfile:
    stage: AnalysisStage
    tool: LocalTool
    model: LocalAnalysisSource
    adapter_version: str
    adapter_license: str
    model_license: str
    timeout_seconds: int
    parameters: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _AnalysisProfile:
    ffprobe: LocalTool
    providers: tuple[_AnalysisProviderProfile, ...]
    diarization_mode: str
    exact_speaker_count: int | None
    detect_overlap: bool
    score: LocalAnalysisSource | None
    score_license: str | None

    @property
    def stages(self) -> tuple[AnalysisStage, ...]:
        return tuple(provider.stage for provider in self.providers)


def _legacy(name: str) -> Any:
    from . import workbench_local_executor

    return getattr(workbench_local_executor, name)


def _transcription_profile(payload: Mapping[str, Any]) -> _TranscriptionProfile:
    required = {
        "adapter_license", "ffmpeg_license", "ffmpeg_path", "ffprobe_path",
        "model_checkpoint", "model_license", "whisper_path",
    }
    allowed = required | {
        "beam_size",
        "device",
        "language",
        "threads",
        "timeout_seconds",
    }
    _exact_keys(payload, allowed, required)
    whisper = _legacy("discover_local_tool")("whisper", _path(payload, "whisper_path"))
    ffprobe = _legacy("discover_local_tool")("ffprobe", _path(payload, "ffprobe_path"))
    ffmpeg = _legacy("discover_local_tool")("ffmpeg", _path(payload, "ffmpeg_path"))
    settings = WhisperCLISettings(
        model_checkpoint=_path(payload, "model_checkpoint"),
        model_license=_bounded_text(payload, "model_license"),
        adapter_license=_bounded_text(payload, "adapter_license"),
        ffmpeg_license=_bounded_text(payload, "ffmpeg_license"),
        language=_optional_text(payload, "language"),
        beam_size=_integer(payload, "beam_size", 5),
        threads=_integer(payload, "threads", 0),
        device=_bounded_text(payload, "device", "cpu"),
        timeout_seconds=_integer(payload, "timeout_seconds", 7_200),
    )
    checkpoint_sha256, checkpoint_size = _legacy("_stable_file_identity")(
        settings.model_checkpoint
    )
    return _TranscriptionProfile(
        whisper,
        ffprobe,
        ffmpeg,
        settings,
        checkpoint_sha256,
        checkpoint_size,
    )


def _analysis_profile(
    payload: Mapping[str, Any], project_root: Path, *, version: int
) -> _AnalysisProfile:
    if version == 2:
        return _analysis_profile_v2(payload, project_root)
    return _analysis_profile_v1(payload, project_root)


def _analysis_profile_v1(
    payload: Mapping[str, Any], project_root: Path
) -> _AnalysisProfile:
    required = {
        "adapter_license",
        "adapter_version",
        "analysis_path",
        "ffprobe_path",
        "model_license",
        "model_path",
    }
    allowed = required | {
        "detect_overlap",
        "diarization_mode",
        "exact_speaker_count",
        "score_id",
        "score_license",
        "score_path",
        "stages",
        "timeout_seconds",
    }
    _exact_keys(payload, allowed, required)
    tool = _legacy("discover_local_tool")(
        "analysis-suite", _path(payload, "analysis_path")
    )
    ffprobe = _legacy("discover_local_tool")("ffprobe", _path(payload, "ffprobe_path"))
    model_path = _path(payload, "model_path")
    model_sha, model_size = _legacy("analysis_artifact_identity")(model_path)
    model = LocalAnalysisSource(
        f"model:analysis-{model_sha[:32]}",
        model_path,
        model_sha,
        model_size,
    )
    stages = _analysis_stages(payload.get("stages"))
    score, score_license = _analysis_score(payload)
    if AnalysisStage.SCORE_ALIGNMENT in stages and score is None:
        raise WorkbenchRuntimeConfigurationError("score_alignment_requires_score")
    mode, exact, detect_overlap = _diarization_options(payload)
    providers = tuple(
        _AnalysisProviderProfile(
            stage=stage,
            tool=tool,
            model=model,
            adapter_version=_bounded_text(payload, "adapter_version"),
            adapter_license=_bounded_text(payload, "adapter_license"),
            model_license=_bounded_text(payload, "model_license"),
            timeout_seconds=_integer(payload, "timeout_seconds", 3_600),
            parameters={},
        )
        for stage in stages
    )
    return _AnalysisProfile(
        ffprobe,
        providers,
        mode,
        exact,
        detect_overlap,
        score,
        score_license,
    )


def _analysis_profile_v2(
    payload: Mapping[str, Any], project_root: Path
) -> _AnalysisProfile:
    required = {"ffprobe_path", "providers"}
    allowed = required | {
        "detect_overlap",
        "diarization_mode",
        "exact_speaker_count",
        "score_id",
        "score_license",
        "score_path",
    }
    _exact_keys(payload, allowed, required)
    ffprobe = _legacy("discover_local_tool")("ffprobe", _path(payload, "ffprobe_path"))
    raw_providers = payload.get("providers")
    if (
        not isinstance(raw_providers, list)
        or not raw_providers
        or len(raw_providers) > 16
    ):
        raise WorkbenchRuntimeConfigurationError("analysis_providers_invalid")
    providers = tuple(
        _analysis_provider_profile(
            _object(item, f"analysis_provider_{index}"),
            index=index,
        )
        for index, item in enumerate(raw_providers, start=1)
    )
    stages = tuple(provider.stage for provider in providers)
    if len(stages) != len(set(stages)):
        raise WorkbenchRuntimeConfigurationError("analysis_stages_repeated")
    score, score_license = _analysis_score(payload)
    if AnalysisStage.SCORE_ALIGNMENT in stages and score is None:
        raise WorkbenchRuntimeConfigurationError("score_alignment_requires_score")
    mode, exact, detect_overlap = _diarization_options(payload)
    return _AnalysisProfile(
        ffprobe,
        providers,
        mode,
        exact,
        detect_overlap,
        score,
        score_license,
    )


def _analysis_stages(value: object) -> tuple[AnalysisStage, ...]:
    if value is None:
        stages = _DEFAULT_ANALYSIS_STAGES
    elif not isinstance(value, list) or not value:
        raise WorkbenchRuntimeConfigurationError("analysis_stages_invalid")
    else:
        try:
            stages = tuple(AnalysisStage(item) for item in value)
        except (TypeError, ValueError) as exc:
            raise WorkbenchRuntimeConfigurationError("analysis_stages_invalid") from exc
    if len(stages) != len(set(stages)):
        raise WorkbenchRuntimeConfigurationError("analysis_stages_repeated")
    return stages


def _analysis_provider_profile(
    payload: Mapping[str, Any], *, index: int
) -> _AnalysisProviderProfile:
    required = {
        "adapter_license",
        "adapter_version",
        "analysis_path",
        "model_license",
        "model_path",
        "stage",
    }
    allowed = required | {"parameters", "timeout_seconds"}
    _exact_keys(payload, allowed, required)
    try:
        stage = AnalysisStage(_bounded_text(payload, "stage"))
    except ValueError as exc:
        raise WorkbenchRuntimeConfigurationError(
            "analysis_provider_stage_invalid"
        ) from exc
    supported = {
        AnalysisStage.ACTIVITY_SEGMENTATION, AnalysisStage.ANONYMOUS_DIARIZATION,
        AnalysisStage.NOTE_TRANSCRIPTION, AnalysisStage.CONTINUOUS_PITCH,
        AnalysisStage.INSTRUMENT_DETECTION, AnalysisStage.INSTRUMENT_DIARIZATION,
        AnalysisStage.SCORE_ALIGNMENT,
    }
    if stage not in supported:
        raise WorkbenchRuntimeConfigurationError("analysis_provider_stage_invalid")
    parameters = _provider_parameters(payload.get("parameters"))
    if stage in {
        AnalysisStage.ANONYMOUS_DIARIZATION,
        AnalysisStage.NOTE_TRANSCRIPTION,
        AnalysisStage.SCORE_ALIGNMENT,
    } and parameters:
        raise WorkbenchRuntimeConfigurationError("analysis_provider_parameters_invalid")
    model_path = _path(payload, "model_path")
    model_sha, model_size = _legacy("analysis_artifact_identity")(model_path)
    return _AnalysisProviderProfile(
        stage=stage,
        tool=_legacy("discover_local_tool")(
            f"analysis-bridge-{index}",
            _path(payload, "analysis_path"),
        ),
        model=LocalAnalysisSource(
            f"model:{stage.value}-{model_sha[:32]}",
            model_path,
            model_sha,
            model_size,
        ),
        adapter_version=_bounded_text(payload, "adapter_version"),
        adapter_license=_bounded_text(payload, "adapter_license"),
        model_license=_bounded_text(payload, "model_license"),
        timeout_seconds=_integer(payload, "timeout_seconds", 3_600),
        parameters=parameters,
    )


def _analysis_score(
    payload: Mapping[str, Any],
) -> tuple[LocalAnalysisSource | None, str | None]:
    score_fields = (
        payload.get("score_path"),
        payload.get("score_id"),
        payload.get("score_license"),
    )
    if any(value is not None for value in score_fields) and not all(
        value is not None for value in score_fields
    ):
        raise WorkbenchRuntimeConfigurationError("score_configuration_incomplete")
    if score_fields[0] is None:
        return None, None
    score_path = _path(payload, "score_path")
    score_sha, score_size = _legacy("_stable_file_identity")(score_path)
    return (
        LocalAnalysisSource(
            _bounded_text(payload, "score_id"),
            score_path,
            score_sha,
            score_size,
        ),
        _bounded_text(payload, "score_license"),
    )


def _provider_parameters(value: object) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 32:
        raise WorkbenchRuntimeConfigurationError("analysis_provider_parameters_invalid")
    def validate(item: object, depth: int = 0) -> object:
        if depth > 8:
            raise WorkbenchRuntimeConfigurationError(
                "analysis_provider_parameters_invalid"
            )
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise WorkbenchRuntimeConfigurationError(
                    "analysis_provider_parameters_invalid"
                )
            return item
        if isinstance(item, str):
            if len(item) > 4_096 or item.startswith(("/", "~")):
                raise WorkbenchRuntimeConfigurationError(
                    "analysis_provider_parameters_invalid"
                )
            return item
        if isinstance(item, list) and len(item) <= 1_024:
            return [validate(child, depth + 1) for child in item]
        if isinstance(item, dict) and len(item) <= 1_024:
            result: dict[str, object] = {}
            for key, child in item.items():
                if (
                    not isinstance(key, str)
                    or not key
                    or len(key) > 256
                    or "path" in key.casefold()
                    or "media" in key.casefold()
                ):
                    raise WorkbenchRuntimeConfigurationError(
                        "analysis_provider_parameters_invalid"
                    )
                result[key] = validate(child, depth + 1)
            return result
        raise WorkbenchRuntimeConfigurationError("analysis_provider_parameters_invalid")
    return validate(value)  # type: ignore[return-value]


def _diarization_options(payload: Mapping[str, Any]) -> tuple[str, int | None, bool]:
    mode = _bounded_text(payload, "diarization_mode", "auto")
    if mode not in {"off", "auto", "exact"}:
        raise WorkbenchRuntimeConfigurationError("diarization_mode_invalid")
    exact = payload.get("exact_speaker_count")
    if exact is not None and (
        not isinstance(exact, int)
        or isinstance(exact, bool)
        or not 1 <= exact <= 10
    ):
        raise WorkbenchRuntimeConfigurationError("exact_speaker_count_invalid")
    if (mode == "exact") != (exact is not None):
        raise WorkbenchRuntimeConfigurationError(
            "exact_diarization_configuration_invalid"
        )
    detect_overlap = payload.get("detect_overlap", False)
    if not isinstance(detect_overlap, bool):
        raise WorkbenchRuntimeConfigurationError("detect_overlap_must_be_boolean")
    return mode, exact, detect_overlap


def _analysis_parameters(
    profile: _AnalysisProfile,
    provider: _AnalysisProviderProfile,
) -> dict[str, object]:
    parameters = dict(provider.parameters)
    if provider.stage is AnalysisStage.ANONYMOUS_DIARIZATION:
        parameters.update(
            {
                "detect_overlap": profile.detect_overlap,
                "diarization_mode": profile.diarization_mode,
                "exact_speaker_count": profile.exact_speaker_count,
            }
        )
    if provider.stage is AnalysisStage.SCORE_ALIGNMENT:
        parameters["score_id"] = profile.score.source_id if profile.score else None
    return parameters


def _read_private_configuration(path: Path) -> Mapping[str, Any]:
    if not path.is_absolute():
        raise WorkbenchRuntimeConfigurationError("runtime_config_path_must_be_absolute")
    try:
        parent = validated_trusted_path(path.parent, kind="directory")
    except ValueError as exc:
        raise WorkbenchRuntimeConfigurationError(
            "runtime_config_must_be_owner_private"
        ) from exc
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(parent / path.name, flags)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise WorkbenchRuntimeConfigurationError("runtime_config_unavailable") from exc
        raise WorkbenchRuntimeConfigurationError(
            "runtime_config_must_be_owner_private"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not _private_runtime_config_stat(before):
            raise WorkbenchRuntimeConfigurationError(
                "runtime_config_must_be_owner_private"
            )
        content = bytearray()
        while len(content) <= MAX_RUNTIME_CONFIG_BYTES:
            chunk = os.read(
                descriptor,
                min(8192, MAX_RUNTIME_CONFIG_BYTES + 1 - len(content)),
            )
            if not chunk:
                break
            content.extend(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise WorkbenchRuntimeConfigurationError("runtime_config_json_invalid") from exc
    finally:
        os.close(descriptor)
    if len(content) > MAX_RUNTIME_CONFIG_BYTES or _runtime_config_stat_identity(
        before
    ) != _runtime_config_stat_identity(after):
        raise WorkbenchRuntimeConfigurationError("runtime_config_must_be_owner_private")
    try:
        payload = json.loads(
            bytes(content).decode("utf-8"), object_pairs_hook=_unique_object
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WorkbenchRuntimeConfigurationError("runtime_config_json_invalid") from exc
    if not isinstance(payload, dict):
        raise WorkbenchRuntimeConfigurationError("runtime_config_must_be_object")
    return payload


def _private_runtime_config_stat(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and not stat.S_IMODE(metadata.st_mode) & 0o077
        and 0 < metadata.st_size <= MAX_RUNTIME_CONFIG_BYTES
    )


def _runtime_config_stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
        stat.S_IMODE(metadata.st_mode),
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkbenchRuntimeConfigurationError("runtime_config_duplicate_key")
        result[key] = value
    return result


def _exact_keys(
    payload: Mapping[str, Any],
    allowed: set[str],
    required: set[str],
) -> None:
    if set(payload) - allowed or required - set(payload):
        raise WorkbenchRuntimeConfigurationError("runtime_config_keys_invalid")


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise WorkbenchRuntimeConfigurationError(
            f"runtime_config_{label}_must_be_object"
        )
    return value


def _path(payload: Mapping[str, Any], key: str) -> Path:
    value = payload.get(key)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise WorkbenchRuntimeConfigurationError("runtime_config_path_invalid")
    path = Path(value)
    if not path.is_absolute():
        raise WorkbenchRuntimeConfigurationError("runtime_config_path_must_be_absolute")
    return path


def _bounded_text(
    payload: Mapping[str, Any],
    key: str,
    default: str | None = None,
) -> str:
    value = payload.get(key, default)
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise WorkbenchRuntimeConfigurationError("runtime_config_text_invalid")
    return value.strip()


def _optional_text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return None if value is None else _bounded_text(payload, key)


def _integer(payload: Mapping[str, Any], key: str, default: int) -> int:
    value = payload.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise WorkbenchRuntimeConfigurationError("runtime_config_integer_invalid")
    return value
