"""Stable public façade for NoteWitness evidence-graph contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from notewitness.evidence_collections import (
    index as index_collection,
    records as collection_records,
    selected_events_allow_remote as payload_events_allow_remote,
)
from notewitness.evidence_contract import (
    ACCESS_RANK,
    ALIGNMENT_STATES,
    COLLECTIONS,
    CORE_EVENT_TYPES,
    CORE_RELATION_TYPES,
    EVENT_LAYERS,
    GENERATOR_KINDS,
    MAX_JSON_DEPTH,
    MAX_PROJECT_BYTES,
    RELATION_LAYERS,
    REVIEW_STATUSES,
    REVISION_OPERATIONS,
    SCHEMA_VERSION,
    VISIBILITY_LEVELS,
    EvidenceGraphError,
    ValidationIssue,
)
from notewitness.evidence_loader import load_payload
from notewitness.evidence_validation import validate_payload
from notewitness.network import NetworkPolicy


__all__ = [
    "ACCESS_RANK",
    "ALIGNMENT_STATES",
    "COLLECTIONS",
    "CORE_EVENT_TYPES",
    "CORE_RELATION_TYPES",
    "EVENT_LAYERS",
    "EvidenceGraph",
    "EvidenceGraphError",
    "GENERATOR_KINDS",
    "MAX_JSON_DEPTH",
    "MAX_PROJECT_BYTES",
    "RELATION_LAYERS",
    "REVIEW_STATUSES",
    "REVISION_OPERATIONS",
    "SCHEMA_VERSION",
    "VISIBILITY_LEVELS",
    "ValidationIssue",
]


class EvidenceGraph:
    """Read-only view and validation entrypoint for one project payload."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = dict(payload)

    @classmethod
    def load(cls, path: str | Path) -> "EvidenceGraph":
        return cls(load_payload(path))

    def network_policy(self) -> NetworkPolicy:
        return NetworkPolicy.from_mapping(self.payload.get("network"))

    def records(self, collection: str) -> tuple[dict[str, Any], ...]:
        return collection_records(self.payload, collection)

    def index(self, collection: str) -> dict[str, dict[str, Any]]:
        return index_collection(self.payload, collection)

    def require_valid(self) -> None:
        issues = self.validate()
        if issues:
            raise EvidenceGraphError(issues)

    def validate(self) -> tuple[ValidationIssue, ...]:
        return validate_payload(self.payload)

    def selected_events_allow_remote(self, event_ids: Iterable[str]) -> bool:
        return payload_events_allow_remote(self.payload, event_ids)
