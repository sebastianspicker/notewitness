"""Validation for evidence event records."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from notewitness.evidence_contract import (
    CORE_EVENT_TYPES,
    EVENT_LAYERS,
    ID_PATTERN,
    LOCAL_TYPE_PATTERN,
    REVIEW_STATUSES,
    ValidationIssue,
)
from notewitness.evidence_validation_common import (
    record_path,
    reject_unknown_fields,
    require_fields,
    require_non_empty_string,
    rights_are_broader,
    validate_confidence,
    validate_id,
)

RecordIndex = Mapping[str, Mapping[str, Any]]


def validate_events(
    events: RecordIndex,
    actors: RecordIndex,
    targets: RecordIndex,
    generators: RecordIndex,
    rights: RecordIndex,
    sources: RecordIndex,
    issues: list[ValidationIssue],
) -> None:
    for record_id, record in events.items():
        path = record_path("events", record_id)
        _validate_event_shape(record, path, issues)
        _validate_event_type(record, path, issues)
        _validate_event_references(record, path, actors, generators, rights, issues)
        _validate_event_targets(record, path, targets, rights, sources, issues)
        _validate_event_body(record.get("body"), path, issues)
        _validate_event_values(record, path, issues)


def _validate_event_shape(
    record: Mapping[str, Any], path: str, issues: list[ValidationIssue]
) -> None:
    fields = {
        "id",
        "type",
        "scope",
        "actor_id",
        "target_ids",
        "body",
        "alternatives",
        "generator_id",
        "rights_id",
        "layer",
        "confidence",
        "review_status",
    }
    reject_unknown_fields(record, path, fields, issues)
    require_fields(
        record,
        path,
        (
            "type",
            "scope",
            "actor_id",
            "target_ids",
            "body",
            "alternatives",
            "generator_id",
            "rights_id",
            "layer",
            "confidence",
            "review_status",
        ),
        issues,
    )


def _validate_event_type(
    record: Mapping[str, Any], path: str, issues: list[ValidationIssue]
) -> None:
    event_type = record.get("type")
    if not isinstance(event_type, str) or (
        event_type not in CORE_EVENT_TYPES
        and not LOCAL_TYPE_PATTERN.fullmatch(event_type)
    ):
        issues.append(
            ValidationIssue(
                f"{path}.type", "must use a core or namespaced local event type"
            )
        )


def _validate_event_references(
    record: Mapping[str, Any],
    path: str,
    actors: RecordIndex,
    generators: RecordIndex,
    rights: RecordIndex,
    issues: list[ValidationIssue],
) -> None:
    for field in ("actor_id", "generator_id", "rights_id"):
        validate_id(record.get(field), f"{path}.{field}", issues)
    _require_reference(
        record.get("actor_id"),
        actors,
        f"{path}.actor_id",
        "must reference an actor",
        issues,
    )
    _require_reference(
        record.get("generator_id"),
        generators,
        f"{path}.generator_id",
        "must reference a generator",
        issues,
    )
    _require_reference(
        record.get("rights_id"),
        rights,
        f"{path}.rights_id",
        "must reference rights",
        issues,
    )


def _require_reference(
    value: Any,
    records: RecordIndex,
    path: str,
    message: str,
    issues: list[ValidationIssue],
) -> None:
    if not isinstance(value, str) or value not in records:
        issues.append(ValidationIssue(path, message))


def _validate_event_targets(
    record: Mapping[str, Any],
    path: str,
    targets: RecordIndex,
    rights: RecordIndex,
    sources: RecordIndex,
    issues: list[ValidationIssue],
) -> None:
    target_ids = record.get("target_ids")
    if not isinstance(target_ids, list):
        issues.append(ValidationIssue(f"{path}.target_ids", "must be an array"))
        return
    _validate_event_target_scope(record.get("scope"), target_ids, path, issues)
    seen_target_ids: set[str] = set()
    for target_id in target_ids:
        _validate_event_target(
            target_id, record, path, targets, rights, sources, seen_target_ids, issues
        )


def _validate_event_target_scope(
    scope: Any, target_ids: list[Any], path: str, issues: list[ValidationIssue]
) -> None:
    if scope == "evidence" and not target_ids:
        issues.append(
            ValidationIssue(
                f"{path}.target_ids", "evidence events require at least one target"
            )
        )
    if scope == "project" and target_ids:
        issues.append(
            ValidationIssue(
                f"{path}.target_ids", "project-scoped events must not target evidence"
            )
        )
    if not isinstance(scope, str) or scope not in {"evidence", "project"}:
        issues.append(ValidationIssue(f"{path}.scope", "has an unknown scope"))


def _validate_event_target(
    target_id: Any,
    record: Mapping[str, Any],
    path: str,
    targets: RecordIndex,
    rights: RecordIndex,
    sources: RecordIndex,
    seen_target_ids: set[str],
    issues: list[ValidationIssue],
) -> None:
    target_path = f"{path}.target_ids"
    if not isinstance(target_id, str) or not ID_PATTERN.fullmatch(target_id):
        issues.append(ValidationIssue(target_path, "contains an invalid ID"))
        return
    if target_id in seen_target_ids:
        issues.append(ValidationIssue(target_path, f"duplicates {target_id!r}"))
        return
    seen_target_ids.add(target_id)
    target = targets.get(target_id)
    if target is None:
        issues.append(ValidationIssue(target_path, f"unknown target {target_id!r}"))
        return
    source = sources.get(str(target.get("source_id")))
    parent_rights = rights.get(str(source.get("rights_id"))) if source else None
    child_rights = rights.get(str(record.get("rights_id")))
    if rights_are_broader(child_rights, parent_rights):
        issues.append(
            ValidationIssue(
                f"{path}.rights_id",
                "event rights are broader than targeted source rights",
            )
        )


def _validate_event_body(body: Any, path: str, issues: list[ValidationIssue]) -> None:
    if not isinstance(body, dict):
        issues.append(ValidationIssue(f"{path}.body", "must be an object"))
        return
    require_fields(body, f"{path}.body", ("format", "value"), issues)
    require_non_empty_string(body.get("format"), f"{path}.body.format", issues)


def _validate_event_values(
    record: Mapping[str, Any], path: str, issues: list[ValidationIssue]
) -> None:
    if not isinstance(record.get("alternatives"), list):
        issues.append(ValidationIssue(f"{path}.alternatives", "must be an array"))
    layer = record.get("layer")
    if not isinstance(layer, str) or layer not in EVENT_LAYERS:
        issues.append(ValidationIssue(f"{path}.layer", "has an unknown layer"))
    validate_confidence(record.get("confidence"), f"{path}.confidence", issues)
    review_status = record.get("review_status")
    if not isinstance(review_status, str) or review_status not in REVIEW_STATUSES:
        issues.append(ValidationIssue(f"{path}.review_status", "has an unknown status"))
