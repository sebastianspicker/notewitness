"""SQLite row and parameter codecs for durable analysis jobs."""

from __future__ import annotations

import json
import sqlite3

from notewitness.domain.analysis import AnalysisStage, JobState
from notewitness.domain.jobs import AnalysisJobSpec, DurableJob
from notewitness.domain.timeline import MediaSpan


def spec_values(spec: AnalysisJobSpec) -> tuple[object, ...]:
    spans = [
        {
            "source_id": item.source_id,
            "stream_id": item.stream_id,
            "start_us": item.start_us,
            "duration_us": item.duration_us,
        }
        for item in spec.spans
    ]
    return (
        spec.job_id,
        spec.source_id,
        spec.source_sha256,
        json.dumps([stage.value for stage in spec.stages], separators=(",", ":")),
        json.dumps(spans, separators=(",", ":")),
        spec.adapter_fingerprint_sha256,
        spec.runtime_fingerprint_sha256,
        spec.settings_fingerprint_sha256,
        spec.score_sha256,
        spec.created_at,
    )


def row_to_job(row: sqlite3.Row) -> DurableJob:
    spans = tuple(MediaSpan(**item) for item in json.loads(row["spans_json"]))
    spec = AnalysisJobSpec(
        row["job_id"],
        row["source_id"],
        row["source_sha256"],
        tuple(AnalysisStage(item) for item in json.loads(row["stages_json"])),
        spans,
        row["adapter_fingerprint_sha256"],
        row["runtime_fingerprint_sha256"],
        row["settings_fingerprint_sha256"],
        row["score_sha256"],
        row["created_at"],
    )
    return DurableJob(
        spec,
        JobState(row["state"]),
        row["owner_id"],
        row["lease_expires_at"],
        bool(row["cancel_requested"]),
        AnalysisStage(row["checkpoint_stage"])
        if row["checkpoint_stage"]
        else None,
        row["completed_span_count"],
        row["continuation_token"],
        row["last_artifact_id"],
        row["failure_reason"],
    )
