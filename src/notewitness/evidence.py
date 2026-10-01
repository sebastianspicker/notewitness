"""Stable public API for the versioned NoteWitness evidence graph."""

from pathlib import Path

from notewitness.core.evidence.contract import (
    ACCESS_RANK,
    ALIGNMENT_STATES,
    COLLECTIONS,
    CORE_EVENT_TYPES,
    CORE_RELATION_TYPES,
    EVENT_LAYERS,
    EvidenceGraphError,
    GENERATOR_KINDS,
    MAX_JSON_DEPTH,
    MAX_PROJECT_BYTES,
    RELATION_LAYERS,
    REVIEW_STATUSES,
    REVISION_OPERATIONS,
    SCHEMA_VERSION,
    ValidationIssue,
    VISIBILITY_LEVELS,
)
from notewitness.core.evidence.graph import EvidenceGraph as _EvidenceGraph
from notewitness.projects.document import load_payload


class EvidenceGraph(_EvidenceGraph):
    """Public evidence graph type; implementation lives in :mod:`notewitness.core`."""

    @classmethod
    def load(cls, path: str | Path) -> "EvidenceGraph":
        """Load a bounded project document through the project I/O boundary."""

        return cls(load_payload(path))


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
