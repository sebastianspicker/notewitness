"""Owner-private transcript projections from workbench evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
from pathlib import Path
from typing import Any, Mapping

from notewitness.domain.interop import (
    ExportFormat,
    ExportPreflight,
    LossSeverity,
    ProjectionLoss,
)
from notewitness.domain.timeline import MediaSpan
from notewitness.domain.transcript_document import (
    TranscriptDocument,
    TranscriptSegment,
)
from notewitness.domain.transcription_export import transcript_export_losses
from notewitness.domain.transcription_options import (
    DiarizationMode,
    DisfluencyPolicy,
    LanguageMode,
    TranscriptExportFormat,
    TranscriptionJobSpec,
)
from notewitness.project_store import ProjectStore
from notewitness.transcript_writers import (
    publish_new_private_text,
    render_html,
    render_txt,
    render_webvtt,
)


MAX_EXPORT_FILENAME_CHARS = 128
_UNREVIEWED_PREFIX = "[UNREVIEWED MACHINE SUGGESTION]"
_FORMAT_SUFFIX = {
    TranscriptExportFormat.HTML: ".html",
    TranscriptExportFormat.TEXT: ".txt",
    TranscriptExportFormat.WEBVTT: ".vtt",
}
_INTEROP_FORMAT = {
    TranscriptExportFormat.HTML: ExportFormat.HTML,
    TranscriptExportFormat.TEXT: ExportFormat.TEXT,
    TranscriptExportFormat.WEBVTT: ExportFormat.WEBVTT,
}
_RENDERER = {
    TranscriptExportFormat.HTML: render_html,
    TranscriptExportFormat.TEXT: render_txt,
    TranscriptExportFormat.WEBVTT: render_webvtt,
}


class TranscriptEvidenceLayer(StrEnum):
    ACCEPTED_ONLY = "accepted_only"
    INCLUDE_MACHINE_SUGGESTIONS = "include_machine_suggestions"


class TranscriptExportError(RuntimeError):
    """A graph transcript cannot be projected without violating its contract."""


@dataclass(frozen=True, slots=True)
class TranscriptExportResult:
    export_format: TranscriptExportFormat
    path: str
    record_count: int
    source_id: str
    evidence_layer: TranscriptEvidenceLayer
    checksum_sha256: str
    documented_losses: tuple[ProjectionLoss, ...]


class TranscriptEvidenceExportService:
    """Render accepted evidence, or explicitly selected suggestions, locally."""

    def __init__(self, store: ProjectStore) -> None:
        self._store = store

    @classmethod
    def for_project(
        cls,
        project_root: str | Path,
    ) -> TranscriptEvidenceExportService:
        return cls(ProjectStore(project_root))

    def export(
        self,
        *,
        export_format: TranscriptExportFormat | str,
        filename: str,
        source_id: str,
        evidence_layer: TranscriptEvidenceLayer | str,
        rights_authorized: bool,
        loss_preview_acknowledged: bool,
        visible_timestamps: bool = True,
        timestamp_interval_ms: int = 60_000,
        pause_threshold_ms: int | None = None,
    ) -> TranscriptExportResult:
        format_value, layer = _export_selection(export_format, evidence_layer)
        _validate_export_request(filename, format_value, rights_authorized, loss_preview_acknowledged)
        document, record_ids = _document(self._store.load().payload, source_id, layer)
        job = _job(document, format_value, visible_timestamps, timestamp_interval_ms, pause_threshold_ms)
        preflight = _preflight(filename, format_value, record_ids, job, rights_authorized, loss_preview_acknowledged)
        _require_executable_preflight(preflight)
        contents = _render(document, format_value, visible_timestamps, timestamp_interval_ms, pause_threshold_ms)
        published = publish_new_private_text(self._store.ensure_private_directory("exports") / filename, contents)
        return _export_result(format_value, published, record_ids, source_id, layer, contents, preflight)


def _export_selection(
    export_format: TranscriptExportFormat | str, evidence_layer: TranscriptEvidenceLayer | str
) -> tuple[TranscriptExportFormat, TranscriptEvidenceLayer]:
    try:
        return TranscriptExportFormat(export_format), TranscriptEvidenceLayer(evidence_layer)
    except ValueError as exc:
        raise TranscriptExportError("Transcript format or evidence layer is invalid.") from exc


def _validate_export_request(
    filename: str, export_format: TranscriptExportFormat, rights_authorized: bool, loss_acknowledged: bool
) -> None:
    if not isinstance(rights_authorized, bool) or not isinstance(loss_acknowledged, bool):
        raise TranscriptExportError("Transcript export decisions must be explicit booleans.")
    suffix = _FORMAT_SUFFIX[export_format]
    if not _safe_export_filename(filename, suffix):
        raise TranscriptExportError(f"Transcript export filename must be a safe {suffix} basename.")


def _safe_export_filename(filename: str, suffix: str) -> bool:
    return (
        isinstance(filename, str) and bool(filename) and len(filename) <= MAX_EXPORT_FILENAME_CHARS
        and Path(filename).name == filename and filename.casefold().endswith(suffix)
    )


def _preflight(
    filename: str, export_format: TranscriptExportFormat, record_ids: tuple[str, ...], job: TranscriptionJobSpec,
    rights_authorized: bool, loss_acknowledged: bool,
) -> ExportPreflight:
    losses = (*transcript_export_losses(job, selected_record_ids=record_ids), _graph_projection_loss(record_ids))
    return ExportPreflight(_INTEROP_FORMAT[export_format], f"exports/{filename}", record_ids, rights_authorized, losses, loss_acknowledged)


def _require_executable_preflight(preflight: ExportPreflight) -> None:
    if not preflight.executable:
        raise TranscriptExportError("Transcript export requires rights authorization and loss acknowledgement.")


def _render(
    document: TranscriptDocument, export_format: TranscriptExportFormat, visible_timestamps: bool,
    timestamp_interval_ms: int, pause_threshold_ms: int | None,
) -> str:
    return _RENDERER[export_format](document, visible_timestamps=visible_timestamps, timestamp_interval_ms=timestamp_interval_ms, pause_threshold_ms=pause_threshold_ms)


def _export_result(
    export_format: TranscriptExportFormat, published: Path, record_ids: tuple[str, ...], source_id: str,
    layer: TranscriptEvidenceLayer, contents: str, preflight: ExportPreflight,
) -> TranscriptExportResult:
    return TranscriptExportResult(export_format, str(published), len(record_ids), source_id, layer, hashlib.sha256(contents.encode("utf-8")).hexdigest(), preflight.losses)


def _document(
    payload: Mapping[str, Any],
    source_id: str,
    layer: TranscriptEvidenceLayer,
) -> tuple[TranscriptDocument, tuple[str, ...]]:
    _require_source(payload, source_id)
    targets = {item.get("id"): item for item in payload.get("targets", ()) if isinstance(item, Mapping)}
    events = tuple(item for item in payload.get("events", ()) if isinstance(item, Mapping))
    accepted_sources = _body_references(events, "source_suggestion_id")
    superseded_annotations = _body_references(events, "source_annotation_id")
    entries = _selected_entries(events, targets, source_id, layer, accepted_sources, superseded_annotations)
    _require_entries(entries, layer)
    stream_id = _single_stream(entries)
    record_ids = tuple(str(event["id"]) for event, _ in entries)
    segments = tuple(
        _segment(index, source_id, stream_id, event, target)
        for index, (event, target) in enumerate(entries, start=1)
    )
    digest = hashlib.sha256("\0".join(record_ids).encode("utf-8")).hexdigest()[:24]
    return (
        TranscriptDocument(
            f"document:workbench-export-{digest}", source_id, stream_id,
            "artifact:workbench-evidence", f"run:workbench-export-{digest}",
            "mul", segments, (),
        ),
        record_ids,
    )


def _require_source(payload: Mapping[str, Any], source_id: str) -> None:
    if not any(isinstance(source, Mapping) and source.get("id") == source_id for source in payload.get("sources", ())):
        raise TranscriptExportError("The selected transcript export source does not exist.")


def _selected_entries(
    events: tuple[Mapping[str, Any], ...], targets: Mapping[object, Mapping[str, Any]], source_id: str,
    layer: TranscriptEvidenceLayer, accepted_sources: set[str], superseded_annotations: set[str],
) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    entries = [entry for event in events if (entry := _entry_for_event(event, targets, source_id, layer, accepted_sources, superseded_annotations)) is not None]
    return sorted(entries, key=lambda pair: (pair[1]["selector"]["start_us"], str(pair[0]["id"])))


def _entry_for_event(
    event: Mapping[str, Any], targets: Mapping[object, Mapping[str, Any]], source_id: str,
    layer: TranscriptEvidenceLayer, accepted_sources: set[str], superseded_annotations: set[str],
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    if not _eligible_event(event, layer, accepted_sources, superseded_annotations):
        return None
    matches = _source_targets(event, targets, source_id)
    if len(matches) > 1:
        raise TranscriptExportError("Speech evidence has multiple time anchors for the selected source.")
    return (event, matches[0]) if matches else None


def _eligible_event(event: Mapping[str, Any], layer: TranscriptEvidenceLayer, accepted_sources: set[str], superseded: set[str]) -> bool:
    event_id, status, body = event.get("id"), event.get("review_status"), event.get("body")
    return all(
        (
            _eligible_speech_type(event.get("type")),
            _eligible_review_status(status, event_id, layer, accepted_sources, superseded),
            _has_transcript_text(body),
        )
    )


def _eligible_speech_type(value: object) -> bool:
    return value in {"speech", "speech_over_music"}


def _eligible_review_status(
    status: object,
    event_id: object,
    layer: TranscriptEvidenceLayer,
    accepted_sources: set[str],
    superseded: set[str],
) -> bool:
    if status not in {"human_accepted", "human_created", "machine_suggested"}:
        return False
    if event_id in superseded:
        return False
    return not (
        status == "machine_suggested"
        and (layer is TranscriptEvidenceLayer.ACCEPTED_ONLY or event_id in accepted_sources)
    )


def _has_transcript_text(body: object) -> bool:
    if not isinstance(body, Mapping):
        return False
    value = body.get("value")
    return isinstance(value, str) and bool(value.strip())


def _require_entries(entries: list[tuple[Mapping[str, Any], Mapping[str, Any]]], layer: TranscriptEvidenceLayer) -> None:
    if entries:
        return
    label = "accepted" if layer is TranscriptEvidenceLayer.ACCEPTED_ONLY else "accepted or machine-suggested"
    raise TranscriptExportError(f"No {label} speech evidence is available for this source.")


def _single_stream(entries: list[tuple[Mapping[str, Any], Mapping[str, Any]]]) -> str:
    streams = {str(target["selector"].get("stream_id") or "audio") for _, target in entries}
    if len(streams) != 1:
        raise TranscriptExportError("Transcript export requires one source stream; select a narrower projection.")
    return next(iter(streams))


def _body_references(
    events: tuple[Mapping[str, Any], ...],
    field: str,
) -> set[str]:
    return {
        str(body[field])
        for event in events
        if event.get("review_status") in {"human_accepted", "human_created"}
        and isinstance((body := event.get("body")), Mapping)
        and isinstance(body.get(field), str)
    }


def _source_targets(
    event: Mapping[str, Any],
    targets: Mapping[object, Mapping[str, Any]],
    source_id: str,
) -> tuple[Mapping[str, Any], ...]:
    matches = []
    for target_id in event.get("target_ids", ()):
        target = targets.get(target_id)
        selector = target.get("selector") if isinstance(target, Mapping) else None
        if (
            isinstance(target, Mapping)
            and target.get("source_id") == source_id
            and isinstance(selector, Mapping)
            and isinstance(selector.get("start_us"), int)
            and not isinstance(selector["start_us"], bool)
            and isinstance(selector.get("duration_us"), int)
            and not isinstance(selector["duration_us"], bool)
            and selector["start_us"] >= 0
            and selector["duration_us"] > 0
        ):
            matches.append(target)
    return tuple(matches)


def _segment(
    index: int,
    source_id: str,
    stream_id: str,
    event: Mapping[str, Any],
    target: Mapping[str, Any],
) -> TranscriptSegment:
    selector = target["selector"]
    start_us = selector["start_us"]
    text = str(event["body"]["value"])
    if event.get("review_status") == "machine_suggested":
        text = f"{_UNREVIEWED_PREFIX} {text}"
    confidence = event.get("confidence")
    value = confidence.get("value") if isinstance(confidence, Mapping) else None
    numeric_confidence = (
        float(value)
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= 1
        else 1.0
    )
    actor_id = event.get("actor_id")
    speaker = actor_id if isinstance(actor_id, str) and actor_id else None
    return TranscriptSegment(
        f"segment:workbench-export-{index}",
        source_id,
        stream_id,
        start_us,
        start_us + selector["duration_us"],
        text,
        "mul",
        numeric_confidence,
        (),
        speaker,
    )


def _job(
    document: TranscriptDocument,
    export_format: TranscriptExportFormat,
    visible_timestamps: bool,
    timestamp_interval_ms: int,
    pause_threshold_ms: int | None,
) -> TranscriptionJobSpec:
    start = min(item.start_us for item in document.segments)
    end = max(item.end_us for item in document.segments)
    return TranscriptionJobSpec(
        "job:workbench-evidence-export",
        (MediaSpan(document.source_id, document.stream_id, start, end - start),),
        "profile:workbench-evidence",
        LanguageMode.AUTO,
        None,
        DiarizationMode.OFF,
        None,
        False,
        DisfluencyPolicy.INCLUDE,
        pause_threshold_ms,
        visible_timestamps,
        timestamp_interval_ms,
        export_format,
    )


def _graph_projection_loss(record_ids: tuple[str, ...]) -> ProjectionLoss:
    return ProjectionLoss(
        field="evidence_graph_metadata",
        reason=(
            "Transcript formats retain text, source timing, and project-local speaker labels "
            "but not the full evidence graph, confidence, alternatives, or review history."
        ),
        severity=LossSeverity.LOSSY,
        affected_record_ids=record_ids,
    )
