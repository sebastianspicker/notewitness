"""Workbench projection, mutation, job, and export HTTP handlers."""

from __future__ import annotations

from dataclasses import asdict
from http import HTTPStatus
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import unquote

from notewitness.application.workbench import WorkbenchError
from notewitness.application.workbench_processing import WorkbenchProcessingError
from notewitness.application.transcript_export import TranscriptExportError
from notewitness.project_store import ProjectStoreError

from .workbench_protocol import (
    _optional_number,
    _optional_string,
    _required_integer,
    _required_number,
    _required_string,
)


def _legacy(name: str) -> Any:
    """Resolve old façade names so established monkeypatch paths still work."""

    from . import workbench_server

    return getattr(workbench_server, name)


class WorkbenchApiMixin:
    """Route implementations kept separate from HTTP framing and media I/O."""

    def _workbench_snapshot(self, *, send_body: bool) -> None:
        try:
            payload = _legacy("project_workbench")(str(self.server.project_root))
            payload["csrf_token"] = self.server.csrf_token
            self._json(HTTPStatus.OK, payload, send_body=send_body)
        except (WorkbenchError, ProjectStoreError, ValueError):
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
        result = _legacy("accept_evidence_suggestion")(
            str(self.server.project_root),
            event_id=_required_string(request, "event_id"),
            author_id=_required_string(request, "author_id"),
            actor_id=_required_string(request, "actor_id"),
            reason=_required_string(request, "reason"),
            expected_sha256=_required_string(request, "project_sha256"),
            replacement_text=_optional_string(request, "replacement_text"),
        )
        self._json(HTTPStatus.CREATED, asdict(result))

    def _accept_relation_review(self) -> None:
        request = self._json_request()
        result = _legacy("accept_relation_suggestion")(
            str(self.server.project_root),
            relation_id=_required_string(request, "relation_id"),
            author_id=_required_string(request, "author_id"),
            reason=_required_string(request, "reason"),
            expected_sha256=_required_string(request, "project_sha256"),
        )
        self._json(HTTPStatus.CREATED, asdict(result))

    def _reject_relation_review(self) -> None:
        request = self._json_request()
        result = _legacy("reject_relation_suggestion")(
            str(self.server.project_root),
            relation_id=_required_string(request, "relation_id"),
            author_id=_required_string(request, "author_id"),
            reason=_required_string(request, "reason"),
            expected_sha256=_required_string(request, "project_sha256"),
        )
        self._json(HTTPStatus.CREATED, asdict(result))

    def _create_bookmark(self) -> None:
        request = self._json_request()
        result = _legacy("create_exact_time_bookmark")(
            str(self.server.project_root),
            source_id=_required_string(request, "source_id"),
            start_us=_required_integer(request, "start_us"),
            duration_us=_required_integer(request, "duration_us"),
            label=_required_string(request, "label"),
            author_id=_required_string(request, "author_id"),
            expected_sha256=_required_string(request, "project_sha256"),
        )
        self._json(HTTPStatus.CREATED, asdict(result))

    def _create_actor(self) -> None:
        request = self._json_request()
        snapshot = _legacy("add_project_actor")(
            str(self.server.project_root),
            actor_id=_required_string(request, "actor_id"),
            role=_required_string(request, "role"),
            visibility="restricted",
            expected_sha256=_required_string(request, "project_sha256"),
        )
        self._json(HTTPStatus.CREATED, {"project_sha256": snapshot.sha256})

    def _revise_annotation(self) -> None:
        request = self._json_request()
        result = _legacy("revise_evidence_annotation")(
            str(self.server.project_root),
            event_id=_required_string(request, "event_id"),
            author_id=_required_string(request, "author_id"),
            actor_id=_required_string(request, "actor_id"),
            reason=_required_string(request, "reason"),
            replacement_text=_required_string(request, "replacement_text"),
            expected_sha256=_required_string(request, "project_sha256"),
        )
        self._json(HTTPStatus.CREATED, asdict(result))

    def _update_practice(self) -> None:
        request = self._json_request()
        completed = request.get("completed")
        if not isinstance(completed, bool):
            raise WorkbenchError("completed must be a boolean.")
        result = _legacy("set_practice_task_completed")(
            str(self.server.project_root),
            task_id=_required_string(request, "task_id"),
            completed=completed,
            author_id=_required_string(request, "author_id"),
            expected_sha256=_required_string(request, "project_sha256"),
        )
        self._json(HTTPStatus.CREATED, asdict(result))

    def _tuner(self) -> None:
        request = self._json_request()
        reading = _legacy("tuner_reading")(
            _required_number(request, "frequency_hz"),
            a4_hz=_optional_number(request, "a4_hz", default=440.0),
        )
        self._json(HTTPStatus.OK, asdict(reading))

    def _metronome(self) -> None:
        request = self._json_request()
        plan = _legacy("MetronomePlan")(
            bpm=_required_number(request, "bpm"),
            beats_per_bar=_required_integer(request, "beats_per_bar"),
            subdivisions_per_beat=_required_integer(request, "subdivisions"),
        )
        ticks = plan.schedule(_required_integer(request, "bars"))
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
            _required_string(payload, "kind"), _required_string(payload, "source_id")
        )
        self._json(HTTPStatus.ACCEPTED, job.as_public_dict())

    def _export_music(self) -> None:
        payload = self._json_request()
        expected = {
            "acknowledge_export_losses", "authorize_local_export", "filename", "format", "source_id"
        }
        if set(payload) != expected:
            raise WorkbenchError("Music export request has unknown or missing fields.")
        authorized = payload.get("authorize_local_export")
        acknowledged = payload.get("acknowledge_export_losses")
        if not isinstance(authorized, bool) or not isinstance(acknowledged, bool):
            raise WorkbenchError("Music export decisions must be booleans.")
        result = _legacy("SymbolicMusicExportService").for_project(self.server.project_root).export(
            export_format=_legacy("MusicExportFormat")(_required_string(payload, "format")),
            filename=_required_string(payload, "filename"),
            rights_authorized=authorized,
            loss_preview_acknowledged=acknowledged,
            source_id=_required_string(payload, "source_id"),
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
            raise WorkbenchError("Transcript export request has unknown or missing fields.")
        authorized = payload.get("authorize_local_export")
        acknowledged = payload.get("acknowledge_export_losses")
        visible = payload.get("visible_timestamps")
        interval = payload.get("timestamp_interval_ms")
        pause = payload.get("pause_threshold_ms")
        if not all(isinstance(value, bool) for value in (authorized, acknowledged, visible)):
            raise WorkbenchError("Transcript export decisions must be booleans.")
        if not isinstance(interval, int) or isinstance(interval, bool):
            raise WorkbenchError("timestamp_interval_ms must be an integer.")
        if pause is not None and (not isinstance(pause, int) or isinstance(pause, bool)):
            raise WorkbenchError("pause_threshold_ms must be an integer or null.")
        try:
            result = _legacy("TranscriptEvidenceExportService").for_project(self.server.project_root).export(
                export_format=_legacy("TranscriptExportFormat")(_required_string(payload, "format")),
                filename=_required_string(payload, "filename"),
                source_id=_required_string(payload, "source_id"),
                evidence_layer=_legacy("TranscriptEvidenceLayer")(_required_string(payload, "evidence_layer")),
                rights_authorized=authorized,
                loss_preview_acknowledged=acknowledged,
                visible_timestamps=visible,
                timestamp_interval_ms=interval,
                pause_threshold_ms=pause,
            )
        except (TranscriptExportError, ValueError) as exc:
            raise WorkbenchError(str(exc)) from exc
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
