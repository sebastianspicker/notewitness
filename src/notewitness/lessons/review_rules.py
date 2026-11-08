"""Shared rules and value types for append-only human evidence review."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Any

from notewitness.core.evidence.contract import is_human_evidence_author


MAX_BOOKMARK_LABEL_CHARS = 1_000
MAX_REVIEW_REASON_CHARS = 4_000
MAX_REPLACEMENT_TEXT_CHARS = 20_000
MAX_IDENTIFIER_CHARS = 256
MAX_CAPTURE_DURATION_MS = 2 * 60 * 60 * 1_000


class ReviewError(RuntimeError):
    """A local evidence-review operation violated the project contract."""


@dataclass(frozen=True, slots=True)
class ReviewMutation:
    record_ids: tuple[str, ...]
    revision_ids: tuple[str, ...]
    project_sha256: str


def ensure_human_generator(payload: dict[str, Any], actor_id: str) -> str:
    """Return the deterministic local human generator, appending it if needed."""

    digest = hashlib.sha256(actor_id.encode("utf-8")).hexdigest()[:20]
    generator_id = f"generator:workbench-human-{digest}"
    generators = index(payload, "generators")
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


def index(payload: Mapping[str, Any], name: str) -> dict[str, dict[str, Any]]:
    records = payload.get(name)
    if not isinstance(records, list) or any(
        not isinstance(item, dict) for item in records
    ):
        raise ReviewError(f"Project collection {name!r} is malformed.")
    return {str(item["id"]): item for item in records}


def require_human_author(
    actors: Mapping[str, Mapping[str, Any]],
    actor_id: str,
    action: str,
) -> None:
    actor = actors.get(actor_id)
    if not is_human_evidence_author(actor):
        raise ReviewError(f"{action} requires an explicit human project actor.")


def identifier(value: str, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_IDENTIFIER_CHARS
    ):
        raise ReviewError(f"{name} must be a bounded non-empty identifier.")


def bounded_text(value: str, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewError(f"{name} must be non-empty text.")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ReviewError(f"{name} exceeds {maximum} characters.")
    if "\x00" in normalized:
        raise ReviewError(f"{name} must not contain NUL characters.")
    return normalized


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_normalized_machine_suggestion(
    source: Mapping[str, Any],
    generators: Mapping[str, Mapping[str, Any]],
) -> bool:
    """Return whether a record meets the shared acceptance predicate exactly."""

    generator = generators.get(str(source.get("generator_id")))
    return bool(
        source.get("review_status") == "machine_suggested"
        and source.get("layer") == "normalized_hypothesis"
        and generator is not None
        and generator.get("kind") == "machine"
    )


def reviewed_record_ids(
    revisions: Iterable[Mapping[str, Any]], *operations: str
) -> set[str]:
    """Return IDs of records that carry a revision with one of the operations."""

    return {
        str(revision["record_id"])
        for revision in revisions
        if revision.get("operation") in operations
        and isinstance(revision.get("record_id"), str)
    }


def latest_event_ids(events: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """Map each revised accepted event ID to the ID of its latest revision."""

    successor: dict[str, str] = {}
    for event_id, event in events.items():
        body = event.get("body")
        if (
            event.get("layer") == "accepted_annotation"
            and event.get("review_status") in {"human_created", "human_accepted"}
            and isinstance(body, Mapping)
            and isinstance(body.get("source_annotation_id"), str)
        ):
            successor[body["source_annotation_id"]] = str(event_id)
    latest: dict[str, str] = {}
    for old_id in successor:
        current, seen = old_id, {old_id}
        while current in successor and successor[current] not in seen:
            current = successor[current]
            seen.add(current)
        latest[old_id] = current
    return latest


def require_evidence_not_decided(payload: Mapping[str, Any], event_id: str) -> None:
    events = index(payload, "events")
    if any(
        event.get("review_status") == "human_accepted"
        and isinstance(event.get("body"), Mapping)
        and event["body"].get("source_suggestion_id") == event_id
        for event in events.values()
    ):
        raise ReviewError("The selected evidence suggestion was already reviewed.")
    if event_id in reviewed_record_ids(index(payload, "revisions").values(), "reject"):
        raise ReviewError("The selected evidence suggestion was already reviewed.")
