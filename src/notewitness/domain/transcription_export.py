"""Transcript-specific export projections and loss preflight."""

from __future__ import annotations

from notewitness.domain.interop import (
    ExportFormat,
    ExportPreflight,
    LossSeverity,
    ProjectionLoss,
)
from notewitness.domain.transcription_options import (
    TranscriptExportFormat,
    TranscriptionJobSpec,
)
from notewitness.domain.transcription_run import (
    CanonicalTranscriptEvidence,
    TranscriptionRunManifest,
)


def transcript_export_losses(
    job: TranscriptionJobSpec,
    evidence: CanonicalTranscriptEvidence | None = None,
    selected_record_ids: tuple[str, ...] | None = None,
) -> tuple[ProjectionLoss, ...]:
    """Expose noScribe-compatible VTT rendering loss without mutating evidence."""

    if job.output_format is not TranscriptExportFormat.WEBVTT:
        return ()
    losses: list[ProjectionLoss] = []
    selected = selected_record_ids or (
        evidence.record_ids if evidence is not None else (job.job_id,)
    )

    def add_loss(
        field_name: str, reason: str, affected_record_ids: tuple[str, ...]
    ) -> None:
        if affected_record_ids:
            losses.append(
                ProjectionLoss(
                    field=field_name,
                    reason=reason,
                    severity=LossSeverity.LOSSY,
                    affected_record_ids=affected_record_ids,
                )
            )

    if job.pause_threshold_ms is not None:
        add_loss(
            "pause_threshold_ms",
            "WebVTT keeps timing but not transcript pause-marker rendering.",
            selected,
        )
    if evidence is None:
        overlap_ids = selected if job.detect_overlap else ()
    else:
        selected_set = set(selected)
        overlap_ids = tuple(
            record_id
            for record_id in evidence.overlap_event_ids
            if record_id in selected_set
        )
    add_loss(
        "detect_overlap",
        "WebVTT cannot preserve noScribe-style overlap delimiter rendering.",
        overlap_ids,
    )
    if job.visible_timestamps:
        add_loss(
            "visible_timestamps",
            "WebVTT cues replace inline visible transcript timestamp markers.",
            selected,
        )
    return tuple(losses)


def transcript_export_preflight(
    manifest: TranscriptionRunManifest,
    evidence: CanonicalTranscriptEvidence,
    *,
    destination: str,
    selected_record_ids: tuple[str, ...],
    rights_authorized: bool,
    loss_preview_acknowledged: bool,
) -> ExportPreflight:
    """Bind canonical evidence to its run before authorizing a projection."""

    if not isinstance(manifest, TranscriptionRunManifest):
        raise ValueError("Transcript exports require a transcription run manifest.")
    if evidence.run_id != manifest.run_id:
        raise ValueError("Transcript evidence must belong to the exported run.")
    job = manifest.job
    selected = set(selected_record_ids)
    unknown = selected - set(evidence.record_ids)
    if unknown:
        raise ValueError(
            "Transcript export selections must belong to canonical evidence."
        )
    export_format = {
        TranscriptExportFormat.HTML: ExportFormat.HTML,
        TranscriptExportFormat.TEXT: ExportFormat.TEXT,
        TranscriptExportFormat.WEBVTT: ExportFormat.WEBVTT,
    }[job.output_format]
    return ExportPreflight(
        export_format=export_format,
        destination=destination,
        selected_record_ids=selected_record_ids,
        rights_authorized=rights_authorized,
        losses=transcript_export_losses(job, evidence, selected_record_ids),
        loss_preview_acknowledged=loss_preview_acknowledged,
    )
