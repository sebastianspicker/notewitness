"""Local workbench read model and append-only human editing operations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import PurePosixPath
from typing import Any, Callable, Mapping
from uuid import uuid4

from notewitness.application.actor_eligibility import is_human_evidence_author
from notewitness.application.lesson_notes import LessonNotesProjector
from notewitness.evidence import ACCESS_RANK, EvidenceGraph
from notewitness.media_ingest import MediaPublication
from notewitness.presentation.timeline import TimelineViewModel
from notewitness.project_store import ProjectSnapshot, ProjectStore


MAX_BOOKMARK_LABEL_CHARS = 1_000
MAX_REVIEW_REASON_CHARS = 4_000
MAX_REPLACEMENT_TEXT_CHARS = 20_000
MAX_IDENTIFIER_CHARS = 256
MAX_CAPTURE_DURATION_MS = 2 * 60 * 60 * 1_000


class WorkbenchError(RuntimeError):
    """A workbench operation violated the local evidence contract."""


@dataclass(frozen=True, slots=True)
class WorkbenchMutation:
    record_ids: tuple[str, ...]
    revision_ids: tuple[str, ...]
    project_sha256: str


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
    for index, source in enumerate(media_sources, start=1):
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
                "display_name": str(capture.get("name") or f"Lesson recording {index}"),
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


def accept_evidence_suggestion(
    project_root: str,
    *,
    event_id: str,
    author_id: str,
    actor_id: str,
    reason: str,
    expected_sha256: str,
    replacement_text: str | None = None,
) -> WorkbenchMutation:
    """Append a human-reviewed event without replacing machine evidence."""

    _identifier(event_id, "event_id")
    _identifier(author_id, "author_id")
    _identifier(actor_id, "actor_id")
    normalized_reason = _bounded_text(reason, "reason", MAX_REVIEW_REASON_CHARS)
    replacement = (
        _bounded_text(
            replacement_text,
            "replacement_text",
            MAX_REPLACEMENT_TEXT_CHARS,
        )
        if replacement_text is not None
        else None
    )
    token = uuid4().hex
    accepted_id = f"event:accepted-{token}"
    revision_id = f"revision:adjudicate-{token}"

    def append(payload: dict[str, Any]) -> None:
        actors = _index(payload, "actors")
        events = _index(payload, "events")
        generators = _index(payload, "generators")
        _require_human_author(actors, author_id, "Review")
        if actor_id not in actors:
            raise WorkbenchError("Attributed actor must exist.")
        source = events.get(event_id)
        if source is None:
            raise WorkbenchError("The selected evidence suggestion does not exist.")
        generator = generators.get(str(source.get("generator_id")))
        if (
            source.get("review_status") != "machine_suggested"
            or source.get("layer") != "normalized_hypothesis"
            or generator is None
            or generator.get("kind") != "machine"
        ):
            raise WorkbenchError("Only normalized machine suggestions may be accepted.")
        if any(
            item.get("review_status") == "human_accepted"
            and isinstance(item.get("body"), Mapping)
            and item["body"].get("source_suggestion_id") == event_id
            for item in payload["events"]
        ):
            raise WorkbenchError("The selected suggestion was already accepted.")
        body = source.get("body")
        if not isinstance(body, Mapping):
            raise WorkbenchError("The selected suggestion body is malformed.")
        accepted_body = dict(body)
        accepted_body["source_suggestion_id"] = event_id
        if replacement is not None:
            if not isinstance(accepted_body.get("value"), str):
                raise WorkbenchError(
                    "Text replacement is only valid for textual evidence."
                )
            accepted_body["value"] = replacement
        generator_id = _ensure_human_generator(payload, author_id)
        payload["events"].append(
            {
                **source,
                "actor_id": actor_id,
                "body": accepted_body,
                "confidence": {"kind": "human_review"},
                "generator_id": generator_id,
                "id": accepted_id,
                "layer": "accepted_annotation",
                "review_status": "human_accepted",
            }
        )
        payload["revisions"].append(
            {
                "author_id": author_id,
                "id": revision_id,
                "operation": "adjudicate",
                "parent_revision_ids": [],
                "reason": normalized_reason,
                "record_id": accepted_id,
                "timestamp": _now(),
            }
        )

    updated = ProjectStore(project_root).mutate(
        append,
        expected_sha256=expected_sha256,
    )
    return WorkbenchMutation((accepted_id,), (revision_id,), updated.sha256)


def accept_relation_suggestion(
    project_root: str,
    *,
    relation_id: str,
    author_id: str,
    reason: str,
    expected_sha256: str,
) -> WorkbenchMutation:
    """Append a human-accepted relation using already reviewed event anchors.

    Relations have no editable text payload in the evidence schema.  Acceptance
    therefore never accepts a replacement value: it creates a new accepted
    relation whose event arguments point at the corresponding human evidence.
    """

    _identifier(relation_id, "relation_id")
    _identifier(author_id, "author_id")
    normalized_reason = _bounded_text(reason, "reason", MAX_REVIEW_REASON_CHARS)
    token = uuid4().hex
    accepted_id = f"relation:accepted-{token}"
    source_revision_id = f"revision:supersede-{token}"
    accepted_revision_id = f"revision:adjudicate-{token}"

    def append(payload: dict[str, Any]) -> None:
        actors = _index(payload, "actors")
        relations = _index(payload, "relations")
        events = _index(payload, "events")
        _require_human_author(actors, author_id, "Relation review")
        source = relations.get(relation_id)
        if source is None:
            raise WorkbenchError("The selected relation suggestion does not exist.")
        generators = _index(payload, "generators")
        generator = generators.get(str(source.get("generator_id")))
        if (
            source.get("review_status") != "machine_suggested"
            or source.get("layer") != "normalized_hypothesis"
            or generator is None
            or generator.get("kind") != "machine"
        ):
            raise WorkbenchError("Only normalized machine relation suggestions may be accepted.")
        _require_relation_not_decided(payload, relation_id)
        arguments = source.get("arguments")
        if not isinstance(arguments, list):
            raise WorkbenchError("The selected relation arguments are malformed.")
        accepted_arguments = []
        for argument in arguments:
            if not isinstance(argument, Mapping):
                raise WorkbenchError("The selected relation arguments are malformed.")
            accepted_argument = dict(argument)
            if accepted_argument.get("ref_kind") == "event":
                event_id = accepted_argument.get("ref_id")
                if not isinstance(event_id, str):
                    raise WorkbenchError("The selected relation arguments are malformed.")
                accepted_argument["ref_id"] = _accepted_event_id(events, event_id)
            accepted_arguments.append(accepted_argument)
        generator_id = _ensure_human_generator(payload, author_id)
        payload["relations"].append(
            {
                **source,
                "arguments": accepted_arguments,
                "annotator_id": author_id,
                "confidence": {"kind": "human_review"},
                "generator_id": generator_id,
                "id": accepted_id,
                "layer": "accepted_annotation",
                "review_status": "human_accepted",
            }
        )
        payload["revisions"].extend(
            (
                {
                    "author_id": author_id,
                    "id": source_revision_id,
                    "operation": "supersede",
                    "parent_revision_ids": [],
                    "reason": normalized_reason,
                    "record_id": relation_id,
                    "timestamp": _now(),
                },
                {
                    "author_id": author_id,
                    "id": accepted_revision_id,
                    "operation": "adjudicate",
                    "parent_revision_ids": [],
                    "reason": normalized_reason,
                    "record_id": accepted_id,
                    "timestamp": _now(),
                },
            )
        )

    updated = ProjectStore(project_root).mutate(append, expected_sha256=expected_sha256)
    return WorkbenchMutation(
        (accepted_id,),
        (source_revision_id, accepted_revision_id),
        updated.sha256,
    )


def reject_relation_suggestion(
    project_root: str,
    *,
    relation_id: str,
    author_id: str,
    reason: str,
    expected_sha256: str,
) -> WorkbenchMutation:
    """Append a human rejection revision without mutating machine evidence."""

    _identifier(relation_id, "relation_id")
    _identifier(author_id, "author_id")
    normalized_reason = _bounded_text(reason, "reason", MAX_REVIEW_REASON_CHARS)
    token = uuid4().hex
    revision_id = f"revision:reject-{token}"

    def append(payload: dict[str, Any]) -> None:
        actors = _index(payload, "actors")
        relations = _index(payload, "relations")
        _require_human_author(actors, author_id, "Relation review")
        source = relations.get(relation_id)
        if source is None:
            raise WorkbenchError("The selected relation suggestion does not exist.")
        if source.get("review_status") != "machine_suggested":
            raise WorkbenchError("Only machine relation suggestions may be rejected.")
        _require_relation_not_decided(payload, relation_id)
        payload["revisions"].append(
            {
                "author_id": author_id,
                "id": revision_id,
                "operation": "reject",
                "parent_revision_ids": [],
                "reason": normalized_reason,
                "record_id": relation_id,
                "timestamp": _now(),
            }
        )

    updated = ProjectStore(project_root).mutate(append, expected_sha256=expected_sha256)
    return WorkbenchMutation((), (revision_id,), updated.sha256)


def revise_evidence_annotation(
    project_root: str,
    *,
    event_id: str,
    author_id: str,
    actor_id: str,
    reason: str,
    replacement_text: str,
    expected_sha256: str,
) -> WorkbenchMutation:
    """Append a textual revision and leave the earlier annotation auditable."""

    _identifier(event_id, "event_id")
    _identifier(author_id, "author_id")
    _identifier(actor_id, "actor_id")
    normalized_reason = _bounded_text(reason, "reason", MAX_REVIEW_REASON_CHARS)
    replacement = _bounded_text(
        replacement_text,
        "replacement_text",
        MAX_REPLACEMENT_TEXT_CHARS,
    )
    token = uuid4().hex
    revised_id = f"event:revised-{token}"
    revision_id = f"revision:replace-{token}"

    def append(payload: dict[str, Any]) -> None:
        actors = _index(payload, "actors")
        events = _index(payload, "events")
        _require_human_author(actors, author_id, "Revision")
        if actor_id not in actors:
            raise WorkbenchError("Attributed actor must exist.")
        source = events.get(event_id)
        if source is None:
            raise WorkbenchError("The selected annotation does not exist.")
        if (
            source.get("layer") != "accepted_annotation"
            or source.get("review_status")
            not in {"human_created", "human_accepted"}
        ):
            raise WorkbenchError("Only accepted annotations may be revised.")
        if any(
            isinstance(item.get("body"), Mapping)
            and item["body"].get("source_annotation_id") == event_id
            and item.get("layer") == "accepted_annotation"
            for item in payload["events"]
        ):
            raise WorkbenchError("The selected annotation was already superseded.")
        body = source.get("body")
        if not isinstance(body, Mapping) or not isinstance(body.get("value"), str):
            raise WorkbenchError("Only textual accepted annotations may be revised.")
        revised_body = dict(body)
        revised_body["source_annotation_id"] = event_id
        revised_body["value"] = replacement
        generator_id = _ensure_human_generator(payload, author_id)
        payload["events"].append(
            {
                **source,
                "actor_id": actor_id,
                "body": revised_body,
                "confidence": {"kind": "human_revision"},
                "generator_id": generator_id,
                "id": revised_id,
                "layer": "accepted_annotation",
                "review_status": "human_created",
            }
        )
        parent_revisions = [
            str(item["id"])
            for item in payload["revisions"]
            if item.get("record_id") == event_id
        ]
        payload["revisions"].append(
            {
                "author_id": author_id,
                "id": revision_id,
                "operation": "replace",
                "parent_revision_ids": parent_revisions,
                "reason": normalized_reason,
                "record_id": revised_id,
                "timestamp": _now(),
            }
        )

    updated = ProjectStore(project_root).mutate(
        append,
        expected_sha256=expected_sha256,
    )
    return WorkbenchMutation((revised_id,), (revision_id,), updated.sha256)


def create_exact_time_bookmark(
    project_root: str,
    *,
    source_id: str,
    start_us: int,
    duration_us: int,
    label: str,
    author_id: str,
    expected_sha256: str,
) -> WorkbenchMutation:
    """Append one human-created exact-time bookmark to the evidence graph."""

    _identifier(source_id, "source_id")
    _identifier(author_id, "author_id")
    normalized_label = _bounded_text(label, "label", MAX_BOOKMARK_LABEL_CHARS)
    for value, name in ((start_us, "start_us"), (duration_us, "duration_us")):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise WorkbenchError(f"{name} must be a non-negative integer.")
    token = uuid4().hex
    target_id = f"target:bookmark-{token}"
    event_id = f"event:bookmark-{token}"
    revision_id = f"revision:create-bookmark-{token}"

    def append(payload: dict[str, Any]) -> None:
        actors = _index(payload, "actors")
        sources = _index(payload, "sources")
        _require_human_author(actors, author_id, "Bookmark")
        source = sources.get(source_id)
        if source is None:
            raise WorkbenchError("Bookmark source does not exist.")
        rights_id = str(source["rights_id"])
        generator_id = _ensure_human_generator(payload, author_id)
        payload["targets"].append(
            {
                "alignment_state": "unknown",
                "id": target_id,
                "musical_selector": None,
                "selector": {
                    "duration_us": duration_us,
                    "start_us": start_us,
                    "stream_id": "audio",
                },
                "source_id": source_id,
            }
        )
        payload["events"].append(
            {
                "actor_id": author_id,
                "alternatives": [],
                "body": {
                    "format": "notewitness.bookmark.v1",
                    "value": normalized_label,
                },
                "confidence": {"kind": "human_annotation"},
                "generator_id": generator_id,
                "id": event_id,
                "layer": "accepted_annotation",
                "review_status": "human_created",
                "rights_id": rights_id,
                "scope": "evidence",
                "target_ids": [target_id],
                "type": "local:bookmark",
            }
        )
        payload["revisions"].append(
            {
                "author_id": author_id,
                "id": revision_id,
                "operation": "create",
                "parent_revision_ids": [],
                "reason": "Created exact-time bookmark in the local workbench.",
                "record_id": event_id,
                "timestamp": _now(),
            }
        )

    updated = ProjectStore(project_root).mutate(
        append,
        expected_sha256=expected_sha256,
    )
    return WorkbenchMutation(
        (target_id, event_id),
        (revision_id,),
        updated.sha256,
    )


def set_practice_task_completed(
    project_root: str,
    *,
    task_id: str,
    completed: bool,
    author_id: str,
    expected_sha256: str,
) -> WorkbenchMutation:
    """Append a local completion-state event for one evidence-backed task."""

    _identifier(task_id, "task_id")
    _identifier(author_id, "author_id")
    if not isinstance(completed, bool):
        raise WorkbenchError("completed must be a boolean.")
    store = ProjectStore(project_root)
    snapshot = store.load()
    if snapshot.sha256 != expected_sha256:
        raise WorkbenchError("Project changed before practice update.")
    notes = LessonNotesProjector.project(EvidenceGraph(snapshot.payload))
    matches = [task for task in notes.practice_tasks if task.task_id == task_id]
    if len(matches) != 1:
        raise WorkbenchError("Practice task does not exist or is ambiguous.")
    task = matches[0]
    if not task.rights_ids:
        raise WorkbenchError("Practice task has no rights provenance.")
    rights = _index(snapshot.payload, "rights")
    effective_rights_id = min(
        task.rights_ids,
        key=lambda item: ACCESS_RANK.get(
            str(rights.get(item, {}).get("access")),
            -1,
        ),
    )
    token = uuid4().hex
    event_id = f"event:practice-state-{token}"
    revision_id = f"revision:create-practice-state-{token}"

    def append(payload: dict[str, Any]) -> None:
        _require_human_author(_index(payload, "actors"), author_id, "Practice update")
        generator_id = _ensure_human_generator(payload, author_id)
        payload["events"].append(
            {
                "actor_id": author_id,
                "alternatives": [],
                "body": {
                    "format": "notewitness.practice-state.v1",
                    "value": {"completed": completed, "task_id": task_id},
                },
                "confidence": {"kind": "human_annotation"},
                "generator_id": generator_id,
                "id": event_id,
                "layer": "accepted_annotation",
                "review_status": "human_created",
                "rights_id": effective_rights_id,
                "scope": "project",
                "target_ids": [],
                "type": "local:practice_completion",
            }
        )
        payload["revisions"].append(
            {
                "author_id": author_id,
                "id": revision_id,
                "operation": "create",
                "parent_revision_ids": [],
                "reason": "Updated local practice task completion state.",
                "record_id": event_id,
                "timestamp": _now(),
            }
        )

    updated = store.mutate(append, expected_sha256=expected_sha256)
    return WorkbenchMutation((event_id,), (revision_id,), updated.sha256)


def resolve_media_source(
    project_root: str,
    source_id: str,
) -> tuple[ProjectSnapshot, Mapping[str, Any], PurePosixPath]:
    """Resolve only an ingested project-relative media source."""

    _identifier(source_id, "source_id")
    snapshot = ProjectStore(project_root).load()
    source = _index(snapshot.payload, "sources").get(source_id)
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

    _identifier(author_id, "author_id")
    normalized_name = _bounded_text(
        capture_name,
        "capture_name",
        MAX_BOOKMARK_LABEL_CHARS,
    )
    normalized_type = _bounded_text(content_type, "content_type", 128)
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
        actors = _index(typed_payload, "actors")
        _require_human_author(actors, author_id, "Capture")
        if publication.source_id not in _index(typed_payload, "sources"):
            raise WorkbenchError("Capture source was not published atomically.")
        generator_id = _ensure_human_generator(typed_payload, author_id)
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
                "timestamp": _now(),
            }
        )

    return append


def _ensure_human_generator(payload: dict[str, Any], actor_id: str) -> str:
    digest = hashlib.sha256(actor_id.encode("utf-8")).hexdigest()[:20]
    generator_id = f"generator:workbench-human-{digest}"
    generators = _index(payload, "generators")
    if generator_id not in generators:
        payload["generators"].append(
            {
                "id": generator_id,
                "kind": "human",
                "name": "NoteWitness local workbench",
                "version": "1",
            }
        )
    return generator_id


def _accepted_event_id(
    events: Mapping[str, Mapping[str, Any]], source_event_id: str
) -> str:
    source = events.get(source_event_id)
    if source is not None and (
        source.get("layer") == "accepted_annotation"
        and source.get("review_status") in {"human_accepted", "human_created"}
    ):
        return source_event_id
    matches = [
        event_id
        for event_id, event in events.items()
        if event.get("layer") == "accepted_annotation"
        and event.get("review_status") == "human_accepted"
        and isinstance(event.get("body"), Mapping)
        and event["body"].get("source_suggestion_id") == source_event_id
    ]
    if len(matches) != 1:
        raise WorkbenchError(
            "Accept the transcript evidence for this relation before accepting the relation."
        )
    return matches[0]


def _require_relation_not_decided(payload: Mapping[str, Any], relation_id: str) -> None:
    revisions = payload.get("revisions")
    if not isinstance(revisions, list):
        raise WorkbenchError("Project revisions collection is malformed.")
    if any(
        isinstance(revision, Mapping)
        and revision.get("record_id") == relation_id
        and revision.get("operation") in {"supersede", "reject"}
        for revision in revisions
    ):
        raise WorkbenchError("The selected relation suggestion was already reviewed.")


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


def _index(payload: Mapping[str, Any], name: str) -> dict[str, dict[str, Any]]:
    records = payload.get(name)
    if not isinstance(records, list) or any(
        not isinstance(item, dict) for item in records
    ):
        raise WorkbenchError(f"Project collection {name!r} is malformed.")
    return {str(item["id"]): item for item in records}


def _require_human_author(
    actors: Mapping[str, Mapping[str, Any]],
    actor_id: str,
    action: str,
) -> None:
    actor = actors.get(actor_id)
    if not is_human_evidence_author(actor):
        raise WorkbenchError(f"{action} requires an explicit human project actor.")


def _identifier(value: str, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_IDENTIFIER_CHARS
    ):
        raise WorkbenchError(f"{name} must be a bounded non-empty identifier.")


def _bounded_text(value: str, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkbenchError(f"{name} must be non-empty text.")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise WorkbenchError(f"{name} exceeds {maximum} characters.")
    return normalized


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _url_path_segment(value: str) -> str:
    """Percent-encode UTF-8 without importing a network-capable URL package."""

    unreserved = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    return "".join(
        chr(byte) if byte in unreserved else f"%{byte:02X}"
        for byte in value.encode("utf-8")
    )
