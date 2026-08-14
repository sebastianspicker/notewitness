"""Validation for evidence relation records."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from notewitness.evidence_contract import (
    CORE_RELATION_TYPES,
    LOCAL_TYPE_PATTERN,
    RELATION_LAYERS,
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


def validate_relations(
    relations: RecordIndex,
    events: RecordIndex,
    targets: RecordIndex,
    actors: RecordIndex,
    generators: RecordIndex,
    rights: RecordIndex,
    sources: RecordIndex,
    issues: list[ValidationIssue],
) -> None:
    for record_id, record in relations.items():
        path = record_path("relations", record_id)
        _validate_relation_shape(record, path, issues)
        _validate_relation_type(record, path, issues)
        _validate_relation_references(record, path, actors, generators, rights, issues)
        _validate_relation_values(record, path, issues)
        _validate_relation_arguments(
            record, path, events, targets, rights, sources, issues
        )


def _validate_relation_shape(
    record: Mapping[str, Any], path: str, issues: list[ValidationIssue]
) -> None:
    fields = {
        "id",
        "type",
        "arguments",
        "generator_id",
        "annotator_id",
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
            "arguments",
            "generator_id",
            "annotator_id",
            "rights_id",
            "layer",
            "confidence",
            "review_status",
        ),
        issues,
    )


def _validate_relation_type(
    record: Mapping[str, Any], path: str, issues: list[ValidationIssue]
) -> None:
    relation_type = record.get("type")
    if not isinstance(relation_type, str) or (
        relation_type not in CORE_RELATION_TYPES
        and not LOCAL_TYPE_PATTERN.fullmatch(relation_type)
    ):
        issues.append(
            ValidationIssue(
                f"{path}.type", "must use a core or namespaced local relation"
            )
        )


def _validate_relation_references(
    record: Mapping[str, Any],
    path: str,
    actors: RecordIndex,
    generators: RecordIndex,
    rights: RecordIndex,
    issues: list[ValidationIssue],
) -> None:
    for field in ("generator_id", "annotator_id", "rights_id"):
        validate_id(record.get(field), f"{path}.{field}", issues)
    _require_reference(
        record.get("generator_id"),
        generators,
        f"{path}.generator_id",
        "must reference a generator",
        issues,
    )
    _require_reference(
        record.get("annotator_id"),
        actors,
        f"{path}.annotator_id",
        "must reference an actor",
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


def _validate_relation_values(
    record: Mapping[str, Any], path: str, issues: list[ValidationIssue]
) -> None:
    review_status = record.get("review_status")
    if not isinstance(review_status, str) or review_status not in REVIEW_STATUSES:
        issues.append(ValidationIssue(f"{path}.review_status", "has an unknown status"))
    layer = record.get("layer")
    if not isinstance(layer, str) or layer not in RELATION_LAYERS:
        issues.append(ValidationIssue(f"{path}.layer", "has an unknown layer"))
    validate_confidence(record.get("confidence"), f"{path}.confidence", issues)


def _validate_relation_arguments(
    record: Mapping[str, Any],
    path: str,
    events: RecordIndex,
    targets: RecordIndex,
    rights: RecordIndex,
    sources: RecordIndex,
    issues: list[ValidationIssue],
) -> None:
    arguments = record.get("arguments")
    if not isinstance(arguments, list) or len(arguments) < 2:
        issues.append(
            ValidationIssue(f"{path}.arguments", "must contain at least two arguments")
        )
        return
    parent_rights = [
        _validate_relation_argument(
            argument,
            f"{path}.arguments[{index}]",
            events,
            targets,
            rights,
            sources,
            issues,
        )
        for index, argument in enumerate(arguments)
    ]
    child_rights = rights.get(str(record.get("rights_id")))
    if any(rights_are_broader(child_rights, parent) for parent in parent_rights):
        issues.append(
            ValidationIssue(
                f"{path}.rights_id",
                "relation rights are broader than referenced evidence rights",
            )
        )


def _validate_relation_argument(
    argument: Any,
    path: str,
    events: RecordIndex,
    targets: RecordIndex,
    rights: RecordIndex,
    sources: RecordIndex,
    issues: list[ValidationIssue],
) -> Mapping[str, Any] | None:
    if not isinstance(argument, dict):
        issues.append(ValidationIssue(path, "must be an object"))
        return None
    require_fields(argument, path, ("role", "ref_kind", "ref_id"), issues)
    reject_unknown_fields(argument, path, {"role", "ref_kind", "ref_id"}, issues)
    require_non_empty_string(argument.get("role"), f"{path}.role", issues)
    validate_id(argument.get("ref_id"), f"{path}.ref_id", issues)
    return _relation_argument_rights(
        argument, path, events, targets, rights, sources, issues
    )


def _relation_argument_rights(
    argument: Mapping[str, Any],
    path: str,
    events: RecordIndex,
    targets: RecordIndex,
    rights: RecordIndex,
    sources: RecordIndex,
    issues: list[ValidationIssue],
) -> Mapping[str, Any] | None:
    ref_id, ref_kind = argument.get("ref_id"), argument.get("ref_kind")
    if ref_kind == "event":
        referenced = events.get(ref_id) if isinstance(ref_id, str) else None
        if referenced is None:
            issues.append(ValidationIssue(f"{path}.ref_id", "unknown event"))
            return None
        return rights.get(str(referenced.get("rights_id")))
    if ref_kind == "target":
        target = targets.get(ref_id) if isinstance(ref_id, str) else None
        if target is None:
            issues.append(ValidationIssue(f"{path}.ref_id", "unknown target"))
            return None
        source = sources.get(str(target.get("source_id")))
        return rights.get(str(source.get("rights_id"))) if source else None
    issues.append(ValidationIssue(f"{path}.ref_kind", "must be 'event' or 'target'"))
    return None
