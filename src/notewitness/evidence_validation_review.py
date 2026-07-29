"""Validation of human review provenance for evidence records."""

from __future__ import annotations

from typing import Any, Mapping

from notewitness.evidence_contract import ValidationIssue
from notewitness.evidence_validation_common import record_path


RecordIndex = Mapping[str, Mapping[str, Any]]


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
    human_statuses = {"human_created", "human_accepted", "rejected", "contested"}
    if isinstance(status, str) and status in human_statuses and generator_kind != "human":
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
    accepted_status = isinstance(status, str) and status in {
        "human_created",
        "human_accepted",
    }
    if layer == "accepted_annotation" and not accepted_status:
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
