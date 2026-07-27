"""Bounded, fail-loud coordination for dependency-injected local stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from notewitness.application.ports import AnalysisPort
from notewitness.domain.analysis import (
    AnalysisBatch,
    AnalysisRequest,
    AnalysisResult,
    AnalysisStage,
    AnalysisState,
)
from notewitness.domain.timeline import MediaSpan


MAX_PIPELINE_STEPS = 32


class CapabilityUnavailable(RuntimeError):
    def __init__(self, stage: AnalysisStage) -> None:
        self.stage = stage
        super().__init__(
            f"No local adapter is configured for analysis stage {stage.value!r}."
        )


class InvalidAdapterResult(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PipelineStep:
    stage: AnalysisStage
    request: AnalysisRequest


@dataclass(frozen=True, slots=True)
class PipelineRun:
    batches: tuple[AnalysisBatch, ...]

    @property
    def results(self) -> tuple[AnalysisResult, ...]:
        return tuple(batch.result for batch in self.batches)

    @property
    def completed(self) -> bool:
        return bool(self.batches) and all(
            batch.result.state is AnalysisState.READY for batch in self.batches
        )


class LocalAnalysisPipeline:
    """Run only bounded local ports; invoke this service from a worker process."""

    def __init__(self, adapters: Iterable[AnalysisPort] = ()) -> None:
        by_stage: dict[AnalysisStage, AnalysisPort] = {}
        for adapter in adapters:
            if adapter.stage in by_stage:
                raise ValueError(f"Duplicate adapter for stage {adapter.stage.value!r}.")
            by_stage[adapter.stage] = adapter
        self._adapters: Mapping[AnalysisStage, AnalysisPort] = by_stage

    @property
    def available_stages(self) -> tuple[AnalysisStage, ...]:
        return tuple(sorted(self._adapters, key=lambda stage: stage.value))

    def run(self, steps: Iterable[PipelineStep]) -> PipelineRun:
        planned = tuple(steps)
        if not planned:
            raise ValueError("A pipeline run requires at least one step.")
        if len(planned) > MAX_PIPELINE_STEPS:
            raise ValueError(f"A pipeline run is limited to {MAX_PIPELINE_STEPS} steps.")

        batches: list[AnalysisBatch] = []
        for step in planned:
            adapter = self._adapters.get(step.stage)
            if adapter is None:
                raise CapabilityUnavailable(step.stage)
            batch = adapter.analyze(step.request)
            result = batch.result
            if result.stage is not step.stage:
                raise InvalidAdapterResult(
                    f"Adapter for {step.stage.value!r} returned {result.stage.value!r}."
                )
            self._validate_batch(step, adapter, batch)
            batches.append(batch)
            if result.state is not AnalysisState.READY:
                break
        return PipelineRun(tuple(batches))

    @staticmethod
    def _validate_batch(
        step: PipelineStep, adapter: AnalysisPort, batch: AnalysisBatch
    ) -> None:
        for hypothesis in batch.hypotheses:
            if hypothesis.generator_id != adapter.generator_id:
                raise InvalidAdapterResult(
                    "Adapter returned a hypothesis with different generator provenance."
                )
            if hypothesis.span.source_id != step.request.source_id:
                raise InvalidAdapterResult(
                    "Adapter returned a hypothesis for a different source."
                )
            if not any(
                _span_contains(request_span, hypothesis.span)
                for request_span in step.request.spans
            ):
                raise InvalidAdapterResult(
                    "Adapter returned a hypothesis outside the requested spans."
                )


def _span_contains(container: object, candidate: object) -> bool:
    if not isinstance(container, MediaSpan) or not isinstance(candidate, MediaSpan):
        return False
    return bool(
        container.source_id == candidate.source_id
        and container.stream_id == candidate.stream_id
        and candidate.start_us >= container.start_us
        and candidate.end_us <= container.end_us
    )
