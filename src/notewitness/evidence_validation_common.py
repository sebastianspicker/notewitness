"""Shared primitives for dependency-free evidence-graph validation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from notewitness.evidence_contract import (
    ACCESS_RANK,
    ID_PATTERN,
    ValidationIssue,
)


def require_fields(
    record: Mapping[str, Any],
    path: str,
    fields: Iterable[str],
    issues: list[ValidationIssue],
) -> None:
    for field in fields:
        if field not in record:
            issues.append(ValidationIssue(f"{path}.{field}", "is required"))


def reject_unknown_fields(
    record: Mapping[str, Any],
    path: str,
    allowed: set[str],
    issues: list[ValidationIssue],
) -> None:
    for field in sorted(set(record) - allowed, key=str):
        issues.append(
            ValidationIssue(f"{path}.{field}", "is not allowed by the contract")
        )


def validate_id(
    value: object, path: str, issues: list[ValidationIssue]
) -> None:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        issues.append(ValidationIssue(path, "must be a stable ID"))


def require_non_empty_string(
    value: object, path: str, issues: list[ValidationIssue]
) -> None:
    if not isinstance(value, str) or not value:
        issues.append(ValidationIssue(path, "must be a non-empty string"))


def validate_datetime(
    value: object, path: str, issues: list[ValidationIssue]
) -> None:
    if not isinstance(value, str):
        issues.append(ValidationIssue(path, "must be an RFC 3339 date-time"))
        return
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        issues.append(ValidationIssue(path, "must be an RFC 3339 date-time"))
        return
    if parsed.tzinfo is None:
        issues.append(ValidationIssue(path, "must include a UTC offset"))


def validate_confidence(
    value: object, path: str, issues: list[ValidationIssue]
) -> None:
    if not isinstance(value, dict):
        issues.append(ValidationIssue(path, "must be an object"))
        return
    require_fields(value, path, ("kind",), issues)
    require_non_empty_string(value.get("kind"), f"{path}.kind", issues)


def record_path(collection: str, record_id: str) -> str:
    return f"$.{collection}[id={record_id!r}]"


def rights_are_broader(
    child: Mapping[str, Any] | None,
    parent: Mapping[str, Any] | None,
) -> bool:
    if not child or not parent:
        return False
    child_access = ACCESS_RANK.get(str(child.get("access")), -1)
    parent_access = ACCESS_RANK.get(str(parent.get("access")), -1)
    return (
        child_access > parent_access
        or (
            child.get("remote_processing") is True
            and parent.get("remote_processing") is not True
        )
        or (
            child.get("model_training") is True
            and parent.get("model_training") is not True
        )
        or child.get("retention") != parent.get("retention")
    )
