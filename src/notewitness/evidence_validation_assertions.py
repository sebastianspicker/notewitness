"""Validation for evidence events, relations, and review provenance."""

from __future__ import annotations

from typing import Any, Mapping

from notewitness.evidence_contract import (
    CORE_EVENT_TYPES,
    CORE_RELATION_TYPES,
    EVENT_LAYERS,
    ID_PATTERN,
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


def validate_review_provenance(
    events: RecordIndex,
    relations: RecordIndex,
    generators: RecordIndex,
    revisions: RecordIndex,
    actors: RecordIndex,
    issues: list[ValidationIssue],
) -> None:
    human_operations = _human_review_operations(revisions, actors)
    for collection, records in (("events", events), ("relations", relations)):
        for record_id, record in records.items():
            _validate_review_record(
                record,
                record_path(collection, record_id),
                generators,
                human_operations,
                issues,
            )


def _human_review_operations(
    revisions: RecordIndex, actors: RecordIndex
) -> dict[str, set[str]]:
    operations: dict[str, set[str]] = {}
    for revision in revisions.values():
        record_id, author_id, operation = (
            revision.get("record_id"),
            revision.get("author_id"),
            revision.get("operation"),
        )
        if (
            isinstance(record_id, str)
            and isinstance(author_id, str)
            and author_id in actors
            and isinstance(operation, str)
            and operation in {"adjudicate", "reject"}
        ):
            operations.setdefault(record_id, set()).add(operation)
    return operations


def _validate_review_record(
    record: Mapping[str, Any],
    path: str,
    generators: RecordIndex,
    operations: Mapping[str, set[str]],
    issues: list[ValidationIssue],
) -> None:
    generator = generators.get(str(record.get("generator_id")))
    generator_kind = generator.get("kind") if generator else None
    status, layer = record.get("review_status"), record.get("layer")
    _validate_review_generator(status, generator_kind, path, issues)
    _validate_review_layer(status, layer, path, issues)
    _validate_review_operations(
        status, operations.get(str(record.get("id")), set()), path, issues
    )


def _validate_review_generator(
    status: Any, generator_kind: Any, path: str, issues: list[ValidationIssue]
) -> None:
    if generator_kind == "machine" and status != "machine_suggested":
        issues.append(
            ValidationIssue(
                f"{path}.review_status",
                "machine-generated records must remain machine_suggested",
            )
        )
    if (
        isinstance(status, str)
        and status in {"human_created", "human_accepted", "rejected", "contested"}
        and generator_kind != "human"
    ):
        issues.append(
            ValidationIssue(
                f"{path}.generator_id", "human review states require a human generator"
            )
        )


def _validate_review_layer(
    status: Any, layer: Any, path: str, issues: list[ValidationIssue]
) -> None:
    if (
        status == "machine_suggested"
        and isinstance(layer, str)
        and layer in {"accepted_annotation", "presentation"}
    ):
        issues.append(
            ValidationIssue(
                f"{path}.layer", "machine suggestions cannot enter an accepted layer"
            )
        )
    if layer == "accepted_annotation" and (
        not isinstance(status, str) or status not in {"human_created", "human_accepted"}
    ):
        issues.append(
            ValidationIssue(
                f"{path}.review_status",
                "accepted annotations require a human review state",
            )
        )


def _validate_review_operations(
    status: Any, operations: set[str], path: str, issues: list[ValidationIssue]
) -> None:
    if status == "human_accepted" and "adjudicate" not in operations:
        issues.append(
            ValidationIssue(
                f"{path}.review_status",
                "human_accepted requires a human adjudication revision",
            )
        )
    if status == "rejected" and "reject" not in operations:
        issues.append(
            ValidationIssue(
                f"{path}.review_status", "rejected requires a human rejection revision"
            )
        )
