"""Registry-owned noScribe research-profile conformance probes.

The probe suite exercises a checksum-pinned synthetic fixture contract. It
checks adapter behavior and isolation invariants; it is not a substitute for a
corpus benchmark of transcription or diarization accuracy.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from notewitness.application.ports import AnalysisPort
from notewitness.domain.analysis import (
    AnalysisBatch,
    AnalysisRequest,
    AnalysisStage,
    AnalysisState,
    SpeakerSegmentHypothesis,
    SpeechSegmentHypothesis,
    WordHypothesis,
)
from notewitness.domain.timeline import MediaSpan
from notewitness.network import deny_outbound_sockets


NOSCRIBE_ASR_FEATURES = frozenset(
    {
        "auto_language",
        "disfluency_policy",
        "fixed_language",
        "model_profiles",
        "multilingual_language",
        "segment_timestamps",
        "word_confidence",
        "word_timestamps",
    }
)
NOSCRIBE_DIARIZATION_FEATURES = frozenset(
    {
        "anonymous_clusters",
        "auto_speaker_count",
        "diarization_off",
        "exact_speaker_count_1_10",
        "overlap_detection",
    }
)
NOSCRIBE_PROBE_INVARIANTS = frozenset(
    {
        "bounded_output",
        "offline_runtime",
        "source_time_preserved",
        "typed_output",
    }
)
NOSCRIBE_PROBE_PROFILE = "noscribe-research-v0.1"
NOSCRIBE_PROBE_VERSION = "1"
NOSCRIBE_FIXTURE_SHA256 = (
    "1bd5cd0b11202242ecc2920951df1d4680ce977b70396ed1ec299bc6205ee4c0"
)

_FIXTURE_SPEC = (
    b'{"duration_us":2000000,"id":"noscribe-conformance-v1",'
    b'"languages":["en","de"],"speech":"uh legato bitte",'
    b'"speaker_clusters":10,"version":1}'
)
_PROBE_SOURCE_ID = f"source:noscribe-probe:{NOSCRIBE_FIXTURE_SHA256}"
_PROBE_SPAN = MediaSpan(_PROBE_SOURCE_ID, "audio", 0, 2_000_000)
_MAX_PROBE_HYPOTHESES = 64

if hashlib.sha256(_FIXTURE_SPEC).hexdigest() != NOSCRIBE_FIXTURE_SHA256:
    raise RuntimeError("The embedded noScribe conformance fixture was modified.")


class NoscribeConformanceError(ValueError):
    """The adapter failed an observed research-profile contract."""


@dataclass(frozen=True, slots=True)
class AdapterConformanceResult:
    profile_id: str
    probe_version: str
    adapter_id: str
    stage: AnalysisStage
    fixture_sha256: str
    passed_features: tuple[str, ...]
    observed_invariants: tuple[str, ...]
    passed: bool


def required_features(stage: AnalysisStage) -> frozenset[str]:
    return {
        AnalysisStage.SPEECH_RECOGNITION: NOSCRIBE_ASR_FEATURES,
        AnalysisStage.ANONYMOUS_DIARIZATION: NOSCRIBE_DIARIZATION_FEATURES,
    }.get(stage, frozenset())


def run_noscribe_conformance(adapter: AnalysisPort) -> AdapterConformanceResult:
    """Run the fixed suite and derive every granted feature from observations."""

    with deny_outbound_sockets() as socket_probe:
        if adapter.stage is AnalysisStage.SPEECH_RECOGNITION:
            features = _probe_asr(adapter)
        elif adapter.stage is AnalysisStage.ANONYMOUS_DIARIZATION:
            features = _probe_diarization(adapter)
        else:
            raise NoscribeConformanceError(
                "The noScribe probe supports only ASR and diarization adapters."
            )
    if socket_probe.attempted_operations:
        raise NoscribeConformanceError(
            "The adapter attempted outbound networking during its local probe."
        )
    return AdapterConformanceResult(
        profile_id=NOSCRIBE_PROBE_PROFILE,
        probe_version=NOSCRIBE_PROBE_VERSION,
        adapter_id=adapter.generator_id,
        stage=adapter.stage,
        fixture_sha256=NOSCRIBE_FIXTURE_SHA256,
        passed_features=tuple(sorted(features)),
        observed_invariants=tuple(sorted(NOSCRIBE_PROBE_INVARIANTS)),
        passed=True,
    )


def _probe_asr(adapter: AnalysisPort) -> frozenset[str]:
    fixed = _run_case(
        adapter,
        "asr-fixed-include-precise",
        {
            "language_mode": "fixed",
            "requested_language": "en",
            "disfluency_policy": "include",
            "model_profile_id": "precise",
        },
    )
    automatic = _run_case(
        adapter,
        "asr-auto",
        {"language_mode": "auto", "model_profile_id": "precise"},
    )
    multilingual = _run_case(
        adapter,
        "asr-multilingual",
        {"language_mode": "multilingual", "model_profile_id": "precise"},
    )
    excluded = _run_case(
        adapter,
        "asr-suppress-disfluencies",
        {
            "language_mode": "fixed",
            "requested_language": "en",
            "disfluency_policy": "suppress",
            "model_profile_id": "precise",
        },
    )
    fast = _run_case(
        adapter,
        "asr-fixed-fast",
        {
            "language_mode": "fixed",
            "requested_language": "en",
            "disfluency_policy": "include",
            "model_profile_id": "fast",
        },
    )
    batches = (fixed, automatic, multilingual, excluded, fast)
    for batch in batches:
        _require_ready_asr(batch)

    fixed_words = _words(fixed)
    if {word.language for word in fixed_words} != {"en"}:
        raise NoscribeConformanceError("Fixed-language ASR did not preserve English.")
    automatic_languages = {word.language for word in _words(automatic)}
    if None in automatic_languages or not automatic_languages:
        raise NoscribeConformanceError("Automatic ASR did not resolve a language.")
    multilingual_languages = {word.language for word in _words(multilingual)}
    if not {"en", "de"}.issubset(multilingual_languages):
        raise NoscribeConformanceError(
            "Multilingual ASR did not preserve both fixture languages."
        )
    included_text = " ".join(word.text or "" for word in fixed_words).casefold()
    excluded_text = " ".join(word.text or "" for word in _words(excluded)).casefold()
    if "uh" not in included_text.split() or "uh" in excluded_text.split():
        raise NoscribeConformanceError(
            "Disfluency include/exclude behavior was not observed."
        )
    if fixed.result.hypothesis_ids == fast.result.hypothesis_ids:
        raise NoscribeConformanceError(
            "Distinct model-profile probes returned the same evidence identity."
        )
    if any(word.confidence is None for batch in batches for word in _words(batch)):
        raise NoscribeConformanceError("ASR words did not retain confidence values.")
    return NOSCRIBE_ASR_FEATURES


def _probe_diarization(adapter: AnalysisPort) -> frozenset[str]:
    disabled = _run_case(adapter, "diarization-off", {"diarization_mode": "off"})
    if (
        disabled.result.state is not AnalysisState.NOT_APPLICABLE
        or disabled.hypotheses
    ):
        raise NoscribeConformanceError("Disabled diarization still emitted speakers.")

    automatic = _run_case(
        adapter,
        "diarization-auto-overlap",
        {"diarization_mode": "auto", "detect_overlap": True},
    )
    automatic_speakers = _require_ready_diarization(automatic)
    if len({item.anonymous_cluster_id for item in automatic_speakers}) < 2:
        raise NoscribeConformanceError("Automatic diarization found fewer than two clusters.")
    if not _contains_overlap(automatic_speakers):
        raise NoscribeConformanceError("Diarization did not preserve fixture overlap.")

    for count in range(1, 11):
        batch = _run_case(
            adapter,
            f"diarization-exact-{count}",
            {"diarization_mode": "exact", "exact_speaker_count": count},
        )
        speakers = _require_ready_diarization(batch)
        cluster_ids = {item.anonymous_cluster_id for item in speakers}
        if len(cluster_ids) != count:
            raise NoscribeConformanceError(
                f"Exact diarization requested {count} clusters but observed "
                f"{len(cluster_ids)}."
            )
    return NOSCRIBE_DIARIZATION_FEATURES


def _run_case(
    adapter: AnalysisPort,
    case_id: str,
    parameters: dict[str, Any],
) -> AnalysisBatch:
    request = AnalysisRequest(
        job_id=f"job:noscribe-probe:{case_id}",
        source_id=_PROBE_SOURCE_ID,
        spans=(_PROBE_SPAN,),
        parameters={
            **parameters,
            "fixture_sha256": NOSCRIBE_FIXTURE_SHA256,
            "probe_version": NOSCRIBE_PROBE_VERSION,
        },
    )
    batch = adapter.analyze(request)
    if not isinstance(batch, AnalysisBatch):
        raise NoscribeConformanceError("Adapter output is not a typed AnalysisBatch.")
    if batch.result.stage is not adapter.stage:
        raise NoscribeConformanceError("Adapter output stage does not match the probe.")
    if len(batch.hypotheses) > _MAX_PROBE_HYPOTHESES:
        raise NoscribeConformanceError("Adapter output exceeded the probe bound.")
    for hypothesis in batch.hypotheses:
        if hypothesis.generator_id != adapter.generator_id:
            raise NoscribeConformanceError("Probe output lost adapter provenance.")
        if not _span_contains(request.spans[0], hypothesis.span):
            raise NoscribeConformanceError("Probe output left the requested source time.")
    return batch


def _require_ready_asr(batch: AnalysisBatch) -> None:
    if batch.result.state is not AnalysisState.READY:
        raise NoscribeConformanceError("ASR conformance cases must complete.")
    words = _words(batch)
    segments = tuple(
        item for item in batch.hypotheses if isinstance(item, SpeechSegmentHypothesis)
    )
    if not words or not segments:
        raise NoscribeConformanceError("ASR must return typed words and segments.")
    word_ids = {word.hypothesis_id for word in words}
    if any(
        not segment.word_hypothesis_ids
        or not set(segment.word_hypothesis_ids).issubset(word_ids)
        for segment in segments
    ):
        raise NoscribeConformanceError("ASR segment-to-word links are incomplete.")


def _require_ready_diarization(
    batch: AnalysisBatch,
) -> tuple[SpeakerSegmentHypothesis, ...]:
    if batch.result.state is not AnalysisState.READY:
        raise NoscribeConformanceError("Diarization conformance cases must complete.")
    speakers = tuple(
        item for item in batch.hypotheses if isinstance(item, SpeakerSegmentHypothesis)
    )
    if not speakers or len(speakers) != len(batch.hypotheses):
        raise NoscribeConformanceError("Diarization output is not typed speaker evidence.")
    if any(
        not item.anonymous_cluster_id or item.confirmed_actor_id is not None
        for item in speakers
    ):
        raise NoscribeConformanceError("Diarization exposed non-anonymous identities.")
    return speakers


def _words(batch: AnalysisBatch) -> tuple[WordHypothesis, ...]:
    return tuple(item for item in batch.hypotheses if isinstance(item, WordHypothesis))


def _span_contains(container: MediaSpan, candidate: MediaSpan) -> bool:
    return bool(
        candidate.source_id == container.source_id
        and candidate.stream_id == container.stream_id
        and candidate.start_us >= container.start_us
        and candidate.end_us <= container.end_us
    )


def _contains_overlap(speakers: tuple[SpeakerSegmentHypothesis, ...]) -> bool:
    return any(
        first.anonymous_cluster_id != second.anonymous_cluster_id
        and first.span.source_id == second.span.source_id
        and first.span.stream_id == second.span.stream_id
        and first.span.start_us < second.span.end_us
        and second.span.start_us < first.span.end_us
        for index, first in enumerate(speakers)
        for second in speakers[index + 1 :]
    )
