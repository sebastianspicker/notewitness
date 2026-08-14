"""Crash-resumable local CLI analysis with one atomic evidence publication."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Mapping

from notewitness.adapters.analysis_cli import (
    AnalysisCLICancelled,
    AnalysisCLIError,
    AnalysisCLIExecutionError,
    LocalAnalysisCLIAdapter,
)
from notewitness.application._resumable_analysis_artifacts import (
    MAX_RESUMABLE_CHUNKS_PER_STAGE,
    ResumableAnalysisArtifacts,
    ResumableAnalysisError,
    StageChunk,
    continuation_after,
    events_exist,
    next_continuation,
    read_json,
    read_private,
)
from notewitness.application._resumable_analysis_leases import LeaseRenewer
from notewitness.application.analysis_evidence import (
    AnalysisEvidenceContext,
    AnalysisEvidenceError,
    append_analysis_batches,
    require_unique_hypothesis_ids,
)
from notewitness.domain.analysis import (
    AnalysisBatch,
    AnalysisRequest,
    AnalysisStage,
    JobState,
)
from notewitness.domain.jobs import AnalysisJobSpec, DurableJob
from notewitness.infrastructure.sqlite_job_store import JobConflictError, SQLiteJobStore
from notewitness.local_artifacts import LocalArtifactError
from notewitness.project_store import ProjectStore


MAX_RESUMABLE_STAGES = 16
_StageChunk = StageChunk
_LeaseRenewer = LeaseRenewer
_read_private = read_private
_read_json = read_json
_events_exist = events_exist


@dataclass(frozen=True, slots=True)
class ResumableAnalysisStep:
    """One explicit CLI adapter and the bounded request parameters for its stage."""

    adapter: LocalAnalysisCLIAdapter
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.adapter, LocalAnalysisCLIAdapter):
            raise ValueError("Resumable analysis requires LocalAnalysisCLIAdapter instances.")
        if not isinstance(self.parameters, Mapping):
            raise ValueError("Resumable analysis parameters must be a mapping.")


class ResumableAnalysisCoordinator:
    """Execute one immutable job while retaining replayable raw stage output."""

    def __init__(
        self,
        store: SQLiteJobStore,
        project_root: Path,
        steps: tuple[ResumableAnalysisStep, ...],
        *,
        owner_id: str,
        lease_seconds: float,
        adapter_fingerprint_sha256: str,
        runtime_fingerprint_sha256: str,
        settings_fingerprint_sha256: str,
        model_sha256: str,
    ) -> None:
        if not isinstance(store, SQLiteJobStore):
            raise ValueError("store must be a SQLiteJobStore.")
        if not isinstance(project_root, Path):
            raise ValueError("project_root must be a Path.")
        if not steps or len(steps) > MAX_RESUMABLE_STAGES:
            raise ValueError(f"steps must contain 1-{MAX_RESUMABLE_STAGES} items.")
        if not isinstance(owner_id, str) or not owner_id or len(owner_id) > 256:
            raise ValueError("owner_id must be a bounded non-empty string.")
        if not isinstance(lease_seconds, (int, float)) or not 0 < lease_seconds <= 86_400:
            raise ValueError("lease_seconds must be finite and between 0 and 86400.")
        identities = (
            adapter_fingerprint_sha256,
            runtime_fingerprint_sha256,
            settings_fingerprint_sha256,
            model_sha256,
        )
        if any(not _is_sha256(value) for value in identities):
            raise ValueError("analysis identities must be lowercase SHA-256 digests.")
        stages = tuple(step.adapter.stage for step in steps)
        if len(stages) != len(set(stages)):
            raise ValueError("resumable analysis stages must be unique.")
        if any(step.adapter.settings.model.sha256 != model_sha256 for step in steps):
            raise ValueError("every analysis adapter must use the declared model identity.")
        executable_identities = {
            (step.adapter.tool.executable, step.adapter.tool.identity)
            for step in steps
        }
        if len(executable_identities) != 1:
            raise ValueError("every analysis stage must use the same executable identity.")
        self._store = store
        self._project = ProjectStore(project_root)
        self._runs_directory = self._project.ensure_private_directory("runs")
        self._artifacts = ResumableAnalysisArtifacts(self._runs_directory)
        self._steps = steps
        self._by_stage = {step.adapter.stage: step for step in steps}
        self._owner_id = owner_id
        self._lease_seconds = float(lease_seconds)
        self._adapter_fingerprint_sha256 = adapter_fingerprint_sha256
        self._runtime_fingerprint_sha256 = runtime_fingerprint_sha256
        self._settings_fingerprint_sha256 = settings_fingerprint_sha256
        self._model_sha256 = model_sha256

    def enqueue(self, spec: AnalysisJobSpec) -> DurableJob:
        """Create the durable job and its immutable private identity manifest."""
        self._validate_spec(spec)
        job = self._store.enqueue(spec)
        self._artifacts.ensure_identity(spec.job_id, self._identity_payload(spec))
        return job

    def run(self, job_id: str) -> DurableJob | None:
        """Claim, resume, and publish one job without re-running durable stages."""
        job = self._store.claim(
            job_id,
            owner_id=self._owner_id,
            lease_seconds=self._lease_seconds,
            source_sha256=self._source_sha256(job_id),
            adapter_fingerprint_sha256=self._adapter_fingerprint_sha256,
            runtime_fingerprint_sha256=self._runtime_fingerprint_sha256,
            settings_fingerprint_sha256=self._settings_fingerprint_sha256,
            score_sha256=self._score_sha256(job_id),
        )
        if job is None:
            return None
        try:
            self._verify_identity(job)
            chunks = self._completed_chunks(job)
            start = self._resume_stage_index(job)
            for index in range(start, len(job.spec.stages)):
                job = self._store.heartbeat(
                    job.spec.job_id,
                    owner_id=self._owner_id,
                    lease_seconds=self._lease_seconds,
                )
                if job.cancel_requested:
                    return self._store.complete(job.spec.job_id, owner_id=self._owner_id)
                stage = job.spec.stages[index]
                stage_chunks, job = self._resume_stage(job, stage)
                if job.state is JobState.CANCELLED:
                    return job
                current = self._store.get(job.spec.job_id)
                if current is not None and current.cancel_requested:
                    return self._store.complete(job.spec.job_id, owner_id=self._owner_id)
                chunks.extend(stage_chunks)
            if job.cancel_requested:
                return self._store.complete(job.spec.job_id, owner_id=self._owner_id)
            require_unique_hypothesis_ids(chunk.batch for chunk in chunks)
            self._publish_once(job, chunks)
            return self._store.complete(
                job.spec.job_id,
                owner_id=self._owner_id,
                last_artifact_id="artifact:analysis-publication",
            )
        except AnalysisCLICancelled:
            return self._store.complete(job_id, owner_id=self._owner_id)
        except (
            AnalysisCLIError,
            AnalysisEvidenceError,
            LocalArtifactError,
            OSError,
            ResumableAnalysisError,
            ValueError,
        ):
            return self._store.fail(
                job_id,
                owner_id=self._owner_id,
                reason="resumable_analysis_failed",
            )

    def _validate_spec(self, spec: AnalysisJobSpec) -> None:
        if not isinstance(spec, AnalysisJobSpec):
            raise ValueError("enqueue requires an AnalysisJobSpec.")
        if tuple(self._by_stage) != spec.stages:
            raise ValueError("job stages must exactly match configured analysis steps.")
        if spec.adapter_fingerprint_sha256 != self._adapter_fingerprint_sha256:
            raise JobConflictError("adapter fingerprint does not match coordinator.")
        if spec.runtime_fingerprint_sha256 != self._runtime_fingerprint_sha256:
            raise JobConflictError("runtime fingerprint does not match coordinator.")
        if spec.settings_fingerprint_sha256 != self._settings_fingerprint_sha256:
            raise JobConflictError("settings fingerprint does not match coordinator.")
        scores = {
            step.adapter.settings.score.sha256 if step.adapter.settings.score else None
            for step in self._steps
        }
        if scores != {spec.score_sha256}:
            raise JobConflictError("score identity does not match configured analysis steps.")
        if any(step.adapter.settings.media.sha256 != spec.source_sha256 for step in self._steps):
            raise JobConflictError("source identity does not match configured analysis steps.")

    def _execute_stage(
        self,
        job: DurableJob,
        stage: AnalysisStage,
        continuation_token: str | None,
    ) -> tuple[AnalysisBatch, bytes]:
        step = self._by_stage[stage]
        request = AnalysisRequest(
            job.spec.job_id,
            job.spec.source_id,
            job.spec.spans,
            step.parameters,
            continuation_token,
        )
        step.adapter.require_executable_identity()
        try:
            execution = step.adapter.execute(
                request,
                cancellation_requested=lambda: self._cancellation_requested(
                    job.spec.job_id
                ),
            )
        finally:
            step.adapter.require_executable_identity()
        return execution.batch, execution.raw_output

    def _cancellation_requested(self, job_id: str) -> bool:
        current = self._store.get(job_id)
        if current is None:
            raise ResumableAnalysisError("analysis job disappeared during execution")
        return current.cancel_requested

    def _replay_stage(
        self,
        job: DurableJob,
        stage: AnalysisStage,
        raw: bytes,
        continuation_token: str | None,
    ) -> AnalysisBatch:
        step = self._by_stage[stage]
        request = AnalysisRequest(
            job.spec.job_id,
            job.spec.source_id,
            job.spec.spans,
            step.parameters,
            continuation_token,
        )
        return step.adapter.replay(request, raw)

    def _execute_stage_with_lease(
        self,
        job: DurableJob,
        stage: AnalysisStage,
        continuation_token: str | None,
    ) -> tuple[AnalysisBatch, bytes]:
        """Keep an owned lease current while a bounded adapter call is in progress."""
        renewer = _LeaseRenewer(
            self._store,
            job.spec.job_id,
            self._owner_id,
            self._lease_seconds,
        )
        renewer.start()
        try:
            batch = self._execute_stage(job, stage, continuation_token)
        finally:
            renewer.stop()
        renewer.raise_if_lost()
        return batch

    def _resume_stage(
        self, job: DurableJob, stage: AnalysisStage
    ) -> tuple[list[_StageChunk], DurableJob]:
        """Replay persisted chunks, then execute bounded continuations to completion."""
        chunks = self._stage_chunks(job, stage)
        continuation = self._continuation_after(chunks)
        checkpoint_index = 0
        if job.checkpoint_stage is stage:
            checkpoint_index = self._checkpoint_chunk_index(job, stage, chunks)
            checkpoint_continuation = self._continuation_after(chunks[:checkpoint_index])
            if checkpoint_continuation != job.continuation_token:
                raise ResumableAnalysisError(
                    "checkpoint continuation does not match raw replay."
                )
        if chunks and checkpoint_index < len(chunks):
            job = self._checkpoint_chunk(job, chunks[-1], continuation)
        elif job.checkpoint_stage is stage and not chunks:
            raise ResumableAnalysisError("checkpointed stage has no durable raw response.")
        seen_tokens = {
            str(chunk.batch.result.continuation_token)
            for chunk in chunks
            if chunk.batch.result.continuation_token is not None
        }
        while continuation is not None or not chunks:
            if len(chunks) >= MAX_RESUMABLE_CHUNKS_PER_STAGE:
                raise ResumableAnalysisError(
                    "analysis stage exceeded its continuation chunk limit."
                )
            try:
                batch, raw = self._execute_stage_with_lease(job, stage, continuation)
            except AnalysisCLIExecutionError as exc:
                self._write_failed_raw(job.spec.job_id, stage, len(chunks) + 1, exc.raw_output)
                raise
            chunk_index = len(chunks) + 1
            path = self._write_raw(job.spec.job_id, stage, chunk_index, raw, batch)
            chunk = _StageChunk(stage, chunk_index, batch, path)
            chunks.append(chunk)
            next_token = self._next_continuation(batch, continuation, seen_tokens)
            job = self._checkpoint_chunk(job, chunk, next_token)
            current = self._store.get(job.spec.job_id)
            if current is not None and current.cancel_requested:
                return chunks, self._store.complete(job.spec.job_id, owner_id=self._owner_id)
            continuation = next_token
        return chunks, job

    @staticmethod
    def _next_continuation(
        batch: AnalysisBatch, previous: str | None, seen: set[str]
    ) -> str | None:
        return next_continuation(batch, previous, seen)

    def _resume_stage_index(self, job: DurableJob) -> int:
        if job.checkpoint_stage is None:
            return 0
        index = job.spec.stages.index(job.checkpoint_stage)
        return index if job.continuation_token is not None else index + 1

    def _completed_chunks(self, job: DurableJob) -> list[_StageChunk]:
        if job.checkpoint_stage is None:
            return []
        checkpoint_index = job.spec.stages.index(job.checkpoint_stage)
        end = checkpoint_index + (0 if job.continuation_token is not None else 1)
        chunks: list[_StageChunk] = []
        for stage in job.spec.stages[:end]:
            stage_chunks = self._stage_chunks(job, stage)
            if not stage_chunks:
                raise ResumableAnalysisError("completed stage has no durable raw response.")
            if self._continuation_after(stage_chunks) is not None:
                raise ResumableAnalysisError("completed stage remains incomplete on replay.")
            chunks.extend(stage_chunks)
        return chunks

    def _checkpoint_chunk(
        self,
        job: DurableJob,
        chunk: _StageChunk,
        continuation_token: str | None,
    ) -> DurableJob:
        return self._store.checkpoint(
            job.spec.job_id,
            owner_id=self._owner_id,
            stage=chunk.stage,
            completed_span_count=(
                0 if continuation_token is not None else len(job.spec.spans)
            ),
            continuation_token=continuation_token,
            last_artifact_id=self._raw_artifact_id(
                job.spec.job_id, chunk.stage, chunk.index, chunk.path
            ),
            pause=False,
        )

    def _checkpoint_chunk_index(
        self,
        job: DurableJob,
        stage: AnalysisStage,
        chunks: list[_StageChunk],
    ) -> int:
        return self._artifacts.checkpoint_chunk_index(job, stage, chunks)

    @staticmethod
    def _continuation_after(chunks: list[_StageChunk]) -> str | None:
        return continuation_after(chunks)

    def _publish_once(self, job: DurableJob, chunks: list[_StageChunk]) -> None:
        expected_events = tuple(
            f"event:analysis-{self._chunk_run_token(job.spec.job_id, chunk)}-{index}"
            for chunk in chunks
            for index, _ in enumerate(chunk.batch.hypotheses, start=1)
        )
        snapshot = self._project.load()
        if _events_exist(snapshot.payload, expected_events):
            return
        first_settings = self._steps[0].adapter.settings
        generator_parameters = {
            "adapter_fingerprint_sha256": self._adapter_fingerprint_sha256,
            "adapter_license": first_settings.adapter_license,
            "executable_sha256": self._steps[0].adapter.tool.identity.sha256,
            "executable_size_bytes": self._steps[0].adapter.tool.identity.size_bytes,
            "effective_stage_parameters": {
                step.adapter.stage.value: dict(step.parameters) for step in self._steps
            },
            "model_license": first_settings.model_license,
            "network_requirement": "none",
            "runtime_fingerprint_sha256": self._runtime_fingerprint_sha256,
            "score_license": first_settings.score_license,
            "settings_fingerprint_sha256": self._settings_fingerprint_sha256,
        }

        def publish(payload: dict[str, Any]) -> None:
            known = tuple(
                hypothesis.hypothesis_id
                for chunk in chunks
                for hypothesis in chunk.batch.hypotheses
                if hypothesis.__class__.__name__ in {
                    "NoteHypothesis",
                    "PitchPointHypothesis",
                }
            )
            for chunk in chunks:
                step = self._by_stage[chunk.stage]
                raw = _read_private(chunk.path)
                append_analysis_batches(
                    payload,
                    source_id=job.spec.source_id,
                    batches=(chunk.batch,),
                    context=AnalysisEvidenceContext(
                        run_token=self._chunk_run_token(job.spec.job_id, chunk),
                        generator_id=step.adapter.generator_id,
                        generator_name=step.adapter.name,
                        generator_version=step.adapter.version,
                        model_name=step.adapter.settings.model.source_id,
                        weight_hash_state=f"sha256:{self._model_sha256}",
                        raw_artifact_id=self._raw_artifact_id(
                            job.spec.job_id, chunk.stage, chunk.index, chunk.path
                        ),
                        raw_artifact_sha256=hashlib.sha256(raw).hexdigest(),
                        raw_artifact_size_bytes=len(raw),
                        parameters=generator_parameters,
                        analysis_run_id=job.spec.job_id,
                    ),
                    known_note_or_pitch_ids=known,
                )

        self._project.mutate(publish, expected_sha256=snapshot.sha256)

    def _verify_identity(self, job: DurableJob) -> None:
        self._validate_spec(job.spec)
        if self._artifacts.identity(job.spec.job_id) != self._identity_payload(job.spec):
            raise JobConflictError("persisted model or job identity changed; refusing resume")

    def _identity_payload(self, spec: AnalysisJobSpec) -> dict[str, object]:
        first = self._steps[0].adapter.settings
        return {
            "adapter_fingerprint_sha256": spec.adapter_fingerprint_sha256,
            "created_at": spec.created_at,
            "executable": {
                "changed_ns": self._steps[0].adapter.tool.identity.changed_ns,
                "device": self._steps[0].adapter.tool.identity.device,
                "inode": self._steps[0].adapter.tool.identity.inode,
                "mode": self._steps[0].adapter.tool.identity.mode,
                "modified_ns": self._steps[0].adapter.tool.identity.modified_ns,
                "owner_uid": self._steps[0].adapter.tool.identity.owner_uid,
                "sha256": self._steps[0].adapter.tool.identity.sha256,
                "size_bytes": self._steps[0].adapter.tool.identity.size_bytes,
            },
            "job_id": spec.job_id,
            "model": {
                "license": first.model_license,
                "sha256": self._model_sha256,
                "size_bytes": first.model.size_bytes,
                "source_id": first.model.source_id,
            },
            "network_requirement": "none",
            "runtime_fingerprint_sha256": spec.runtime_fingerprint_sha256,
            "score": (
                {
                    "license": first.score_license,
                    "sha256": first.score.sha256,
                    "size_bytes": first.score.size_bytes,
                    "source_id": first.score.source_id,
                }
                if first.score is not None
                else None
            ),
            "score_sha256": spec.score_sha256,
            "settings_fingerprint_sha256": spec.settings_fingerprint_sha256,
            "source_id": spec.source_id,
            "source_sha256": spec.source_sha256,
            "spans": [
                {
                    "duration_us": span.duration_us,
                    "start_us": span.start_us,
                    "stream_id": span.stream_id,
                }
                for span in spec.spans
            ],
            "stages": [
                {
                    "adapter_license": step.adapter.settings.adapter_license,
                    "adapter_name": step.adapter.name,
                    "adapter_version": step.adapter.version,
                    "generator_id": step.adapter.generator_id,
                    "parameters": dict(step.parameters),
                    "stage": step.adapter.stage.value,
                    "timeout_seconds": step.adapter.settings.timeout_seconds,
                }
                for step in self._steps
            ],
        }

    def _run_directory(self, job_id: str) -> Path:
        return self._artifacts.run_directory(job_id)

    def _raw_path(self, job_id: str, stage: AnalysisStage) -> Path:
        return self._artifacts.raw_path(job_id, stage)

    def _chunk_path(self, job_id: str, stage: AnalysisStage, index: int) -> Path:
        return self._artifacts.chunk_path(job_id, stage, index)

    def _stage_chunks(self, job: DurableJob, stage: AnalysisStage) -> list[_StageChunk]:
        return self._artifacts.stage_chunks(job, stage, self._replay_stage)

    def _write_raw(
        self,
        job_id: str,
        stage: AnalysisStage,
        index: int,
        raw: bytes,
        batch: AnalysisBatch,
    ) -> Path:
        return self._artifacts.write_raw(job_id, stage, index, raw, batch)

    def _write_failed_raw(
        self, job_id: str, stage: AnalysisStage, index: int, raw: bytes
    ) -> None:
        self._artifacts.write_failed_raw(job_id, stage, index, raw)

    def _raw_artifact_id(
        self, job_id: str, stage: AnalysisStage, index: int, path: Path
    ) -> str:
        return self._artifacts.raw_artifact_id(job_id, stage, index, path)

    def _chunk_run_token(self, job_id: str, chunk: _StageChunk) -> str:
        return self._artifacts.chunk_run_token(job_id, chunk)

    @staticmethod
    def _token(job_id: str) -> str:
        return ResumableAnalysisArtifacts.token(job_id)

    def _source_sha256(self, job_id: str) -> str:
        job = self._store.get(job_id)
        if job is None:
            raise ResumableAnalysisError("job does not exist")
        return job.spec.source_sha256

    def _score_sha256(self, job_id: str) -> str | None:
        job = self._store.get(job_id)
        if job is None:
            raise ResumableAnalysisError("job does not exist")
        return job.spec.score_sha256


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(item in "0123456789abcdef" for item in value)
    )
