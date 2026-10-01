"""Project actor creation and the policy for actors allowed to create human evidence.

Actor roles describe a project participant, not an authentication mechanism.  This
small policy prevents reserved non-human and unresolved roles from being used as
the author of append-only human evidence while preserving legitimate, project-
specific roles such as ``music analysis researcher``.
"""

from __future__ import annotations

from typing import Any, Mapping

from notewitness.projects.store import ProjectSnapshot, ProjectStore


MAX_ACTOR_FIELD_CHARS = 256


class TranscriptReviewError(RuntimeError):
    pass


INELIGIBLE_HUMAN_EVIDENCE_ROLES = frozenset(
    {
        "",
        "analysis",
        "machine",
        "system",
        "unattributed",
        "unknown",
    }
)


def is_human_evidence_author(actor: Mapping[str, Any] | None) -> bool:
    """Return whether an explicit project actor may author human evidence."""

    if actor is None:
        return False
    role = actor.get("role")
    return (
        isinstance(role, str)
        and role.strip().casefold() not in INELIGIBLE_HUMAN_EVIDENCE_ROLES
    )


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
        actors = payload.get("actors")
        if not isinstance(actors, list) or any(
            not isinstance(item, dict) for item in actors
        ):
            raise TranscriptReviewError("Project collection 'actors' is malformed.")
        if any(item.get("id") == actor_id for item in actors):
            raise TranscriptReviewError("Actor ID already exists in this project.")
        actors.append(actor)

    return ProjectStore(project_root).mutate(append, expected_sha256=expected_sha256)
