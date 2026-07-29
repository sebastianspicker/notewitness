"""Validation for append-only evidence revision records."""

from __future__ import annotations

from typing import Any, Mapping

from notewitness.evidence_contract import (
    ID_PATTERN,
    REVISION_OPERATIONS,
    ValidationIssue,
)
from notewitness.evidence_validation_common import (
    record_path,
    reject_unknown_fields,
    require_fields,
    validate_datetime,
    validate_id,
)


RecordIndex = Mapping[str, Mapping[str, Any]]


def validate_revisions(
    revisions: RecordIndex,
    all_ids: Mapping[str, str],
    actors: RecordIndex,
    issues: list[ValidationIssue],
) -> None:
    for record_id, record in revisions.items():
        path = record_path("revisions", record_id)
        _validate_revision_shape(record, path, issues)
        _validate_revision_references(record, path, all_ids, actors, issues)
        _validate_revision_parents(
            record.get("parent_revision_ids"), path, revisions, issues
        )
        _validate_revision_values(record, path, issues)


def _validate_revision_shape(
    record: Mapping[str, Any], path: str, issues: list[ValidationIssue]
) -> None:
    reject_unknown_fields(
        record,
        path,
        {
            "id",
            "record_id",
            "parent_revision_ids",
            "author_id",
            "timestamp",
            "operation",
            "reason",
        },
        issues,
    )
    require_fields(
        record,
        path,
        (
            "record_id",
            "parent_revision_ids",
            "author_id",
            "timestamp",
            "operation",
            "reason",
        ),
        issues,
    )


def _validate_revision_references(
    record: Mapping[str, Any],
    path: str,
    all_ids: Mapping[str, str],
    actors: RecordIndex,
    issues: list[ValidationIssue],
) -> None:
    validate_id(record.get("record_id"), f"{path}.record_id", issues)
    validate_id(record.get("author_id"), f"{path}.author_id", issues)
    revised_record_id = record.get("record_id")
    if not isinstance(revised_record_id, str) or revised_record_id not in all_ids:
        issues.append(
            ValidationIssue(f"{path}.record_id", "references an unknown record")
        )
    author_id = record.get("author_id")
    if not isinstance(author_id, str) or author_id not in actors:
        issues.append(ValidationIssue(f"{path}.author_id", "must reference an actor"))


def _validate_revision_parents(
    parents: Any, path: str, revisions: RecordIndex, issues: list[ValidationIssue]
) -> None:
    if not isinstance(parents, list):
        issues.append(
            ValidationIssue(f"{path}.parent_revision_ids", "must be an array")
        )
        return
    seen_parents: set[str] = set()
    for parent_id in parents:
        _validate_revision_parent(parent_id, path, revisions, seen_parents, issues)


def _validate_revision_parent(
    parent_id: Any,
    path: str,
    revisions: RecordIndex,
    seen_parents: set[str],
    issues: list[ValidationIssue],
) -> None:
    parent_path = f"{path}.parent_revision_ids"
    if not isinstance(parent_id, str) or not ID_PATTERN.fullmatch(parent_id):
        issues.append(ValidationIssue(parent_path, "contains an invalid ID"))
        return
    if parent_id in seen_parents:
        issues.append(ValidationIssue(parent_path, f"duplicates {parent_id!r}"))
        return
    seen_parents.add(parent_id)
    if parent_id not in revisions:
        issues.append(ValidationIssue(parent_path, f"unknown revision {parent_id!r}"))


def _validate_revision_values(
    record: Mapping[str, Any], path: str, issues: list[ValidationIssue]
) -> None:
    validate_datetime(record.get("timestamp"), f"{path}.timestamp", issues)
    operation = record.get("operation")
    if not isinstance(operation, str) or operation not in REVISION_OPERATIONS:
        issues.append(ValidationIssue(f"{path}.operation", "has an unknown operation"))
    if not isinstance(record.get("reason"), str):
        issues.append(ValidationIssue(f"{path}.reason", "must be a string"))
