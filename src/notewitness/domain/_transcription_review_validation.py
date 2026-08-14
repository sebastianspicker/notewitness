"""Private validation rules for immutable speaker-review records."""

from __future__ import annotations

from typing import Any

from notewitness.domain.timeline import MediaSpan
from notewitness.domain.transcription_shared import _validate_timestamp


def validate_speaker_result_span_assignment(assignment: Any) -> None:
    if not assignment.result_cluster_id:
        raise ValueError("Speaker result-span assignments require a result cluster ID.")
    if not assignment.spans:
        raise ValueError("Speaker result-span assignments require at least one span.")
    if any(not isinstance(span, MediaSpan) for span in assignment.spans):
        raise ValueError("Speaker result-span assignments must contain MediaSpan values.")
    if len(assignment.spans) != len(set(assignment.spans)):
        raise ValueError("Speaker result-span assignment spans must be unique.")


def validate_speaker_correction(correction: Any, assignment_type: type[Any]) -> None:
    _validate_speaker_correction_basics(correction, assignment_type)
    _validate_speaker_correction_assignments(correction)
    _validate_speaker_correction_kind(correction)


def _validate_speaker_correction_basics(
    correction: Any, assignment_type: type[Any]
) -> None:
    _validate_speaker_correction_identity(correction)
    _validate_speaker_correction_parents(correction)
    _validate_speaker_correction_clusters(correction)
    _validate_speaker_correction_spans(correction)
    _validate_speaker_correction_assignment_types(correction, assignment_type)


def _validate_speaker_correction_identity(correction: Any) -> None:
    if not correction.correction_id or not correction.cluster_ids or not correction.author_id:
        raise ValueError("Speaker corrections require identity, clusters, and author.")
    if not correction.reason:
        raise ValueError("Speaker corrections require a reason.")
    _validate_timestamp(correction.created_at, "created_at", required=True)


def _validate_speaker_correction_parents(correction: Any) -> None:
    if not correction.parent_revision_ids:
        raise ValueError("Speaker corrections require a parent revision.")
    if len(correction.parent_revision_ids) != len(set(correction.parent_revision_ids)):
        raise ValueError("Speaker correction parent revisions must be unique.")


def _validate_speaker_correction_clusters(correction: Any) -> None:
    if len(correction.cluster_ids) != len(set(correction.cluster_ids)):
        raise ValueError("Speaker correction cluster IDs must be unique.")
    if not correction.result_cluster_ids or any(
        not cluster_id for cluster_id in correction.result_cluster_ids
    ):
        raise ValueError("Speaker corrections require result cluster IDs.")
    if len(correction.result_cluster_ids) != len(set(correction.result_cluster_ids)):
        raise ValueError("Speaker result cluster IDs must be unique.")


def _validate_speaker_correction_spans(correction: Any) -> None:
    if any(not isinstance(span, MediaSpan) for span in correction.spans):
        raise ValueError("Speaker correction spans must contain MediaSpan values.")
    if len(correction.spans) != len(set(correction.spans)):
        raise ValueError("Speaker correction spans must be unique.")


def _validate_speaker_correction_assignment_types(
    correction: Any, assignment_type: type[Any]
) -> None:
    if not correction.result_span_assignments:
        raise ValueError("Speaker corrections require explicit result-to-span assignments.")
    if any(
        not isinstance(assignment, assignment_type)
        for assignment in correction.result_span_assignments
    ):
        raise ValueError(
            "Speaker result-span assignments must be SpeakerResultSpanAssignment values."
        )


def _validate_speaker_correction_assignments(correction: Any) -> None:
    assigned_result_ids = tuple(
        assignment.result_cluster_id for assignment in correction.result_span_assignments
    )
    if len(assigned_result_ids) != len(set(assigned_result_ids)):
        raise ValueError("Every result cluster may have only one span assignment.")
    if set(assigned_result_ids) != set(correction.result_cluster_ids):
        raise ValueError(
            "Speaker result-span assignments must reference exactly result cluster IDs."
        )
    assigned_spans = tuple(
        span
        for assignment in correction.result_span_assignments
        for span in assignment.spans
    )
    if len(assigned_spans) != len(set(assigned_spans)):
        raise ValueError("A speaker-correction span may not map to multiple result clusters.")
    if set(assigned_spans) != set(correction.spans):
        raise ValueError("Speaker correction spans must exactly match result-span assignments.")


def _validate_speaker_correction_kind(correction: Any) -> None:
    if correction.kind.value == "assign":
        _validate_speaker_assignment(correction)
    elif correction.kind.value == "merge":
        _validate_speaker_merge(correction)
    elif correction.kind.value == "split":
        _validate_speaker_split(correction)


def _validate_speaker_assignment(correction: Any) -> None:
    if (
        len(correction.cluster_ids) != 1
        or correction.result_cluster_ids != correction.cluster_ids
        or not correction.actor_id
        or len(correction.result_span_assignments) != 1
    ):
        raise ValueError("Speaker assignment requires one unchanged cluster, one result, and an actor ID.")


def _validate_speaker_merge(correction: Any) -> None:
    if (
        len(correction.cluster_ids) < 2
        or len(correction.result_cluster_ids) != 1
        or correction.result_cluster_ids[0] in correction.cluster_ids
        or len(correction.result_span_assignments) != 1
    ):
        raise ValueError("Speaker merge requires two inputs, one new result, and one assignment.")


def _validate_speaker_split(correction: Any) -> None:
    if (
        len(correction.cluster_ids) != 1
        or len(correction.result_cluster_ids) < 2
        or set(correction.result_cluster_ids) & set(correction.cluster_ids)
        or not correction.spans
    ):
        raise ValueError("Speaker split requires one input, new result clusters, and spans.")
    _validate_non_overlapping_split_assignments(correction.result_span_assignments)


def _validate_non_overlapping_split_assignments(assignments: tuple[Any, ...]) -> None:
    spans_by_stream: dict[tuple[str, str], list[MediaSpan]] = {}
    for assignment in assignments:
        for span in assignment.spans:
            spans_by_stream.setdefault((span.source_id, span.stream_id), []).append(span)
    for stream_spans in spans_by_stream.values():
        _validate_non_overlapping_stream_spans(stream_spans)


def _validate_non_overlapping_stream_spans(stream_spans: list[MediaSpan]) -> None:
    ordered_spans = sorted(stream_spans, key=lambda span: (span.start_us, span.end_us))
    previous_end_us = -1
    for span in ordered_spans:
        if span.start_us < previous_end_us:
            raise ValueError(
                "Speaker split assignments must not overlap on the same source stream."
            )
        previous_end_us = span.end_us
