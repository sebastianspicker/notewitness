"""Qualitative and artistic-research records kept separate from evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


def _required(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty.")


class QueryTarget(StrEnum):
    EVENTS = "events"
    RELATIONS = "relations"
    EPISODES = "episodes"
    CASES = "cases"


@dataclass(frozen=True, slots=True)
class CodeDefinition:
    code_id: str
    label: str
    definition: str
    parent_code_id: str | None = None
    vocabulary_uri: str | None = None

    def __post_init__(self) -> None:
        _required(self.code_id, "code_id")
        _required(self.label, "label")
        _required(self.definition, "definition")
        if self.parent_code_id == self.code_id:
            raise ValueError("A code cannot be its own parent.")


@dataclass(frozen=True, slots=True)
class Codebook:
    codebook_id: str
    title: str
    version: str
    codes: tuple[CodeDefinition, ...]

    def __post_init__(self) -> None:
        _required(self.codebook_id, "codebook_id")
        _required(self.title, "title")
        _required(self.version, "version")
        ids = tuple(code.code_id for code in self.codes)
        if len(ids) != len(set(ids)):
            raise ValueError("Code IDs must be unique within a codebook.")


@dataclass(frozen=True, slots=True)
class ResearchCase:
    case_id: str
    label: str
    project_ids: tuple[str, ...]
    actor_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required(self.case_id, "case_id")
        _required(self.label, "label")


@dataclass(frozen=True, slots=True)
class ResearchMemo:
    memo_id: str
    author_id: str
    body: str
    linked_record_ids: tuple[str, ...]
    revision_parent_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required(self.memo_id, "memo_id")
        _required(self.author_id, "author_id")
        _required(self.body, "body")


@dataclass(frozen=True, slots=True)
class ResearchQuery:
    query_id: str
    target: QueryTarget
    relation_types: tuple[str, ...] = ()
    actor_roles: tuple[str, ...] = ()
    code_ids: tuple[str, ...] = ()
    include_contested: bool = True

    def __post_init__(self) -> None:
        _required(self.query_id, "query_id")

