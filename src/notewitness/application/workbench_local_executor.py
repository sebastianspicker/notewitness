"""Startup-approved orchestration for local workbench processing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable, Mapping

from notewitness.adapters.analysis_cli import (
    LocalAnalysisCLIAdapter,
    LocalAnalysisCLISettings,
    LocalAnalysisSource,
    analysis_artifact_identity,
)
from notewitness.adapters.ffprobe import FFprobeMediaProbe
from notewitness.adapters.whisper_cli import WhisperCLIAdapter
from notewitness.application.analysis_runtime import (
    LocalAnalysisRunRequest,
    LocalAnalysisRuntime,
    LocalAnalysisStep,
)
from notewitness.application.pedagogical_digest import suggest_practice_relations
from notewitness.application.run_integration import integrate_completed_run
from notewitness.application.speaker_alignment import align_speech_to_anonymous_speakers
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
from notewitness.local_tools import discover_local_tool
from notewitness.project_store import ProjectStore

from ._workbench_executor_config import (
    MAX_RUNTIME_CONFIG_BYTES,
    _DEFAULT_ANALYSIS_STAGES,
    _AnalysisProfile,
    _AnalysisProviderProfile,
    _TranscriptionProfile,
    _analysis_parameters,
    _analysis_profile,
    _analysis_profile_v1,
    _analysis_profile_v2,
    _analysis_provider_profile,
    _analysis_score,
    _analysis_stages,
    _bounded_text,
    _diarization_options,
    _exact_keys,
    _integer,
    _object,
    _optional_text,
    _path,
    _provider_parameters,
    _read_private_configuration,
    _transcription_profile,
    _unique_object,
    WorkbenchRuntimeConfigurationError,
)
from ._workbench_executor_identity import (
    _recover_completed_workbench_run,
    _require_checkpoint_identity,
    _stable_file_identity,
    _workbench_run_token,
)


_COMPLETE_PASS_MODALITIES = (
    "speech_transcription",
    "activity_segmentation",
    "anonymous_diarization",
    "note_transcription",
    "instrument_diarization",
)


class WorkbenchRunCancelled(WorkbenchProcessingError):
    """The durable GUI job requested cancellation between local stages."""


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
        cls, project_root: str | Path, config_path: str | Path
    ) -> "LocalWorkbenchExecutor":
        payload = _read_private_configuration(Path(config_path))
        _exact_keys(payload, {"version", "transcription", "analysis"}, {"version"})
        version = payload.get("version")
        if version not in {1, 2}:
            raise WorkbenchRuntimeConfigurationError(
                "runtime_config_version_unsupported"
            )
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
        configured_stages = (
            set(self.analysis.stages) if self.analysis is not None else set()
        )
        modalities = {
            "speech_transcription": self.transcription is not None,
            "activity_segmentation": (
                AnalysisStage.ACTIVITY_SEGMENTATION in configured_stages
            ),
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
            "instrument_diarization": (
                AnalysisStage.INSTRUMENT_DIARIZATION in configured_stages
            ),
        }
        missing_complete_modalities = [
            modality
            for modality in _COMPLETE_PASS_MODALITIES
            if not modalities[modality]
        ]
        return {
            "analysis_ready": self.analysis is not None,
            "analysis_stages": (
                [stage.value for stage in self.analysis.stages]
                if self.analysis
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
        tool = self.transcription.ffprobe if self.transcription else (
            self.analysis.ffprobe if self.analysis else None
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
                self.project_root, job_id=job_id, step=step, attempt=attempt
            ):
                report_progress(
                    48 if step == "transcription" else 96,
                    f"Recovered completed {step} evidence without rerunning the model",
                )
            elif step == "transcription":
                self._transcribe(
                    source_id, cancellation_requested, report_progress,
                    run_token=_workbench_run_token(job_id, step, attempt),
                )
            else:
                self._analyze(
                    source_id, cancellation_requested, report_progress,
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
        steps = tuple(
            self._analysis_step(profile, provider, media)
            for provider in profile.providers
        )
        report_progress(
            56 if self.transcription else 8,
            "Detecting speakers, instruments, notes, pitch, and activity locally",
        )
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

    def _analysis_step(
        self,
        profile: _AnalysisProfile,
        provider: _AnalysisProviderProfile,
        media: LocalAnalysisSource,
    ) -> LocalAnalysisStep:
        parameters = _analysis_parameters(profile, provider)
        parameters_sha256 = hashlib.sha256(
            json.dumps(
                parameters,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        adapter = LocalAnalysisCLIAdapter(
            provider.tool,
            stage=provider.stage,
            version=provider.adapter_version,
            generator_id=(
                f"generator:analysis-{provider.stage.value}-"
                f"{provider.tool.identity.sha256[:8]}-"
                f"{provider.model.sha256[:8]}-{parameters_sha256[:8]}"
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
        )
        return LocalAnalysisStep(adapter, parameters)
