"""Shared constants and errors for the NoteWitness evidence graph."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


SCHEMA_VERSION = "0.1.0"
MAX_PROJECT_BYTES = 8 * 1024 * 1024
MAX_JSON_DEPTH = 64
COLLECTIONS = (
    "rights",
    "sources",
    "actors",
    "targets",
    "generators",
    "events",
    "relations",
    "revisions",
)
CORE_RELATION_TYPES = frozenset(
    {
        "demonstrates",
        "attempts",
        "feedback_on",
        "refers_to",
        "repeats",
        "revises",
        "contrasts_with",
    }
)
CORE_EVENT_TYPES = frozenset(
    {
        "gesture",
        "music",
        "score_reference",
        "silence",
        "speech",
        "speech_over_music",
        "sung_or_hummed",
    }
)
REVIEW_STATUSES = frozenset(
    {
        "machine_suggested",
        "human_accepted",
        "human_created",
        "rejected",
        "contested",
    }
)
ALIGNMENT_STATES = frozenset(
    {"aligned", "unknown", "not_detected", "not_applicable", "not_alignable"}
)
ACCESS_RANK = {"restricted": 0, "project": 1, "public": 2}
VISIBILITY_LEVELS = frozenset(ACCESS_RANK)
GENERATOR_KINDS = frozenset({"human", "machine", "import"})
EVENT_LAYERS = frozenset(
    {
        "raw_model_output",
        "normalized_hypothesis",
        "accepted_annotation",
        "presentation",
    }
)
RELATION_LAYERS = frozenset(
    {"normalized_hypothesis", "accepted_annotation", "presentation"}
)
REVISION_OPERATIONS = frozenset(
    {"create", "replace", "supersede", "reject", "adjudicate"}
)
ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]*$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
LOCAL_TYPE_PATTERN = re.compile(r"^local:[A-Za-z][A-Za-z0-9._-]*$")


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


class EvidenceGraphError(ValueError):
    def __init__(self, issues: Iterable[ValidationIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(str(issue) for issue in self.issues))


# Preserve the public import/pickle path after extracting the implementations.
ValidationIssue.__module__ = "notewitness.evidence"
EvidenceGraphError.__module__ = "notewitness.evidence"
