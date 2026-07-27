"""Startup-approved local model composition for the graphical workbench."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import stat
from typing import Any, Callable, Mapping

from notewitness.adapters.analysis_cli import (
    LocalAnalysisCLIAdapter,
    LocalAnalysisCLISettings,
    LocalAnalysisSource,
    analysis_artifact_identity,
)
from notewitness.adapters.ffprobe import FFprobeMediaProbe
from notewitness.adapters.whisper_cli import WhisperCLIAdapter, WhisperCLISettings
from notewitness.application.analysis_runtime import (
    LocalAnalysisRunRequest,
    LocalAnalysisRuntime,
    LocalAnalysisStep,
)
from notewitness.application.run_integration import (
    PUBLICATION_FILENAME,
    integrate_completed_run,
)
from notewitness.application.speaker_alignment import (
    align_speech_to_anonymous_speakers,
)
from notewitness.application.pedagogical_digest import suggest_practice_relations
from notewitness.application.transcription_runtime import (
    LocalTranscriptionRequest,
    LocalTranscriptionRuntime,
)
from notewitness.application.workbench import resolve_media_source
from notewitness.application.workbench_processing import (
    WorkbenchJobKind,
    WorkbenchProcessingError,
)
from notewitness.domain.analysis import AnalysisStage
from notewitness.domain.timeline import MediaSpan
from notewitness.local_tools import LocalTool, discover_local_tool
from notewitness.project_store import ProjectStore


MAX_RUNTIME_CONFIG_BYTES = 64 * 1024
_DEFAULT_ANALYSIS_STAGES = (
    AnalysisStage.ACTIVITY_SEGMENTATION,
    AnalysisStage.ANONYMOUS_DIARIZATION,
    AnalysisStage.NOTE_TRANSCRIPTION,
    AnalysisStage.CONTINUOUS_PITCH,
    AnalysisStage.INSTRUMENT_DETECTION,
)
_COMPLETE_PASS_MODALITIES = (
    "speech_transcription",
    "activity_segmentation",
    "anonymous_diarization",
    "note_transcription",
    "instrument_diarization",
)


class WorkbenchRuntimeConfigurationError(WorkbenchProcessingError):
    """An owner-supplied runtime configuration is unsafe or incomplete."""


class WorkbenchRunCancelled(WorkbenchProcessingError):
    """The durable GUI job requested cancellation between local stages."""


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


class LocalWorkbenchExecutor:
    """Execute only tools and artifacts approved when the server started."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        transcription: _TranscriptionProfile | None,
        analysis: _AnalysisProfile | None,
    ) -> None:
        self.project_root = ProjectStore(project_root).root
        self.transcription = transcription
        self.analysis = analysis

    @classmethod
    def from_private_config(
        cls,
        project_root: str | Path,
        config_path: str | Path,
    ) -> LocalWorkbenchExecutor:
        payload = _read_private_configuration(Path(config_path))
        _exact_keys(payload, {"version", "transcription", "analysis"}, {"version"})
        version = payload.get("version")
        if version not in {1, 2}:
            raise WorkbenchRuntimeConfigurationError("runtime_config_version_unsupported")
        transcription_raw = payload.get("transcription")
        analysis_raw = payload.get("analysis")
        transcription = (
            _transcription_profile(_object(transcription_raw, "transcription"))
            if transcription_raw is not None
            else None
        )
        analysis = (
            _analysis_profile(
                _object(analysis_raw, "analysis"),
                ProjectStore(project_root).root,
                version=version,
            )
            if analysis_raw is not None
            else None
        )
        if transcription is None and analysis is None:
            raise WorkbenchRuntimeConfigurationError("runtime_config_has_no_engines")
        return cls(project_root, transcription=transcription, analysis=analysis)

    def status(self) -> Mapping[str, object]:
        configured_stages = set(self.analysis.stages) if self.analysis is not None else set()
        modalities = {
            "speech_transcription": self.transcription is not None,
            "activity_segmentation": AnalysisStage.ACTIVITY_SEGMENTATION in configured_stages,
            "anonymous_diarization": (
                self.analysis is not None
                and self.analysis.diarization_mode != "off"
                and AnalysisStage.ANONYMOUS_DIARIZATION in configured_stages
            ),
            "note_transcription": AnalysisStage.NOTE_TRANSCRIPTION in configured_stages,
            "instrument_detection": bool(
                {
                    AnalysisStage.INSTRUMENT_DETECTION,
                    AnalysisStage.INSTRUMENT_DIARIZATION,
                }
                & configured_stages
            ),
            "instrument_diarization": AnalysisStage.INSTRUMENT_DIARIZATION in configured_stages,
        }
        missing_complete_modalities = [
            modality for modality in _COMPLETE_PASS_MODALITIES if not modalities[modality]
        ]
        return {
            "analysis_ready": self.analysis is not None,
            "analysis_stages": (
                [stage.value for stage in self.analysis.stages]
                if self.analysis is not None
                else []
            ),
            "configured": True,
            "complete_ready": not missing_complete_modalities,
            "missing_complete_modalities": missing_complete_modalities,
            "modalities": modalities,
            "network_used": False,
            "transcription_ready": modalities["speech_transcription"],
        }

    def ingest_probe(self) -> FFprobeMediaProbe | None:
        tool = self.transcription.ffprobe if self.transcription is not None else (
            self.analysis.ffprobe if self.analysis is not None else None
        )
        return FFprobeMediaProbe(tool) if tool is not None else None

    def execute(
        self,
        kind: WorkbenchJobKind,
        source_id: str,
        *,
        job_id: str,
        attempt: int,
        cancellation_requested: Callable[[], bool],
        report_progress: Callable[[int, str], None],
        completed_steps: frozenset[str],
        mark_step_completed: Callable[[str], None],
    ) -> None:
        requested = {
            WorkbenchJobKind.TRANSCRIPTION: ("transcription",),
            WorkbenchJobKind.ANALYSIS: ("analysis",),
            WorkbenchJobKind.COMPLETE: ("transcription", "analysis"),
        }[kind]
        remaining = [step for step in requested if step not in completed_steps]
        if not remaining:
            report_progress(99, "Previously completed local stages verified")
            return
        for index, step in enumerate(remaining):
            if cancellation_requested():
                raise WorkbenchRunCancelled("local_processing_cancelled")
            if _recover_completed_workbench_run(
                self.project_root,
                job_id=job_id,
                step=step,
                attempt=attempt,
            ):
                report_progress(
                    48 if step == "transcription" else 96,
                    f"Recovered completed {step} evidence without rerunning the model",
                )
            elif step == "transcription":
                self._transcribe(
                    source_id,
                    cancellation_requested,
                    report_progress,
                    run_token=_workbench_run_token(job_id, step, attempt),
                )
            else:
                self._analyze(
                    source_id,
                    cancellation_requested,
                    report_progress,
                    run_token=_workbench_run_token(job_id, step, attempt),
                )
            mark_step_completed(step)
            if index + 1 < len(remaining):
                report_progress(52, "Speech evidence saved; preparing music analysis")
        if cancellation_requested():
            raise WorkbenchRunCancelled("local_processing_cancelled")
        report_progress(99, "Final local evidence checks complete")

    def _transcribe(
        self,
        source_id: str,
        cancellation_requested: Callable[[], bool],
        report_progress: Callable[[int, str], None],
        *,
        run_token: str,
    ) -> None:
        if self.transcription is None:
            raise WorkbenchRuntimeConfigurationError("transcription_runtime_not_ready")
        report_progress(8, "Transcribing speech with the approved local checkpoint")
        profile = self.transcription
        _require_checkpoint_identity(profile)
        try:
            LocalTranscriptionRuntime(
                media_probe=FFprobeMediaProbe(profile.ffprobe),
                asr=WhisperCLIAdapter(
                    profile.whisper,
                    profile.settings,
                    ffmpeg=profile.ffmpeg,
                ),
            ).run(
                LocalTranscriptionRequest(
                    self.project_root,
                    source_id,
                    run_token=run_token,
                ),
                cancellation_requested=cancellation_requested,
            )
        finally:
            _require_checkpoint_identity(profile)
        align_speech_to_anonymous_speakers(self.project_root)
        suggest_practice_relations(str(self.project_root))
        report_progress(48, "Speech suggestions saved for human review")

    def _analyze(
        self,
        source_id: str,
        cancellation_requested: Callable[[], bool],
        report_progress: Callable[[int, str], None],
        *,
        run_token: str,
    ) -> None:
        if self.analysis is None:
            raise WorkbenchRuntimeConfigurationError("analysis_runtime_not_ready")
        profile = self.analysis
        _, source, relative = resolve_media_source(str(self.project_root), source_id)
        media_path = self.project_root.joinpath(*relative.parts)
        media_sha256, media_size = _stable_file_identity(media_path)
        if media_sha256 != source.get("sha256"):
            raise WorkbenchRuntimeConfigurationError("media_checksum_changed")
        media = LocalAnalysisSource(source_id, media_path, media_sha256, media_size)
        duration_us = FFprobeMediaProbe(profile.ffprobe).inspect(media_path).duration_us
        planned_steps: list[LocalAnalysisStep] = []
        for provider in profile.providers:
            parameters = _analysis_parameters(profile, provider)
            parameters_sha256 = hashlib.sha256(
                json.dumps(
                    parameters,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            planned_steps.append(LocalAnalysisStep(
                LocalAnalysisCLIAdapter(
                    provider.tool,
                    stage=provider.stage,
                    version=provider.adapter_version,
                    generator_id=(
                        f"generator:analysis-{provider.stage.value}-"
                        f"{provider.tool.identity.sha256[:8]}-"
                        f"{provider.model.sha256[:8]}-"
                        f"{parameters_sha256[:8]}"
                    ),
                    settings=LocalAnalysisCLISettings(
                        working_directory=self.project_root,
                        media=media,
                        model=provider.model,
                        model_license=provider.model_license,
                        adapter_license=provider.adapter_license,
                        timeout_seconds=provider.timeout_seconds,
                        score=profile.score,
                        score_license=profile.score_license,
                    ),
                ),
                parameters,
            ))
        steps = tuple(planned_steps)
        report_progress(56 if self.transcription is not None else 8,
                        "Detecting speakers, instruments, notes, pitch, and activity locally")
        LocalAnalysisRuntime().run(
            LocalAnalysisRunRequest(
                project_root=self.project_root,
                source_id=source_id,
                spans=(MediaSpan(source_id, "audio", 0, duration_us),),
                steps=steps,
                run_token=run_token,
            ),
            cancellation_requested=cancellation_requested,
        )
        align_speech_to_anonymous_speakers(self.project_root)
        suggest_practice_relations(str(self.project_root))
        report_progress(96, "Music and teaching suggestions saved for human review")


def _transcription_profile(payload: Mapping[str, Any]) -> _TranscriptionProfile:
    required = {
        "adapter_license",
        "ffmpeg_license",
        "ffmpeg_path",
        "ffprobe_path",
        "model_checkpoint",
        "model_license",
        "whisper_path",
    }
    allowed = required | {"beam_size", "device", "language", "threads", "timeout_seconds"}
    _exact_keys(payload, allowed, required)
    whisper = discover_local_tool("whisper", _path(payload, "whisper_path"))
    ffprobe = discover_local_tool("ffprobe", _path(payload, "ffprobe_path"))
    ffmpeg = discover_local_tool("ffmpeg", _path(payload, "ffmpeg_path"))
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
    checkpoint_sha256, checkpoint_size = _stable_file_identity(
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
    payload: Mapping[str, Any],
    project_root: Path,
    *,
    version: int,
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
    tool = discover_local_tool("analysis-suite", _path(payload, "analysis_path"))
    ffprobe = discover_local_tool("ffprobe", _path(payload, "ffprobe_path"))
    model_path = _path(payload, "model_path")
    model_sha, model_size = analysis_artifact_identity(model_path)
    model = LocalAnalysisSource(
        f"model:analysis-{model_sha[:32]}", model_path, model_sha, model_size
    )
    raw_stages = payload.get("stages")
    if raw_stages is None:
        stages = _DEFAULT_ANALYSIS_STAGES
    elif not isinstance(raw_stages, list) or not raw_stages:
        raise WorkbenchRuntimeConfigurationError("analysis_stages_invalid")
    else:
        try:
            stages = tuple(AnalysisStage(item) for item in raw_stages)
        except (TypeError, ValueError) as exc:
            raise WorkbenchRuntimeConfigurationError("analysis_stages_invalid") from exc
    if len(stages) != len(set(stages)):
        raise WorkbenchRuntimeConfigurationError("analysis_stages_repeated")
    score_fields = (payload.get("score_path"), payload.get("score_id"), payload.get("score_license"))
    if any(value is not None for value in score_fields) and not all(value is not None for value in score_fields):
        raise WorkbenchRuntimeConfigurationError("score_configuration_incomplete")
    score = None
    score_license = None
    if score_fields[0] is not None:
        score_path = _path(payload, "score_path")
        score_sha, score_size = _stable_file_identity(score_path)
        score = LocalAnalysisSource(
            _bounded_text(payload, "score_id"), score_path, score_sha, score_size
        )
        score_license = _bounded_text(payload, "score_license")
    if AnalysisStage.SCORE_ALIGNMENT in stages and score is None:
        raise WorkbenchRuntimeConfigurationError("score_alignment_requires_score")
    mode = _bounded_text(payload, "diarization_mode", "auto")
    if mode not in {"off", "auto", "exact"}:
        raise WorkbenchRuntimeConfigurationError("diarization_mode_invalid")
    exact = payload.get("exact_speaker_count")
    if exact is not None and (
        not isinstance(exact, int) or isinstance(exact, bool) or not 1 <= exact <= 10
    ):
        raise WorkbenchRuntimeConfigurationError("exact_speaker_count_invalid")
    if (mode == "exact") != (exact is not None):
        raise WorkbenchRuntimeConfigurationError("exact_diarization_configuration_invalid")
    detect_overlap = payload.get("detect_overlap", False)
    if not isinstance(detect_overlap, bool):
        raise WorkbenchRuntimeConfigurationError("detect_overlap_must_be_boolean")
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
        ffprobe=ffprobe,
        providers=providers,
        diarization_mode=mode,
        exact_speaker_count=exact,
        detect_overlap=detect_overlap,
        score=score,
        score_license=score_license,
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
    ffprobe = discover_local_tool("ffprobe", _path(payload, "ffprobe_path"))
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
        ffprobe=ffprobe,
        providers=providers,
        diarization_mode=mode,
        exact_speaker_count=exact,
        detect_overlap=detect_overlap,
        score=score,
        score_license=score_license,
    )


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
        AnalysisStage.ACTIVITY_SEGMENTATION,
        AnalysisStage.ANONYMOUS_DIARIZATION,
        AnalysisStage.NOTE_TRANSCRIPTION,
        AnalysisStage.CONTINUOUS_PITCH,
        AnalysisStage.INSTRUMENT_DETECTION,
        AnalysisStage.INSTRUMENT_DIARIZATION,
        AnalysisStage.SCORE_ALIGNMENT,
    }
    if stage not in supported:
        raise WorkbenchRuntimeConfigurationError(
            "analysis_provider_stage_invalid"
        )
    parameters = _provider_parameters(payload.get("parameters"))
    if stage in {
        AnalysisStage.ANONYMOUS_DIARIZATION,
        AnalysisStage.NOTE_TRANSCRIPTION,
        AnalysisStage.SCORE_ALIGNMENT,
    } and parameters:
        raise WorkbenchRuntimeConfigurationError(
            "analysis_provider_parameters_invalid"
        )
    model_path = _path(payload, "model_path")
    model_sha, model_size = analysis_artifact_identity(model_path)
    return _AnalysisProviderProfile(
        stage=stage,
        tool=discover_local_tool(
            f"analysis-bridge-{index}", _path(payload, "analysis_path")
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
    score_sha, score_size = _stable_file_identity(score_path)
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
        raise WorkbenchRuntimeConfigurationError(
            "analysis_provider_parameters_invalid"
        )

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
        raise WorkbenchRuntimeConfigurationError(
            "analysis_provider_parameters_invalid"
        )

    return validate(value)  # type: ignore[return-value]


def _diarization_options(
    payload: Mapping[str, Any],
) -> tuple[str, int | None, bool]:
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
        raise WorkbenchRuntimeConfigurationError(
            "detect_overlap_must_be_boolean"
        )
    return mode, exact, detect_overlap


def _analysis_parameters(
    profile: _AnalysisProfile,
    provider: _AnalysisProviderProfile,
) -> dict[str, object]:
    stage = provider.stage
    parameters = dict(provider.parameters)
    if stage is AnalysisStage.ANONYMOUS_DIARIZATION:
        parameters.update({
            "detect_overlap": profile.detect_overlap,
            "diarization_mode": profile.diarization_mode,
            "exact_speaker_count": profile.exact_speaker_count,
        })
    if stage is AnalysisStage.SCORE_ALIGNMENT:
        parameters["score_id"] = profile.score.source_id if profile.score else None
    return parameters


def _workbench_run_token(job_id: str, step: str, attempt: int) -> str:
    if (
        not isinstance(job_id, str)
        or not job_id.startswith("job:workbench-")
        or step not in {"transcription", "analysis"}
        or not isinstance(attempt, int)
        or isinstance(attempt, bool)
        or not 1 <= attempt <= 100
    ):
        raise WorkbenchRuntimeConfigurationError("workbench_run_identity_invalid")
    material = f"notewitness-workbench-v1\0{job_id}\0{step}\0{attempt}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _recover_completed_workbench_run(
    project_root: Path,
    *,
    job_id: str,
    step: str,
    attempt: int,
) -> bool:
    for prior_attempt in range(1, attempt + 1):
        token = _workbench_run_token(job_id, step, prior_attempt)
        run_id = f"run:{'analysis-' if step == 'analysis' else ''}{token}"
        directory_name = f"analysis-{token}" if step == "analysis" else token
        publication_path = (
            project_root / "runs" / directory_name / PUBLICATION_FILENAME
        )
        if not publication_path.exists() and not publication_path.is_symlink():
            continue
        integrate_completed_run(project_root, run_id)
        return True
    return False


def _require_checkpoint_identity(profile: _TranscriptionProfile) -> None:
    current_sha256, current_size = _stable_file_identity(
        profile.settings.model_checkpoint
    )
    if (
        current_sha256 != profile.checkpoint_sha256
        or current_size != profile.checkpoint_size_bytes
    ):
        raise WorkbenchRuntimeConfigurationError(
            "configured_transcription_checkpoint_changed"
        )


def _read_private_configuration(path: Path) -> Mapping[str, Any]:
    if not path.is_absolute():
        raise WorkbenchRuntimeConfigurationError("runtime_config_path_must_be_absolute")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise WorkbenchRuntimeConfigurationError("runtime_config_unavailable") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or not 0 < metadata.st_size <= MAX_RUNTIME_CONFIG_BYTES
    ):
        raise WorkbenchRuntimeConfigurationError("runtime_config_must_be_owner_private")
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkbenchRuntimeConfigurationError("runtime_config_json_invalid") from exc
    if not isinstance(payload, dict):
        raise WorkbenchRuntimeConfigurationError("runtime_config_must_be_object")
    return payload


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
        raise WorkbenchRuntimeConfigurationError(f"runtime_config_{label}_must_be_object")
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
    if value is None:
        return None
    return _bounded_text(payload, key)


def _integer(payload: Mapping[str, Any], key: str, default: int) -> int:
    value = payload.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise WorkbenchRuntimeConfigurationError("runtime_config_integer_invalid")
    return value


def _stable_file_identity(path: Path) -> tuple[str, int]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise WorkbenchRuntimeConfigurationError("configured_artifact_unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
            raise WorkbenchRuntimeConfigurationError("configured_artifact_not_regular")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
    if identity(before) != identity(after):
        raise WorkbenchRuntimeConfigurationError("configured_artifact_changed")
    return digest.hexdigest(), before.st_size
