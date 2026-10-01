"""Workbench snapshot projection and ingested media-source resolution."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import PurePosixPath
from typing import Any, Mapping

from notewitness.core.evidence.graph import EvidenceGraph
from notewitness.lessons.actors import is_human_evidence_author
from notewitness.lessons.lesson_notes import LessonNotesProjector
from notewitness.lessons.review_rules import ReviewError, identifier, index
from notewitness.projects.media import is_project_media_uri
from notewitness.projects.store import ProjectSnapshot, ProjectStore

from .timeline import TimelineViewModel


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
        raise ReviewError("Media source is unavailable.")
    return snapshot, source, PurePosixPath(str(source["uri"]))


def _is_project_media_source(source: Mapping[str, Any]) -> bool:
    uri = source.get("uri")
    if not isinstance(uri, str):
        return False
    return is_project_media_uri(uri, nested=True)


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
