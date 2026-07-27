"""Human actor setup and append-only acceptance of transcript suggestions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Any, Mapping
from uuid import uuid4

from notewitness.application.actor_eligibility import is_human_evidence_author
from notewitness.project_store import ProjectSnapshot, ProjectStore


MAX_REVIEW_REASON_CHARS = 4_000
MAX_REPLACEMENT_TEXT_CHARS = 20_000
MAX_ACTOR_FIELD_CHARS = 256


class TranscriptReviewError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TranscriptReviewDecision:
    event_id: str
    replacement_text: str | None = None
    actor_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.event_id, str)
            or not self.event_id
            or len(self.event_id) > MAX_ACTOR_FIELD_CHARS
        ):
            raise ValueError("Review decisions require a machine event ID.")
        if self.actor_id is not None and (
            not isinstance(self.actor_id, str)
            or not self.actor_id
            or len(self.actor_id) > MAX_ACTOR_FIELD_CHARS
        ):
            raise ValueError("Review speaker IDs must be bounded non-empty strings.")
        if self.replacement_text is not None:
            if (
                not isinstance(self.replacement_text, str)
                or not self.replacement_text.strip()
            ):
                raise ValueError("Replacement transcript text must not be empty.")
            if len(self.replacement_text) > MAX_REPLACEMENT_TEXT_CHARS:
                raise ValueError(
                    f"Replacement transcript text exceeds {MAX_REPLACEMENT_TEXT_CHARS} characters."
                )


@dataclass(frozen=True, slots=True)
class TranscriptReviewResult:
    accepted_event_ids: tuple[str, ...]
    revision_ids: tuple[str, ...]
    project_sha256: str


def add_project_actor(
    project_root: str,
    *,
    actor_id: str,
    role: str,
    visibility: str = "restricted",
    instrument_role: str | None = None,
    expected_sha256: str | None = None,
) -> ProjectSnapshot:
    if not isinstance(actor_id, str) or not isinstance(role, str):
        raise TranscriptReviewError("Actor ID and role must be strings.")
    if not actor_id or not role.strip():
        raise TranscriptReviewError("Actor ID and role must not be empty.")
    if len(actor_id) > MAX_ACTOR_FIELD_CHARS or len(role) > MAX_ACTOR_FIELD_CHARS:
        raise TranscriptReviewError("Actor ID and role must be bounded.")
    if visibility not in {"restricted", "project", "public"}:
        raise TranscriptReviewError("Actor visibility is invalid.")
    actor: dict[str, Any] = {
        "id": actor_id,
        "role": role.strip(),
        "visibility": visibility,
    }
    if instrument_role is not None:
        if not isinstance(instrument_role, str) or not instrument_role.strip():
            raise TranscriptReviewError("Instrument role must not be empty.")
        if len(instrument_role) > MAX_ACTOR_FIELD_CHARS:
            raise TranscriptReviewError("Instrument role must be bounded.")
        actor["instrument_role"] = instrument_role.strip()

    def append(payload: dict[str, Any]) -> None:
        actors = _collection(payload, "actors")
        if any(item.get("id") == actor_id for item in actors):
            raise TranscriptReviewError("Actor ID already exists in this project.")
        actors.append(actor)

    return ProjectStore(project_root).mutate(append, expected_sha256=expected_sha256)


def accept_transcript_events(
    project_root: str,
    *,
    decisions: tuple[TranscriptReviewDecision, ...],
    author_id: str,
    reason: str,
) -> TranscriptReviewResult:
    if not decisions or len({item.event_id for item in decisions}) != len(decisions):
        raise TranscriptReviewError("Review decisions must be non-empty and unique.")
    if not isinstance(author_id, str) or not isinstance(reason, str):
        raise TranscriptReviewError("Review acceptance requires author and reason.")
    normalized_reason = reason.strip()
    if (
        not author_id
        or len(author_id) > MAX_ACTOR_FIELD_CHARS
        or not normalized_reason
    ):
        raise TranscriptReviewError("Review acceptance requires author and reason.")
    if len(normalized_reason) > MAX_REVIEW_REASON_CHARS:
        raise TranscriptReviewError(
            f"Review reason exceeds {MAX_REVIEW_REASON_CHARS} characters."
        )
    accepted_ids: list[str] = []
    revision_ids: list[str] = []

    def append(payload: dict[str, Any]) -> None:
        actors = {item["id"]: item for item in _collection(payload, "actors")}
        if not is_human_evidence_author(actors.get(author_id)):
            raise TranscriptReviewError(
                "Review requires an explicit human project actor."
            )
        events = _collection(payload, "events")
        events_by_id = {item["id"]: item for item in events}
        generators = {item["id"]: item for item in _collection(payload, "generators")}
        already_accepted = {
            body.get("source_suggestion_id")
            for event in events
            if event.get("review_status") == "human_accepted"
            and isinstance((body := event.get("body")), dict)
        }
        human_generator_id = _human_generator_id(author_id)
        if human_generator_id not in generators:
            _collection(payload, "generators").append(
                {
                    "id": human_generator_id,
                    "kind": "human",
                    "name": "Local transcript review",
                    "version": "1",
                }
            )
        for decision in decisions:
            source = events_by_id.get(decision.event_id)
            if source is None:
                raise TranscriptReviewError(
                    f"Machine transcript event {decision.event_id!r} was not found."
                )
            generator = generators.get(str(source.get("generator_id")))
            if (
                source.get("type") != "speech"
                or source.get("review_status") != "machine_suggested"
                or source.get("layer") != "normalized_hypothesis"
                or generator is None
                or generator.get("kind") != "machine"
            ):
                raise TranscriptReviewError(
                    "Only normalized machine speech suggestions can be accepted."
                )
            if decision.event_id in already_accepted:
                raise TranscriptReviewError("Transcript suggestion was already accepted.")
            effective_actor_id = decision.actor_id or str(source.get("actor_id"))
            if effective_actor_id not in actors:
                raise TranscriptReviewError("Accepted speaker is not a project actor.")
            source_body = source.get("body")
            if not isinstance(source_body, dict):
                raise TranscriptReviewError("Transcript suggestion body is malformed.")
            body = dict(source_body)
            body["source_suggestion_id"] = decision.event_id
            if decision.replacement_text is not None:
                body["value"] = decision.replacement_text.strip()
            token = uuid4().hex
            accepted_id = f"event:accepted-{token}"
            revision_id = f"revision:adjudicate-{token}"
            events.append(
                {
                    **source,
                    "id": accepted_id,
                    "actor_id": effective_actor_id,
                    "body": body,
                    "generator_id": human_generator_id,
                    "layer": "accepted_annotation",
                    "confidence": {"kind": "human_review"},
                    "review_status": "human_accepted",
                }
            )
            _collection(payload, "revisions").append(
                {
                    "id": revision_id,
                    "record_id": accepted_id,
                    "parent_revision_ids": [],
                    "author_id": author_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "operation": "adjudicate",
                    "reason": normalized_reason,
                }
            )
            accepted_ids.append(accepted_id)
            revision_ids.append(revision_id)

    snapshot = ProjectStore(project_root).mutate(append)
    return TranscriptReviewResult(
        accepted_event_ids=tuple(accepted_ids),
        revision_ids=tuple(revision_ids),
        project_sha256=snapshot.sha256,
    )


def _human_generator_id(author_id: str) -> str:
    digest = hashlib.sha256(author_id.encode("utf-8")).hexdigest()[:20]
    return f"generator:human-review-{digest}"


def _collection(payload: Mapping[str, Any], name: str) -> list[dict[str, Any]]:
    value = payload.get(name)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise TranscriptReviewError(f"Project collection {name!r} is malformed.")
    return value
