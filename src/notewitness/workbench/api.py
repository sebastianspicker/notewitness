"""Workbench projection, mutation, job, and export HTTP handlers."""

from __future__ import annotations

from dataclasses import asdict
from http import HTTPStatus
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import unquote

from notewitness.core.audio import MetronomePlan, tuner_reading
from notewitness.core.transcription.options import TranscriptExportFormat
from notewitness.lessons.review_rules import ReviewError
from notewitness.lessons.evidence_review import (
    accept_evidence_suggestion,
    accept_relation_suggestion,
    create_exact_time_bookmark,
    reject_evidence_suggestion,
    reject_relation_suggestion,
    revise_evidence_annotation,
    set_practice_task_completed,
)
from notewitness.lessons.music_export import MusicExportFormat, SymbolicMusicExportService
from notewitness.lessons.transcript_export import (
    TranscriptEvidenceExportService,
    TranscriptEvidenceLayer,
    TranscriptExportError,
)
from notewitness.lessons.actors import add_project_actor
from notewitness.projects.store import ProjectStoreError

from .jobs import WorkbenchProcessingError
from .protocol import (
    RequestError,
    optional_number,
    optional_string,
    required_integer,
    required_number,
    required_string,
)
from .snapshot import project_workbench


class WorkbenchApiMixin:
    """Route implementations kept separate from HTTP framing and media I/O."""

    def _workbench_snapshot(self, *, send_body: bool) -> None:
        try:
            payload = project_workbench(str(self.server.project_root))
            payload["csrf_token"] = self.server.csrf_token
            self._json(HTTPStatus.OK, payload, send_body=send_body)
        except (RequestError, ReviewError, ProjectStoreError, ValueError):
            self._json_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "project_projection_failed",
                send_body=send_body,
            )

    def _job_snapshot(self, *, send_body: bool) -> None:
        try:
            self._json(HTTPStatus.OK, self.server.processing.snapshot(), send_body=send_body)
        except (WorkbenchProcessingError, OSError, sqlite3.Error):
            self._json_error(
                HTTPStatus.INTERNAL_SERVER_ERROR, "job_store_failed", send_body=send_body
            )

    def _accept_review(self) -> None:
        request = self._json_request()
        result = accept_evidence_suggestion(
            str(self.server.project_root),
            event_id=required_string(request, "event_id"),
            author_id=required_string(request, "author_id"),
            actor_id=required_string(request, "actor_id"),
            reason=required_string(request, "reason"),
            expected_sha256=required_string(request, "project_sha256"),
            replacement_text=optional_string(request, "replacement_text"),
        )
        self._json(HTTPStatus.CREATED, asdict(result))

    def _accept_relation_review(self) -> None:
        request = self._json_request()
        result = accept_relation_suggestion(
            str(self.server.project_root),
            relation_id=required_string(request, "relation_id"),
            author_id=required_string(request, "author_id"),
            reason=required_string(request, "reason"),
            expected_sha256=required_string(request, "project_sha256"),
        )
        self._json(HTTPStatus.CREATED, asdict(result))

    def _reject_review(self) -> None:
        request = self._json_request()
        expected_fields = {"event_id", "author_id", "reason", "project_sha256"}
        if set(request) != expected_fields:
            raise RequestError("Evidence rejection request fields are invalid.")
        result = reject_evidence_suggestion(
            str(self.server.project_root),
            event_id=required_string(request, "event_id"),
            author_id=required_string(request, "author_id"),
            reason=required_string(request, "reason"),
            expected_sha256=required_string(request, "project_sha256"),
        )
        self._json(HTTPStatus.CREATED, asdict(result))

    def _reject_relation_review(self) -> None:
        request = self._json_request()
        result = reject_relation_suggestion(
            str(self.server.project_root),
            relation_id=required_string(request, "relation_id"),
            author_id=required_string(request, "author_id"),
            reason=required_string(request, "reason"),
            expected_sha256=required_string(request, "project_sha256"),
        )
        self._json(HTTPStatus.CREATED, asdict(result))

    def _create_bookmark(self) -> None:
        request = self._json_request()
        result = create_exact_time_bookmark(
            str(self.server.project_root),
            source_id=required_string(request, "source_id"),
            start_us=required_integer(request, "start_us"),
            duration_us=required_integer(request, "duration_us"),
            label=required_string(request, "label"),
            author_id=required_string(request, "author_id"),
            expected_sha256=required_string(request, "project_sha256"),
        )
        self._json(HTTPStatus.CREATED, asdict(result))

    def _create_actor(self) -> None:
        request = self._json_request()
        snapshot = add_project_actor(
            str(self.server.project_root),
            actor_id=required_string(request, "actor_id"),
            role=required_string(request, "role"),
            visibility="restricted",
            expected_sha256=required_string(request, "project_sha256"),
        )
        self._json(HTTPStatus.CREATED, {"project_sha256": snapshot.sha256})

    def _revise_annotation(self) -> None:
        request = self._json_request()
        result = revise_evidence_annotation(
            str(self.server.project_root),
            event_id=required_string(request, "event_id"),
            author_id=required_string(request, "author_id"),
            actor_id=required_string(request, "actor_id"),
            reason=required_string(request, "reason"),
            replacement_text=required_string(request, "replacement_text"),
            expected_sha256=required_string(request, "project_sha256"),
        )
        self._json(HTTPStatus.CREATED, asdict(result))

    def _update_practice(self) -> None:
        request = self._json_request()
        completed = request.get("completed")
        if not isinstance(completed, bool):
            raise RequestError("completed must be a boolean.")
        result = set_practice_task_completed(
            str(self.server.project_root),
            task_id=required_string(request, "task_id"),
            completed=completed,
            author_id=required_string(request, "author_id"),
            expected_sha256=required_string(request, "project_sha256"),
        )
        self._json(HTTPStatus.CREATED, asdict(result))

    def _tuner(self) -> None:
        request = self._json_request()
        reading = tuner_reading(
            required_number(request, "frequency_hz"),
            a4_hz=optional_number(request, "a4_hz", default=440.0),
        )
        self._json(HTTPStatus.OK, asdict(reading))

    def _metronome(self) -> None:
        request = self._json_request()
        plan = MetronomePlan(
            bpm=required_number(request, "bpm"),
            beats_per_bar=required_integer(request, "beats_per_bar"),
            subdivisions_per_beat=required_integer(request, "subdivisions"),
        )
        ticks = plan.schedule(required_integer(request, "bars"))
        self._json(
            HTTPStatus.OK,
            {
                "beats_per_bar": plan.beats_per_bar,
                "bpm": plan.bpm,
                "subdivisions_per_beat": plan.subdivisions_per_beat,
                "ticks": [asdict(tick) for tick in ticks],
            },
        )

    def _enqueue_job(self) -> None:
        payload = self._json_request()
        job = self.server.processing.enqueue(
            required_string(payload, "kind"), required_string(payload, "source_id")
        )
        self._json(HTTPStatus.ACCEPTED, job.as_public_dict())

    def _export_music(self) -> None:
        payload = self._json_request()
        expected = {
            "acknowledge_export_losses", "authorize_local_export", "filename", "format", "source_id"
        }
        if set(payload) != expected:
            raise RequestError("Music export request has unknown or missing fields.")
        authorized = payload.get("authorize_local_export")
        acknowledged = payload.get("acknowledge_export_losses")
        if not isinstance(authorized, bool) or not isinstance(acknowledged, bool):
            raise RequestError("Music export decisions must be booleans.")
        result = SymbolicMusicExportService.for_project(self.server.project_root).export(
            export_format=MusicExportFormat(required_string(payload, "format")),
            filename=required_string(payload, "filename"),
            rights_authorized=authorized,
            loss_preview_acknowledged=acknowledged,
            source_id=required_string(payload, "source_id"),
        )
        self._json(HTTPStatus.CREATED, _music_export_response(result))

    def _export_transcript(self) -> None:
        payload = self._json_request()
        expected = {
            "acknowledge_export_losses", "authorize_local_export", "evidence_layer", "filename",
            "format", "pause_threshold_ms", "source_id", "timestamp_interval_ms",
            "visible_timestamps",
        }
        if set(payload) != expected:
            raise RequestError("Transcript export request has unknown or missing fields.")
        authorized = payload.get("authorize_local_export")
        acknowledged = payload.get("acknowledge_export_losses")
        visible = payload.get("visible_timestamps")
        interval = payload.get("timestamp_interval_ms")
        pause = payload.get("pause_threshold_ms")
        if not all(isinstance(value, bool) for value in (authorized, acknowledged, visible)):
            raise RequestError("Transcript export decisions must be booleans.")
        if not isinstance(interval, int) or isinstance(interval, bool):
            raise RequestError("timestamp_interval_ms must be an integer.")
        if pause is not None and (not isinstance(pause, int) or isinstance(pause, bool)):
            raise RequestError("pause_threshold_ms must be an integer or null.")
        try:
            result = TranscriptEvidenceExportService.for_project(self.server.project_root).export(
                export_format=TranscriptExportFormat(required_string(payload, "format")),
                filename=required_string(payload, "filename"),
                source_id=required_string(payload, "source_id"),
                evidence_layer=TranscriptEvidenceLayer(required_string(payload, "evidence_layer")),
                rights_authorized=authorized,
                loss_preview_acknowledged=acknowledged,
                visible_timestamps=visible,
                timestamp_interval_ms=interval,
                pause_threshold_ms=pause,
            )
        except (TranscriptExportError, ValueError) as exc:
            raise ReviewError(str(exc)) from exc
        self._json(HTTPStatus.CREATED, _transcript_export_response(result))

    def _job_action(self, path: str) -> None:
        parts = path.split("/")
        if len(parts) != 5 or parts[:3] != ["", "api", "jobs"]:
            self._json_error(HTTPStatus.NOT_FOUND, "route_not_found")
            return
        try:
            job_id = unquote(parts[3], errors="strict")
        except UnicodeError:
            self._json_error(HTTPStatus.NOT_FOUND, "job_not_found")
            return
        self._json_request()
        if parts[4] == "cancel":
            job = self.server.processing.cancel(job_id)
        elif parts[4] == "retry":
            job = self.server.processing.retry(job_id)
        else:
            self._json_error(HTTPStatus.NOT_FOUND, "route_not_found")
            return
        self._json(HTTPStatus.ACCEPTED, job.as_public_dict())


def _music_export_response(result: Any) -> dict[str, Any]:
    return {
        "checksum_sha256": result.checksum_sha256,
        "documented_losses": [asdict(loss) for loss in result.documented_losses],
        "filename": Path(result.path).name,
        "format": result.export_format.value,
        "network_used": False,
        "record_count": result.record_count,
        "source_ids": list(result.source_ids),
    }


def _transcript_export_response(result: Any) -> dict[str, Any]:
    return {
        "checksum_sha256": result.checksum_sha256,
        "documented_losses": [asdict(loss) for loss in result.documented_losses],
        "evidence_layer": result.evidence_layer.value,
        "filename": Path(result.path).name,
        "format": result.export_format.value,
        "network_used": False,
        "record_count": result.record_count,
        "source_id": result.source_id,
    }
