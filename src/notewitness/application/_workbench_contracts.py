"""Shared private contracts for the workbench compatibility facade."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Any, Mapping

from notewitness.application.actor_eligibility import is_human_evidence_author


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
        raise WorkbenchError(f"Project collection {name!r} is malformed.")
    return {str(item["id"]): item for item in records}


def require_human_author(
    actors: Mapping[str, Mapping[str, Any]],
    actor_id: str,
    action: str,
) -> None:
    actor = actors.get(actor_id)
    if not is_human_evidence_author(actor):
        raise WorkbenchError(f"{action} requires an explicit human project actor.")


def identifier(value: str, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_IDENTIFIER_CHARS
    ):
        raise WorkbenchError(f"{name} must be a bounded non-empty identifier.")


def bounded_text(value: str, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkbenchError(f"{name} must be non-empty text.")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise WorkbenchError(f"{name} exceeds {maximum} characters.")
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
