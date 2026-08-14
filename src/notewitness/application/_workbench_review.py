"""Append-only evidence and relation review operations for the workbench."""

from __future__ import annotations

from typing import Any, Mapping
from uuid import uuid4

from notewitness.application._workbench_contracts import (
    MAX_BOOKMARK_LABEL_CHARS,
    MAX_REPLACEMENT_TEXT_CHARS,
    MAX_REVIEW_REASON_CHARS,
    WorkbenchError,
    WorkbenchMutation,
    bounded_text,
    ensure_human_generator,
    identifier,
    index,
    is_normalized_machine_suggestion,
    now,
    require_human_author,
)
from notewitness.application.lesson_notes import LessonNotesProjector
from notewitness.evidence import ACCESS_RANK, EvidenceGraph
from notewitness.project_store import ProjectStore


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

    identifier(event_id, "event_id")
    identifier(author_id, "author_id")
    identifier(actor_id, "actor_id")
    normalized_reason = bounded_text(reason, "reason", MAX_REVIEW_REASON_CHARS)
    replacement = (
        bounded_text(
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
        actors = index(payload, "actors")
        events = index(payload, "events")
        generators = index(payload, "generators")
        require_human_author(actors, author_id, "Review")
        if actor_id not in actors:
            raise WorkbenchError("Attributed actor must exist.")
        source = events.get(event_id)
        if source is None:
            raise WorkbenchError("The selected evidence suggestion does not exist.")
        if not is_normalized_machine_suggestion(source, generators):
            raise WorkbenchError(
                "Only normalized machine suggestions may be accepted."
            )
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
        _append_accepted_event(
            payload,
            source,
            accepted_body,
            actor_id,
            author_id,
            accepted_id,
        )
        _append_adjudication_revision(
            payload,
            author_id,
            revision_id,
            normalized_reason,
            accepted_id,
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
    """Append a human-accepted relation using already reviewed event anchors."""

    identifier(relation_id, "relation_id")
    identifier(author_id, "author_id")
    normalized_reason = bounded_text(reason, "reason", MAX_REVIEW_REASON_CHARS)
    token = uuid4().hex
    accepted_id = f"relation:accepted-{token}"
    source_revision_id = f"revision:supersede-{token}"
    accepted_revision_id = f"revision:adjudicate-{token}"

    def append(payload: dict[str, Any]) -> None:
        actors = index(payload, "actors")
        relations = index(payload, "relations")
        events = index(payload, "events")
        require_human_author(actors, author_id, "Relation review")
        source = relations.get(relation_id)
        if source is None:
            raise WorkbenchError("The selected relation suggestion does not exist.")
        if not is_normalized_machine_suggestion(
            source,
            index(payload, "generators"),
        ):
            raise WorkbenchError(
                "Only normalized machine relation suggestions may be accepted."
            )
        _require_relation_not_decided(payload, relation_id)
        arguments = source.get("arguments")
        if not isinstance(arguments, list):
            raise WorkbenchError("The selected relation arguments are malformed.")
        accepted_arguments = []
        for argument in arguments:
            if not isinstance(argument, Mapping):
                raise WorkbenchError(
                    "The selected relation arguments are malformed."
                )
            accepted_argument = dict(argument)
            if accepted_argument.get("ref_kind") == "event":
                event_id = accepted_argument.get("ref_id")
                if not isinstance(event_id, str):
                    raise WorkbenchError(
                        "The selected relation arguments are malformed."
                    )
                accepted_argument["ref_id"] = _accepted_event_id(events, event_id)
            accepted_arguments.append(accepted_argument)
        generator_id = ensure_human_generator(payload, author_id)
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
        payload["revisions"].append(
            {
                "author_id": author_id,
                "id": source_revision_id,
                "operation": "supersede",
                "parent_revision_ids": [],
                "reason": normalized_reason,
                "record_id": relation_id,
                "timestamp": now(),
            }
        )
        _append_adjudication_revision(
            payload,
            author_id,
            accepted_revision_id,
            normalized_reason,
            accepted_id,
        )

    updated = ProjectStore(project_root).mutate(
        append,
        expected_sha256=expected_sha256,
    )
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

    identifier(relation_id, "relation_id")
    identifier(author_id, "author_id")
    normalized_reason = bounded_text(reason, "reason", MAX_REVIEW_REASON_CHARS)
    token = uuid4().hex
    revision_id = f"revision:reject-{token}"

    def append(payload: dict[str, Any]) -> None:
        actors = index(payload, "actors")
        relations = index(payload, "relations")
        require_human_author(actors, author_id, "Relation review")
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
                "timestamp": now(),
            }
        )

    updated = ProjectStore(project_root).mutate(
        append,
        expected_sha256=expected_sha256,
    )
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

    identifier(event_id, "event_id")
    identifier(author_id, "author_id")
    identifier(actor_id, "actor_id")
    normalized_reason = bounded_text(reason, "reason", MAX_REVIEW_REASON_CHARS)
    replacement = bounded_text(
        replacement_text,
        "replacement_text",
        MAX_REPLACEMENT_TEXT_CHARS,
    )
    token = uuid4().hex
    revised_id = f"event:revised-{token}"
    revision_id = f"revision:replace-{token}"

    def append(payload: dict[str, Any]) -> None:
        actors = index(payload, "actors")
        events = index(payload, "events")
        require_human_author(actors, author_id, "Revision")
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
        generator_id = ensure_human_generator(payload, author_id)
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
                "timestamp": now(),
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

    identifier(source_id, "source_id")
    identifier(author_id, "author_id")
    normalized_label = bounded_text(label, "label", MAX_BOOKMARK_LABEL_CHARS)
    for value, name in ((start_us, "start_us"), (duration_us, "duration_us")):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise WorkbenchError(f"{name} must be a non-negative integer.")
    token = uuid4().hex
    target_id = f"target:bookmark-{token}"
    event_id = f"event:bookmark-{token}"
    revision_id = f"revision:create-bookmark-{token}"

    def append(payload: dict[str, Any]) -> None:
        actors = index(payload, "actors")
        sources = index(payload, "sources")
        require_human_author(actors, author_id, "Bookmark")
        source = sources.get(source_id)
        if source is None:
            raise WorkbenchError("Bookmark source does not exist.")
        generator_id = ensure_human_generator(payload, author_id)
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
                "rights_id": str(source["rights_id"]),
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
                "timestamp": now(),
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

    identifier(task_id, "task_id")
    identifier(author_id, "author_id")
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
    rights = index(snapshot.payload, "rights")
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
        require_human_author(
            index(payload, "actors"),
            author_id,
            "Practice update",
        )
        generator_id = ensure_human_generator(payload, author_id)
        payload["events"].append(
            {
                "actor_id": author_id,
                "alternatives": [],
                "body": {
                    "format": "notewitness.practice-state.v1",
                    "value": {
                        "completed": completed,
                        "task_id": task_id,
                    },
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
                "timestamp": now(),
            }
        )

    updated = store.mutate(append, expected_sha256=expected_sha256)
    return WorkbenchMutation((event_id,), (revision_id,), updated.sha256)


def _append_accepted_event(
    payload: dict[str, Any],
    source: Mapping[str, Any],
    accepted_body: dict[str, Any],
    actor_id: str,
    author_id: str,
    accepted_id: str,
) -> None:
    generator_id = ensure_human_generator(payload, author_id)
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


def _append_adjudication_revision(
    payload: dict[str, Any],
    author_id: str,
    revision_id: str,
    reason: str,
    record_id: str,
) -> None:
    payload["revisions"].append(
        {
            "author_id": author_id,
            "id": revision_id,
            "operation": "adjudicate",
            "parent_revision_ids": [],
            "reason": reason,
            "record_id": record_id,
            "timestamp": now(),
        }
    )


def _accepted_event_id(
    events: Mapping[str, Mapping[str, Any]],
    source_event_id: str,
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
            "Accept the transcript evidence for this relation before accepting "
            "the relation."
        )
    return matches[0]


def _require_relation_not_decided(
    payload: Mapping[str, Any],
    relation_id: str,
) -> None:
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
