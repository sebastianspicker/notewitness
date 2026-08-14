"""Owner-private raw artifacts and replay checks for resumable analysis."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Callable, Mapping

from notewitness.domain.analysis import AnalysisBatch, AnalysisStage, AnalysisState
from notewitness.domain.jobs import DurableJob
from notewitness.local_artifacts import write_new_private_bytes, write_new_private_json


MAX_RESUMABLE_CHUNKS_PER_STAGE = 64
_PUBLISHABLE_STATES = frozenset(
    {
        AnalysisState.READY,
        AnalysisState.UNKNOWN,
        AnalysisState.NOT_DETECTED,
        AnalysisState.NOT_APPLICABLE,
        AnalysisState.NOT_ALIGNABLE,
        AnalysisState.UNCERTAIN,
    }
)


class ResumableAnalysisError(RuntimeError):
    """A durable local analysis job could not safely continue."""


@dataclass(frozen=True, slots=True)
class StageChunk:
    """One durable raw adapter response and its replayed normalized batch."""

    stage: AnalysisStage
    index: int
    batch: AnalysisBatch
    path: Path


ReplayStage = Callable[[DurableJob, AnalysisStage, bytes, str | None], AnalysisBatch]


class ResumableAnalysisArtifacts:
    """Keep raw response layout, validation, and identity checks in one place."""

    def __init__(self, runs_directory: Path) -> None:
        self._runs_directory = runs_directory

    def ensure_identity(self, job_id: str, payload: dict[str, object]) -> None:
        directory = self.run_directory(job_id)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        manifest = directory / "identity.json"
        if manifest.exists():
            if read_json(manifest) != payload:
                raise ResumableAnalysisError("job run directory has a different identity.")
        else:
            write_new_private_json(manifest, payload)

    def identity(self, job_id: str) -> dict[str, object]:
        return read_json(self.run_directory(job_id) / "identity.json")

    def run_directory(self, job_id: str) -> Path:
        return self._runs_directory / f"resumable-{self.token(job_id)}"

    def raw_path(self, job_id: str, stage: AnalysisStage) -> Path:
        return self.run_directory(job_id) / f"{stage.value}.raw.json"

    def chunk_path(self, job_id: str, stage: AnalysisStage, index: int) -> Path:
        return self.run_directory(job_id) / f"{stage.value}.chunk-{index:03d}.raw.json"

    def stage_chunks(
        self,
        job: DurableJob,
        stage: AnalysisStage,
        replay: ReplayStage,
    ) -> list[StageChunk]:
        paths = self._stage_paths(job.spec.job_id, stage)
        if not paths:
            return []
        chunks: list[StageChunk] = []
        continuation: str | None = None
        seen_tokens: set[str] = set()
        for index, path in enumerate(paths, start=1):
            if index > 1 and continuation is None:
                raise ResumableAnalysisError(
                    "durable analysis has a raw response after a terminal chunk."
                )
            batch = replay(job, stage, read_private(path), continuation)
            chunks.append(StageChunk(stage, index, batch, path))
            continuation = next_continuation(batch, continuation, seen_tokens)
        return chunks

    def write_raw(
        self,
        job_id: str,
        stage: AnalysisStage,
        index: int,
        raw: bytes,
        batch: AnalysisBatch,
    ) -> Path:
        path = (
            self.raw_path(job_id, stage)
            if index == 1 and batch.result.state is not AnalysisState.INCOMPLETE
            else self.chunk_path(job_id, stage, index)
        )
        self._write_once(path, raw, "stage raw output")
        return path

    def write_failed_raw(
        self, job_id: str, stage: AnalysisStage, index: int, raw: bytes
    ) -> None:
        path = self.run_directory(job_id) / f"{stage.value}.failure-{index:03d}.raw.json"
        self._write_once(path, raw, "failed raw output")

    def raw_artifact_id(
        self, job_id: str, stage: AnalysisStage, index: int, path: Path
    ) -> str:
        suffix = "" if path == self.raw_path(job_id, stage) else f"-chunk-{index:03d}"
        return f"artifact:analysis-{self.token(job_id)}-{stage.value}{suffix}"

    def chunk_run_token(self, job_id: str, chunk: StageChunk) -> str:
        suffix = (
            ""
            if chunk.path == self.raw_path(job_id, chunk.stage)
            else f"-chunk-{chunk.index:03d}"
        )
        return f"{self.token(job_id)}-{chunk.stage.value}{suffix}"

    def checkpoint_chunk_index(
        self, job: DurableJob, stage: AnalysisStage, chunks: list[StageChunk]
    ) -> int:
        matches = [
            chunk.index
            for chunk in chunks
            if self.raw_artifact_id(job.spec.job_id, stage, chunk.index, chunk.path)
            == job.last_artifact_id
        ]
        if len(matches) != 1:
            raise ResumableAnalysisError(
                "checkpoint does not identify one durable raw response."
            )
        return matches[0]

    def _stage_paths(self, job_id: str, stage: AnalysisStage) -> list[Path]:
        directory = self.run_directory(job_id)
        paths = sorted(directory.glob(f"{stage.value}.chunk-*.raw.json"))
        legacy = self.raw_path(job_id, stage)
        if paths and legacy.exists():
            raise ResumableAnalysisError(
                "durable analysis stage mixes legacy and chunked raw responses."
            )
        if not paths and legacy.exists():
            paths = [legacy]
        if len(paths) > MAX_RESUMABLE_CHUNKS_PER_STAGE:
            raise ResumableAnalysisError(
                "analysis stage exceeded its continuation chunk limit."
            )
        if paths and paths != [legacy]:
            expected = [
                self.chunk_path(job_id, stage, index)
                for index in range(1, len(paths) + 1)
            ]
            if paths != expected:
                raise ResumableAnalysisError(
                    "durable analysis chunk identities are not contiguous."
                )
        return paths

    @staticmethod
    def _write_once(path: Path, raw: bytes, label: str) -> None:
        if path.exists():
            if read_private(path) != raw:
                raise ResumableAnalysisError(
                    f"{label} conflicts with durable recovery data."
                )
            return
        write_new_private_bytes(path, raw)

    @staticmethod
    def token(job_id: str) -> str:
        return hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:32]


def next_continuation(
    batch: AnalysisBatch, previous: str | None, seen: set[str]
) -> str | None:
    if batch.result.state is AnalysisState.INCOMPLETE:
        token = batch.result.continuation_token
        if token is None or token == previous or token in seen:
            raise ResumableAnalysisError("analysis continuation did not make progress.")
        seen.add(token)
        return token
    if batch.result.state not in _PUBLISHABLE_STATES:
        raise ResumableAnalysisError(f"analysis stage ended in {batch.result.state.value}.")
    return None


def continuation_after(chunks: list[StageChunk]) -> str | None:
    if not chunks or chunks[-1].batch.result.state is not AnalysisState.INCOMPLETE:
        return None
    return chunks[-1].batch.result.continuation_token


def read_private(path: Path) -> bytes:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise ResumableAnalysisError("durable analysis artifact is not owner-private.")
    return path.read_bytes()


def read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(read_private(path))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ResumableAnalysisError("durable analysis manifest is invalid.") from exc
    if not isinstance(value, dict):
        raise ResumableAnalysisError("durable analysis manifest is invalid.")
    return value


def events_exist(payload: Mapping[str, object], expected: tuple[str, ...]) -> bool:
    events = payload.get("events")
    if not isinstance(events, list):
        raise ResumableAnalysisError("project event collection is invalid.")
    identifiers = {item.get("id") for item in events if isinstance(item, dict)}
    return bool(expected) and set(expected).issubset(identifiers)
