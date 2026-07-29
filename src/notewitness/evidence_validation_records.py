"""Validation for rights, sources, actors, targets, and generators."""

from __future__ import annotations

from typing import Any, Mapping

from notewitness.evidence_contract import (
    ACCESS_RANK,
    ALIGNMENT_STATES,
    GENERATOR_KINDS,
    SHA256_PATTERN,
    VISIBILITY_LEVELS,
    ValidationIssue,
)
from notewitness.evidence_validation_common import (
    record_path,
    reject_unknown_fields,
    require_fields,
    require_non_empty_string,
    validate_id,
)


RecordIndex = Mapping[str, Mapping[str, Any]]


def validate_rights(rights: RecordIndex, issues: list[ValidationIssue]) -> None:
    for record_id, record in rights.items():
        path = record_path("rights", record_id)
        reject_unknown_fields(
            record,
            path,
            {
                "id",
                "access",
                "remote_processing",
                "model_training",
                "retention",
                "license",
            },
            issues,
        )
        require_fields(
            record,
            path,
            ("access", "remote_processing", "model_training", "retention"),
            issues,
        )
        access = record.get("access")
        if not isinstance(access, str) or access not in ACCESS_RANK:
            issues.append(ValidationIssue(f"{path}.access", "has an unknown tier"))
        for field in ("remote_processing", "model_training"):
            if not isinstance(record.get(field), bool):
                issues.append(ValidationIssue(f"{path}.{field}", "must be boolean"))
        require_non_empty_string(record.get("retention"), f"{path}.retention", issues)
        if "license" in record and not isinstance(record.get("license"), str):
            issues.append(ValidationIssue(f"{path}.license", "must be a string"))


def validate_sources(
    sources: RecordIndex,
    rights: RecordIndex,
    issues: list[ValidationIssue],
) -> None:
    for record_id, record in sources.items():
        path = record_path("sources", record_id)
        reject_unknown_fields(
            record,
            path,
            {"id", "kind", "uri", "sha256", "rights_id"},
            issues,
        )
        require_fields(record, path, ("kind", "uri", "sha256", "rights_id"), issues)
        require_non_empty_string(record.get("kind"), f"{path}.kind", issues)
        require_non_empty_string(record.get("uri"), f"{path}.uri", issues)
        validate_id(record.get("rights_id"), f"{path}.rights_id", issues)
        rights_id = record.get("rights_id")
        if not isinstance(rights_id, str) or rights_id not in rights:
            issues.append(ValidationIssue(f"{path}.rights_id", "must reference rights"))
        checksum = record.get("sha256")
        if not isinstance(checksum, str) or not SHA256_PATTERN.fullmatch(checksum):
            issues.append(
                ValidationIssue(f"{path}.sha256", "must be lowercase SHA-256")
            )


def validate_actors(actors: RecordIndex, issues: list[ValidationIssue]) -> None:
    for record_id, record in actors.items():
        path = record_path("actors", record_id)
        reject_unknown_fields(
            record,
            path,
            {"id", "role", "visibility", "instrument_role"},
            issues,
        )
        require_fields(record, path, ("role", "visibility"), issues)
        require_non_empty_string(record.get("role"), f"{path}.role", issues)
        visibility = record.get("visibility")
        if not isinstance(visibility, str) or visibility not in VISIBILITY_LEVELS:
            issues.append(ValidationIssue(f"{path}.visibility", "has an unknown tier"))
        if "instrument_role" in record and not isinstance(
            record.get("instrument_role"), str
        ):
            issues.append(
                ValidationIssue(f"{path}.instrument_role", "must be a string")
            )


def validate_targets(
    targets: RecordIndex,
    sources: RecordIndex,
    issues: list[ValidationIssue],
) -> None:
    for record_id, record in targets.items():
        path = record_path("targets", record_id)
        _validate_target_shape(record, path, issues)
        _validate_target_source(record, path, sources, issues)
        _validate_target_selector(record.get("selector"), path, issues)
        _validate_target_options(record, path, issues)


def _validate_target_shape(
    record: Mapping[str, Any], path: str, issues: list[ValidationIssue]
) -> None:
    reject_unknown_fields(
        record,
        path,
        {"id", "source_id", "selector", "musical_selector", "alignment_state"},
        issues,
    )
    require_fields(record, path, ("source_id", "selector", "alignment_state"), issues)


def _validate_target_source(
    record: Mapping[str, Any],
    path: str,
    sources: RecordIndex,
    issues: list[ValidationIssue],
) -> None:
    validate_id(record.get("source_id"), f"{path}.source_id", issues)
    source_id = record.get("source_id")
    if not isinstance(source_id, str) or source_id not in sources:
        issues.append(ValidationIssue(f"{path}.source_id", "must reference a source"))


def _validate_target_selector(
    selector: Any, path: str, issues: list[ValidationIssue]
) -> None:
    if not isinstance(selector, dict):
        issues.append(ValidationIssue(f"{path}.selector", "must be an object"))
        return
    selector_path = f"{path}.selector"
    require_fields(
        selector, selector_path, ("stream_id", "start_us", "duration_us"), issues
    )
    reject_unknown_fields(
        selector,
        selector_path,
        {"stream_id", "start_us", "duration_us", "spatial"},
        issues,
    )
    require_non_empty_string(
        selector.get("stream_id"), f"{selector_path}.stream_id", issues
    )
    _validate_selector_offsets(selector, path, issues)
    if "spatial" in selector and not isinstance(
        selector.get("spatial"), (dict, type(None))
    ):
        issues.append(
            ValidationIssue(f"{selector_path}.spatial", "must be an object or null")
        )


def _validate_selector_offsets(
    selector: Mapping[str, Any], path: str, issues: list[ValidationIssue]
) -> None:
    for field in ("start_us", "duration_us"):
        value = selector.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            issues.append(
                ValidationIssue(
                    f"{path}.selector.{field}", "must be a non-negative integer"
                )
            )


def _validate_target_options(
    record: Mapping[str, Any], path: str, issues: list[ValidationIssue]
) -> None:
    if "musical_selector" in record and not isinstance(
        record.get("musical_selector"), (dict, type(None))
    ):
        issues.append(
            ValidationIssue(f"{path}.musical_selector", "must be an object or null")
        )
    alignment_state = record.get("alignment_state")
    if not isinstance(alignment_state, str) or alignment_state not in ALIGNMENT_STATES:
        issues.append(
            ValidationIssue(f"{path}.alignment_state", "has an unsupported state")
        )


def validate_generators(generators: RecordIndex, issues: list[ValidationIssue]) -> None:
    for record_id, record in generators.items():
        path = record_path("generators", record_id)
        _validate_generator_shape(record, path, issues)
        _validate_generator_kind(record, path, issues)
        _validate_generator_machine_fields(record, path, issues)
        _validate_generator_options(record, path, issues)


def _validate_generator_shape(
    record: Mapping[str, Any], path: str, issues: list[ValidationIssue]
) -> None:
    reject_unknown_fields(
        record,
        path,
        {"id", "kind", "name", "version", "model", "weight_hash_state", "parameters"},
        issues,
    )
    require_fields(record, path, ("kind", "name", "version"), issues)


def _validate_generator_kind(
    record: Mapping[str, Any], path: str, issues: list[ValidationIssue]
) -> None:
    generator_kind = record.get("kind")
    if not isinstance(generator_kind, str) or generator_kind not in GENERATOR_KINDS:
        issues.append(ValidationIssue(f"{path}.kind", "has an unknown kind"))
    require_non_empty_string(record.get("name"), f"{path}.name", issues)
    require_non_empty_string(record.get("version"), f"{path}.version", issues)


def _validate_generator_machine_fields(
    record: Mapping[str, Any], path: str, issues: list[ValidationIssue]
) -> None:
    if record.get("kind") != "machine":
        return
    require_fields(record, path, ("model", "weight_hash_state"), issues)
    require_non_empty_string(record.get("model"), f"{path}.model", issues)
    require_non_empty_string(
        record.get("weight_hash_state"), f"{path}.weight_hash_state", issues
    )


def _validate_generator_options(
    record: Mapping[str, Any], path: str, issues: list[ValidationIssue]
) -> None:
    for field in ("model", "weight_hash_state"):
        if field in record and not isinstance(record.get(field), str):
            issues.append(ValidationIssue(f"{path}.{field}", "must be a string"))
    if "parameters" in record and not isinstance(record.get("parameters"), dict):
        issues.append(ValidationIssue(f"{path}.parameters", "must be an object"))
