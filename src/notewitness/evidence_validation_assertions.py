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
        reject_unknown_fields(
            record,
            path,
            {
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
            },
            issues,
        )
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
        event_type = record.get("type")
        if not isinstance(event_type, str) or (
            event_type not in CORE_EVENT_TYPES
            and not LOCAL_TYPE_PATTERN.fullmatch(event_type)
        ):
            issues.append(
                ValidationIssue(
                    f"{path}.type",
                    "must use a core or namespaced local event type",
                )
            )
        validate_id(record.get("actor_id"), f"{path}.actor_id", issues)
        validate_id(record.get("generator_id"), f"{path}.generator_id", issues)
        validate_id(record.get("rights_id"), f"{path}.rights_id", issues)
        actor_id = record.get("actor_id")
        if not isinstance(actor_id, str) or actor_id not in actors:
            issues.append(
                ValidationIssue(f"{path}.actor_id", "must reference an actor")
            )
        generator_id = record.get("generator_id")
        if not isinstance(generator_id, str) or generator_id not in generators:
            issues.append(
                ValidationIssue(
                    f"{path}.generator_id", "must reference a generator"
                )
            )
        rights_id = record.get("rights_id")
        if not isinstance(rights_id, str) or rights_id not in rights:
            issues.append(
                ValidationIssue(f"{path}.rights_id", "must reference rights")
            )
        target_ids = record.get("target_ids")
        if not isinstance(target_ids, list):
            issues.append(ValidationIssue(f"{path}.target_ids", "must be an array"))
            continue
        seen_target_ids: set[str] = set()
        scope = record.get("scope")
        if scope == "evidence" and not target_ids:
            issues.append(
                ValidationIssue(
                    f"{path}.target_ids", "evidence events require at least one target"
                )
            )
        if scope == "project" and target_ids:
            issues.append(
                ValidationIssue(
                    f"{path}.target_ids",
                    "project-scoped events must not target evidence",
                )
            )
        if not isinstance(scope, str) or scope not in {"evidence", "project"}:
            issues.append(ValidationIssue(f"{path}.scope", "has an unknown scope"))
        for target_id in target_ids:
            target_path = f"{path}.target_ids"
            if not isinstance(target_id, str) or not ID_PATTERN.fullmatch(target_id):
                issues.append(ValidationIssue(target_path, "contains an invalid ID"))
                continue
            if target_id in seen_target_ids:
                issues.append(
                    ValidationIssue(target_path, f"duplicates {target_id!r}")
                )
                continue
            seen_target_ids.add(target_id)
            if target_id not in targets:
                issues.append(
                    ValidationIssue(
                        f"{path}.target_ids", f"unknown target {target_id!r}"
                    )
                )
                continue
            source = sources.get(str(targets[target_id].get("source_id")))
            parent_rights = (
                rights.get(str(source.get("rights_id"))) if source else None
            )
            child_rights = rights.get(str(record.get("rights_id")))
            if rights_are_broader(child_rights, parent_rights):
                issues.append(
                    ValidationIssue(
                        f"{path}.rights_id",
                        "event rights are broader than targeted source rights",
                    )
                )
        body = record.get("body")
        if not isinstance(body, dict):
            issues.append(ValidationIssue(f"{path}.body", "must be an object"))
        else:
            require_fields(body, f"{path}.body", ("format", "value"), issues)
            require_non_empty_string(
                body.get("format"), f"{path}.body.format", issues
            )
        if not isinstance(record.get("alternatives"), list):
            issues.append(
                ValidationIssue(f"{path}.alternatives", "must be an array")
            )
        layer = record.get("layer")
        if not isinstance(layer, str) or layer not in EVENT_LAYERS:
            issues.append(ValidationIssue(f"{path}.layer", "has an unknown layer"))
        validate_confidence(record.get("confidence"), f"{path}.confidence", issues)
        review_status = record.get("review_status")
        if (
            not isinstance(review_status, str)
            or review_status not in REVIEW_STATUSES
        ):
            issues.append(
                ValidationIssue(f"{path}.review_status", "has an unknown status")
            )


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
        reject_unknown_fields(
            record,
            path,
            {
                "id",
                "type",
                "arguments",
                "generator_id",
                "annotator_id",
                "rights_id",
                "layer",
                "confidence",
                "review_status",
            },
            issues,
        )
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
        validate_id(record.get("generator_id"), f"{path}.generator_id", issues)
        validate_id(record.get("annotator_id"), f"{path}.annotator_id", issues)
        validate_id(record.get("rights_id"), f"{path}.rights_id", issues)
        generator_id = record.get("generator_id")
        if not isinstance(generator_id, str) or generator_id not in generators:
            issues.append(
                ValidationIssue(
                    f"{path}.generator_id", "must reference a generator"
                )
            )
        annotator_id = record.get("annotator_id")
        if not isinstance(annotator_id, str) or annotator_id not in actors:
            issues.append(
                ValidationIssue(f"{path}.annotator_id", "must reference an actor")
            )
        rights_id = record.get("rights_id")
        if not isinstance(rights_id, str) or rights_id not in rights:
            issues.append(
                ValidationIssue(f"{path}.rights_id", "must reference rights")
            )
        review_status = record.get("review_status")
        if (
            not isinstance(review_status, str)
            or review_status not in REVIEW_STATUSES
        ):
            issues.append(
                ValidationIssue(f"{path}.review_status", "has an unknown status")
            )
        layer = record.get("layer")
        if not isinstance(layer, str) or layer not in RELATION_LAYERS:
            issues.append(ValidationIssue(f"{path}.layer", "has an unknown layer"))
        validate_confidence(record.get("confidence"), f"{path}.confidence", issues)

        arguments = record.get("arguments")
        if not isinstance(arguments, list) or len(arguments) < 2:
            issues.append(
                ValidationIssue(
                    f"{path}.arguments", "must contain at least two arguments"
                )
            )
            continue
        parent_rights_records: list[Mapping[str, Any] | None] = []
        for index, argument in enumerate(arguments):
            arg_path = f"{path}.arguments[{index}]"
            if not isinstance(argument, dict):
                issues.append(ValidationIssue(arg_path, "must be an object"))
                continue
            require_fields(argument, arg_path, ("role", "ref_kind", "ref_id"), issues)
            reject_unknown_fields(
                argument, arg_path, {"role", "ref_kind", "ref_id"}, issues
            )
            require_non_empty_string(
                argument.get("role"), f"{arg_path}.role", issues
            )
            validate_id(argument.get("ref_id"), f"{arg_path}.ref_id", issues)
            ref_kind = argument.get("ref_kind")
            ref_id = argument.get("ref_id")
            if ref_kind == "event":
                referenced = events.get(ref_id) if isinstance(ref_id, str) else None
                if referenced is None:
                    issues.append(
                        ValidationIssue(f"{arg_path}.ref_id", "unknown event")
                    )
                else:
                    parent_rights_records.append(
                        rights.get(str(referenced.get("rights_id")))
                    )
            elif ref_kind == "target":
                target = targets.get(ref_id) if isinstance(ref_id, str) else None
                if target is None:
                    issues.append(
                        ValidationIssue(f"{arg_path}.ref_id", "unknown target")
                    )
                else:
                    source = sources.get(str(target.get("source_id")))
                    parent_rights_records.append(
                        rights.get(str(source.get("rights_id"))) if source else None
                    )
            else:
                issues.append(
                    ValidationIssue(
                        f"{arg_path}.ref_kind", "must be 'event' or 'target'"
                    )
                )
        child_rights = rights.get(str(record.get("rights_id")))
        if any(
            rights_are_broader(child_rights, parent)
            for parent in parent_rights_records
        ):
            issues.append(
                ValidationIssue(
                    f"{path}.rights_id",
                    "relation rights are broader than referenced evidence rights",
                )
            )


def validate_review_provenance(
    events: RecordIndex,
    relations: RecordIndex,
    generators: RecordIndex,
    revisions: RecordIndex,
    actors: RecordIndex,
    issues: list[ValidationIssue],
) -> None:
    human_review_operations: dict[str, set[str]] = {}
    for revision in revisions.values():
        record_id = revision.get("record_id")
        author_id = revision.get("author_id")
        operation = revision.get("operation")
        if (
            isinstance(record_id, str)
            and isinstance(author_id, str)
            and author_id in actors
            and isinstance(operation, str)
            and operation in {"adjudicate", "reject"}
        ):
            human_review_operations.setdefault(record_id, set()).add(
                str(operation)
            )

    for collection, records in (("events", events), ("relations", relations)):
        for record_id, record in records.items():
            path = record_path(collection, record_id)
            generator = generators.get(str(record.get("generator_id")))
            generator_kind = generator.get("kind") if generator else None
            status = record.get("review_status")
            layer = record.get("layer")

            if generator_kind == "machine" and status != "machine_suggested":
                issues.append(
                    ValidationIssue(
                        f"{path}.review_status",
                        "machine-generated records must remain machine_suggested",
                    )
                )
            if (
                isinstance(status, str)
                and status
                in {"human_created", "human_accepted", "rejected", "contested"}
                and generator_kind != "human"
            ):
                issues.append(
                    ValidationIssue(
                        f"{path}.generator_id",
                        "human review states require a human generator",
                    )
                )
            if (
                status == "machine_suggested"
                and isinstance(layer, str)
                and layer in {"accepted_annotation", "presentation"}
            ):
                issues.append(
                    ValidationIssue(
                        f"{path}.layer",
                        "machine suggestions cannot enter an accepted layer",
                    )
                )
            if layer == "accepted_annotation" and (
                not isinstance(status, str)
                or status not in {"human_created", "human_accepted"}
            ):
                issues.append(
                    ValidationIssue(
                        f"{path}.review_status",
                        "accepted annotations require a human review state",
                    )
                )
            operations = human_review_operations.get(record_id, set())
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
                        f"{path}.review_status",
                        "rejected requires a human rejection revision",
                    )
                )
