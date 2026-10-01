"""Evidence records for locally ingested browser captures."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from notewitness.lessons.review_rules import (
    MAX_BOOKMARK_LABEL_CHARS,
    MAX_CAPTURE_DURATION_MS,
    ReviewError,
    bounded_text,
    ensure_human_generator,
    identifier,
    index,
    now,
    require_human_author,
)
from notewitness.projects.media import MediaPublication


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
        raise ReviewError(
            f"duration_ms must be in [0, {MAX_CAPTURE_DURATION_MS}]."
        )
    try:
        parsed_start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ReviewError("Capture start must be an ISO-8601 timestamp.") from exc
    if parsed_start.tzinfo is None:
        raise ReviewError("Capture start timestamp must include a timezone.")
    normalized_start = parsed_start.astimezone(timezone.utc).isoformat()
    token = uuid4().hex
    event_id = f"event:capture-{token}"
    revision_id = f"revision:create-capture-{token}"

    def append(payload: dict[str, object], publication: MediaPublication) -> None:
        typed_payload: dict[str, Any] = payload
        actors = index(typed_payload, "actors")
        require_human_author(actors, author_id, "Capture")
        if publication.source_id not in index(typed_payload, "sources"):
            raise ReviewError("Capture source was not published atomically.")
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
