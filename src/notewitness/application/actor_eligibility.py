"""Shared policy for actors allowed to create human evidence.

Actor roles describe a project participant, not an authentication mechanism.  This
small policy prevents reserved non-human and unresolved roles from being used as
the author of append-only human evidence while preserving legitimate, project-
specific roles such as ``music analysis researcher``.
"""

from __future__ import annotations

from typing import Any, Mapping


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
