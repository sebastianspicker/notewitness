"""Startup-approved orchestration, run identity, and recovery for local workbench processing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable, Mapping

from notewitness.analysis.suite.adapter import (
    LocalAnalysisCLIAdapter,
    LocalAnalysisCLISettings,
    LocalAnalysisSource,
)
from notewitness.analysis.media_probe import FFprobeMediaProbe
from notewitness.analysis.transcription.whisper_cli import WhisperCLIAdapter
from notewitness.analysis.suite.jobs import analysis_generator_id
from notewitness.analysis.suite.runtime import (
    LocalAnalysisRunRequest,
    LocalAnalysisRuntime,
    LocalAnalysisStep,
)
from notewitness.lessons.pedagogical_digest import suggest_practice_relations
from notewitness.analysis.speaker_alignment import align_speech_to_anonymous_speakers
from notewitness.analysis.transcription.runtime import (
    LocalTranscriptionRequest,
    LocalTranscriptionRuntime,
)
from notewitness.analysis.runs.integration import integrate_completed_run
from notewitness.analysis.runs.publication import PUBLICATION_FILENAME
from notewitness.core.analysis.analysis import AnalysisStage
from notewitness.core.time import MediaSpan
from notewitness.projects.store import ProjectStore

from .jobs import (
    WorkbenchJobKind,
    WorkbenchProcessingError,
)
from .runtime_config import (
    AnalysisProfile,
    AnalysisProviderProfile,
    TranscriptionProfile,
    WorkbenchRuntimeConfigurationError,
    analysis_parameters,
    analysis_profile,
    exact_keys,
    read_private_configuration,
    require_object,
    stable_file_identity,
    transcription_profile,
)
from .snapshot import resolve_media_source


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
        transcription: TranscriptionProfile | None,
        analysis: AnalysisProfile | None,
    ) -> None:
        self.project_root = ProjectStore(project_root).root
        self.transcription = transcription
        self.analysis = analysis

    @classmethod
    def from_private_config(
        cls, project_root: str | Path, config_path: str | Path
    ) -> "LocalWorkbenchExecutor":
        payload = read_private_configuration(Path(config_path))
        exact_keys(payload, {"version", "transcription", "analysis"}, {"version"})
        version = payload.get("version")
        if version not in {1, 2}:
            raise WorkbenchRuntimeConfigurationError(
                "runtime_config_version_unsupported"
            )
        transcription_raw = payload.get("transcription")
        analysis_raw = payload.get("analysis")
        transcription = (
            transcription_profile(require_object(transcription_raw, "transcription"))
            if transcription_raw is not None
            else None
        )
        analysis = (
            analysis_profile(
                require_object(analysis_raw, "analysis"),
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
        media_sha256, media_size = stable_file_identity(media_path)
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
        profile: AnalysisProfile,
        provider: AnalysisProviderProfile,
        media: LocalAnalysisSource,
    ) -> LocalAnalysisStep:
        parameters = analysis_parameters(profile, provider)
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
            generator_id=analysis_generator_id(
                provider.stage,
                provider.tool.identity.sha256,
                provider.model.sha256,
                parameters_sha256,
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


def _workbench_run_token(job_id: str, step: str, attempt: int) -> str:
    if not _is_workbench_job_id(job_id):
        raise WorkbenchRuntimeConfigurationError("workbench_run_identity_invalid")
    if not _is_workbench_step(step):
        raise WorkbenchRuntimeConfigurationError("workbench_run_identity_invalid")
    if not _is_workbench_attempt_type(attempt):
        raise WorkbenchRuntimeConfigurationError("workbench_run_identity_invalid")
    if not _is_workbench_attempt_in_range(attempt):
        raise WorkbenchRuntimeConfigurationError("workbench_run_identity_invalid")
    material = f"notewitness-workbench-v1\0{job_id}\0{step}\0{attempt}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _is_workbench_job_id(value: object) -> bool:
    return isinstance(value, str) and value.startswith("job:workbench-")


def _is_workbench_step(value: object) -> bool:
    return value in {"transcription", "analysis"}


def _is_workbench_attempt_type(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_workbench_attempt_in_range(value: int) -> bool:
    return 1 <= value <= 100


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
        publication_path = project_root / "runs" / directory_name / PUBLICATION_FILENAME
        if not publication_path.exists() and not publication_path.is_symlink():
            continue
        integrate_completed_run(project_root, run_id)
        return True
    return False


def _require_checkpoint_identity(profile: TranscriptionProfile) -> None:
    current_sha256, current_size = stable_file_identity(
        profile.settings.model_checkpoint
    )
    if (
        current_sha256 != profile.checkpoint_sha256
        or current_size != profile.checkpoint_size_bytes
    ):
        raise WorkbenchRuntimeConfigurationError(
            "configured_transcription_checkpoint_changed"
        )
