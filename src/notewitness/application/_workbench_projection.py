"""Workbench snapshot, media resolution, and browser-capture publication."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Callable, Mapping
from uuid import uuid4

from notewitness.application.actor_eligibility import is_human_evidence_author
from notewitness.application._workbench_contracts import (
    MAX_BOOKMARK_LABEL_CHARS,
    MAX_CAPTURE_DURATION_MS,
    WorkbenchError,
    bounded_text,
    ensure_human_generator,
    identifier,
    index,
    now,
    require_human_author,
)
from notewitness.application.lesson_notes import LessonNotesProjector
from notewitness.evidence import EvidenceGraph
from notewitness.media_ingest import MediaPublication
from notewitness.presentation.timeline import TimelineViewModel
from notewitness.project_store import ProjectSnapshot, ProjectStore


def project_workbench(project_root: str) -> dict[str, Any]:
    """Return one validated, JSON-compatible local workbench snapshot."""

    snapshot = ProjectStore(project_root).load()
    graph = EvidenceGraph(snapshot.payload)
    notes = LessonNotesProjector.project(graph)
    timeline = TimelineViewModel.from_lesson_notes(notes)
    project = snapshot.payload["project"]
    actors = [
        {
            key: actor[key]
            for key in ("id", "role", "instrument_role")
            if key in actor
        }
        for actor in sorted(
            snapshot.payload["actors"],
            key=lambda item: str(item["id"]),
        )
    ]
    for actor in actors:
        actor["human_evidence_eligible"] = is_human_evidence_author(actor)
    media_sources = [
        source
        for source in snapshot.payload["sources"]
        if _is_project_media_source(source)
    ]
    capture_details = _capture_details_by_source(snapshot.payload)
    media = []
    for position, source in enumerate(media_sources, start=1):
        source_id = str(source["id"])
        capture = capture_details.get(source_id, {})
        timeline_duration_us = max(
            (
                extent.end_us
                for extent in notes.statistics.timeline_extents
                if extent.source_id == source_id
            ),
            default=0,
        )
        raw_capture_duration = capture.get("duration_ms", 0)
        capture_duration_us = (
            raw_capture_duration * 1_000
            if isinstance(raw_capture_duration, int)
            and not isinstance(raw_capture_duration, bool)
            and raw_capture_duration >= 0
            else 0
        )
        media.append(
            {
                "display_name": str(
                    capture.get("name") or f"Lesson recording {position}"
                ),
                "duration_us": max(timeline_duration_us, capture_duration_us),
                "kind": source["kind"],
                "source_id": source_id,
                "url": f"/api/media/{_url_path_segment(source_id)}",
            }
        )
    return {
        "actors": actors,
        "capabilities": {
            "bookmark": True,
            "capture": True,
            "metronome": True,
            "music_export": True,
            "playback": bool(media),
            "review": True,
            "tuner": True,
        },
        "lesson": notes.as_dict(),
        "media": media,
        "metronome": {
            "bars": 1,
            "beats_per_bar": 4,
            "bpm": 72,
            "subdivisions": 1,
        },
        "project": {
            "id": str(project.get("id", "")),
            "network_mode": notes.network_mode,
            "saved": True,
            "sha256": snapshot.sha256,
            "title": str(project.get("name", "Untitled lesson")),
        },
        "timeline": asdict(timeline),
    }


def resolve_media_source(
    project_root: str,
    source_id: str,
) -> tuple[ProjectSnapshot, Mapping[str, Any], PurePosixPath]:
    """Resolve only an ingested project-relative media source."""

    identifier(source_id, "source_id")
    snapshot = ProjectStore(project_root).load()
    source = index(snapshot.payload, "sources").get(source_id)
    if source is None or not _is_project_media_source(source):
        raise WorkbenchError("Media source is unavailable.")
    return snapshot, source, PurePosixPath(str(source["uri"]))


def capture_publication_hook(
    *,
    author_id: str,
    capture_name: str,
    content_type: str,
    started_at: str,
    duration_ms: int,
) -> Callable[[dict[str, object], MediaPublication], None]:
    """Build an atomic project-graph publication for a browser capture."""

    identifier(author_id, "author_id")
    normalized_name = bounded_text(
        capture_name,
        "capture_name",
        MAX_BOOKMARK_LABEL_CHARS,
    )
    normalized_type = bounded_text(content_type, "content_type", 128)
    if (
        not isinstance(duration_ms, int)
        or isinstance(duration_ms, bool)
        or not 0 <= duration_ms <= MAX_CAPTURE_DURATION_MS
    ):
        raise WorkbenchError(
            f"duration_ms must be in [0, {MAX_CAPTURE_DURATION_MS}]."
        )
    try:
        parsed_start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise WorkbenchError("Capture start must be an ISO-8601 timestamp.") from exc
    if parsed_start.tzinfo is None:
        raise WorkbenchError("Capture start timestamp must include a timezone.")
    normalized_start = parsed_start.astimezone(timezone.utc).isoformat()
    token = uuid4().hex
    event_id = f"event:capture-{token}"
    revision_id = f"revision:create-capture-{token}"

    def append(payload: dict[str, object], publication: MediaPublication) -> None:
        typed_payload: dict[str, Any] = payload
        actors = index(typed_payload, "actors")
        require_human_author(actors, author_id, "Capture")
        if publication.source_id not in index(typed_payload, "sources"):
            raise WorkbenchError("Capture source was not published atomically.")
        generator_id = ensure_human_generator(typed_payload, author_id)
        typed_payload["events"].append(
            {
                "actor_id": author_id,
                "alternatives": [],
                "body": {
                    "format": "notewitness.capture.v1",
                    "value": {
                        "byte_count": publication.byte_count,
                        "content_type": normalized_type,
                        "device_alias": "browser-default-audio-input",
                        "duration_ms": duration_ms,
                        "name": normalized_name,
                        "sha256": publication.sha256,
                        "source_id": publication.source_id,
                        "started_at": normalized_start,
                    },
                },
                "confidence": {"kind": "human_capture"},
                "generator_id": generator_id,
                "id": event_id,
                "layer": "accepted_annotation",
                "review_status": "human_created",
                "rights_id": publication.rights_id,
                "scope": "project",
                "target_ids": [],
                "type": "local:capture",
            }
        )
        typed_payload["revisions"].append(
            {
                "author_id": author_id,
                "id": revision_id,
                "operation": "create",
                "parent_revision_ids": [],
                "reason": "Recorded and ingested a local browser capture.",
                "record_id": event_id,
                "timestamp": now(),
            }
        )

    return append


def _is_project_media_source(source: Mapping[str, Any]) -> bool:
    uri = source.get("uri")
    if not isinstance(uri, str):
        return False
    relative = PurePosixPath(uri)
    return bool(
        not relative.is_absolute()
        and "\\" not in uri
        and relative.parts
        and relative.parts[0] == "media"
        and all(part not in {"", ".", ".."} for part in relative.parts)
    )


def _capture_details_by_source(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for event in payload.get("events", []):
        if not isinstance(event, dict) or event.get("type") != "local:capture":
            continue
        body = event.get("body")
        value = body.get("value") if isinstance(body, dict) else None
        source_id = value.get("source_id") if isinstance(value, dict) else None
        if isinstance(source_id, str):
            result[source_id] = value
    return result


def _url_path_segment(value: str) -> str:
    """Percent-encode UTF-8 without importing a network-capable URL package."""

    unreserved = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    return "".join(
        chr(byte) if byte in unreserved else f"%{byte:02X}"
        for byte in value.encode("utf-8")
    )
