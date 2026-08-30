"""Shared constants and errors for the NoteWitness evidence graph."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Iterable, Mapping


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


class NetworkMode(StrEnum):
    """Operator-selected network authority recorded in the evidence graph."""

    OFFLINE = "offline"
    DOWNLOAD_MODELS_ONLY = "download_models_only"
    REMOTE_EXPLICIT = "remote_explicit"


class NetworkAccessDenied(RuntimeError):
    """Remote work was requested without the evidence graph's explicit authority."""


@dataclass(frozen=True, slots=True)
class NetworkPolicy:
    """Pure network-authority contract independent of any transport adapter."""

    mode: NetworkMode = NetworkMode.OFFLINE

    @classmethod
    def from_mapping(cls, value: object) -> "NetworkPolicy":
        if not isinstance(value, Mapping):
            return cls()
        raw_mode = value.get("mode", NetworkMode.OFFLINE.value)
        try:
            return cls(NetworkMode(str(raw_mode)))
        except ValueError:
            return cls()

    def require_remote_inference(
        self, *, confirmed: bool, rights_allow_remote: bool
    ) -> None:
        if self.mode is not NetworkMode.REMOTE_EXPLICIT:
            raise NetworkAccessDenied(
                "Remote inference requires project network mode 'remote_explicit'."
            )
        if not confirmed:
            raise NetworkAccessDenied(
                "Remote inference requires the explicit --allow-remote flag."
            )
        if not rights_allow_remote:
            raise NetworkAccessDenied(
                "Every selected event and source must allow remote processing."
            )


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
