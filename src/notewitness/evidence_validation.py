"""Orchestration for the dependency-free evidence-graph validator."""

from __future__ import annotations

from typing import Any, Mapping

from notewitness.evidence_collections import index
from notewitness.evidence_contract import (
    COLLECTIONS,
    ID_PATTERN,
    SCHEMA_VERSION,
    ValidationIssue,
)
from notewitness.evidence_validation_assertions import (
    validate_events,
    validate_relations,
    validate_review_provenance,
)
from notewitness.evidence_validation_common import (
    reject_unknown_fields,
    require_fields,
    require_non_empty_string,
    validate_datetime,
    validate_id,
)
from notewitness.evidence_validation_records import (
    validate_actors,
    validate_generators,
    validate_rights,
    validate_sources,
    validate_targets,
)
from notewitness.evidence_validation_revisions import validate_revisions
from notewitness.network import NetworkMode


def validate_payload(payload: Mapping[str, Any]) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    _validate_envelope(payload, issues)
    all_ids = _collect_ids(payload, issues)
    records = _index_records(payload)
    _validate_records(records, all_ids, issues)
    return tuple(issues)


def _validate_envelope(
    payload: Mapping[str, Any], issues: list[ValidationIssue]
) -> None:
    reject_unknown_fields(
        payload, "$", {"schema_version", "project", "network", *COLLECTIONS}, issues
    )
    if payload.get("schema_version") != SCHEMA_VERSION:
        issues.append(
            ValidationIssue("$.schema_version", f"must equal {SCHEMA_VERSION!r}")
        )
    _validate_project(payload.get("project"), issues)
    _validate_network(payload.get("network"), issues)


def _validate_project(project: Any, issues: list[ValidationIssue]) -> None:
    if not isinstance(project, dict):
        issues.append(ValidationIssue("$.project", "must be an object"))
        return
    require_fields(project, "$.project", ("id", "name", "created_at"), issues)
    reject_unknown_fields(project, "$.project", {"id", "name", "created_at"}, issues)
    validate_id(project.get("id"), "$.project.id", issues)
    require_non_empty_string(project.get("name"), "$.project.name", issues)
    validate_datetime(project.get("created_at"), "$.project.created_at", issues)


def _validate_network(network: Any, issues: list[ValidationIssue]) -> None:
    if not isinstance(network, dict):
        issues.append(ValidationIssue("$.network", "must be an object"))
        return
    require_fields(network, "$.network", ("mode",), issues)
    reject_unknown_fields(network, "$.network", {"mode"}, issues)
    network_mode = network.get("mode")
    if not isinstance(network_mode, str) or network_mode not in {
        mode.value for mode in NetworkMode
    }:
        issues.append(
            ValidationIssue(
                "$.network.mode", "must be a supported explicit network mode"
            )
        )


def _collect_ids(
    payload: Mapping[str, Any], issues: list[ValidationIssue]
) -> dict[str, str]:
    all_ids = _project_id(payload.get("project"))
    for collection in COLLECTIONS:
        _collect_collection_ids(payload.get(collection), collection, all_ids, issues)
    return all_ids


def _project_id(project: Any) -> dict[str, str]:
    project_id = project.get("id") if isinstance(project, dict) else None
    if isinstance(project_id, str) and ID_PATTERN.fullmatch(project_id):
        return {project_id: "$.project"}
    return {}


def _collect_collection_ids(
    raw: Any, collection: str, all_ids: dict[str, str], issues: list[ValidationIssue]
) -> None:
    if not isinstance(raw, list):
        issues.append(ValidationIssue(f"$.{collection}", "must be an array"))
        return
    for record_index, record in enumerate(raw):
        _collect_record_id(record, f"$.{collection}[{record_index}]", all_ids, issues)


def _collect_record_id(
    record: Any, path: str, all_ids: dict[str, str], issues: list[ValidationIssue]
) -> None:
    if not isinstance(record, dict):
        issues.append(ValidationIssue(path, "must be an object"))
        return
    record_id = record.get("id")
    if not isinstance(record_id, str) or not ID_PATTERN.fullmatch(record_id):
        issues.append(ValidationIssue(f"{path}.id", "must be a stable ID"))
        return
    if record_id in all_ids:
        issues.append(
            ValidationIssue(
                f"{path}.id", f"duplicates {record_id!r} from {all_ids[record_id]}"
            )
        )
    else:
        all_ids[record_id] = path


def _index_records(
    payload: Mapping[str, Any],
) -> dict[str, Mapping[str, Mapping[str, Any]]]:
    return {collection: index(payload, collection) for collection in COLLECTIONS}


def _validate_records(
    records: Mapping[str, Mapping[str, Mapping[str, Any]]],
    all_ids: Mapping[str, str],
    issues: list[ValidationIssue],
) -> None:
    rights, sources = records["rights"], records["sources"]
    actors, targets = records["actors"], records["targets"]
    generators, events = records["generators"], records["events"]
    relations, revisions = records["relations"], records["revisions"]
    validate_rights(rights, issues)
    validate_sources(sources, rights, issues)
    validate_actors(actors, issues)
    validate_targets(targets, sources, issues)
    validate_generators(generators, issues)
    validate_events(events, actors, targets, generators, rights, sources, issues)
    validate_relations(
        relations, events, targets, actors, generators, rights, sources, issues
    )
    validate_revisions(revisions, all_ids, actors, issues)
    validate_review_provenance(events, relations, generators, revisions, actors, issues)
