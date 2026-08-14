"""Evidence selection and idempotent project-record append for completed runs."""

from __future__ import annotations

import copy
from typing import Any, Iterable, Mapping

from notewitness.application._run_integration_support import (
    RunIntegrationError,
    json_sha256,
)
from notewitness.application._run_publication_contract import (
    PUBLICATION_COLLECTIONS,
    PublicationSourceIdentity,
    RunPublication,
)


def capture_source_identity(
    payload: Mapping[str, Any], source_id: str
) -> PublicationSourceIdentity:
    """Capture the source and rights records that authorize one completed run."""

    source = unique_record(payload, "sources", source_id)
    source_sha256 = source.get("sha256")
    source_uri = source.get("uri")
    rights_id = source.get("rights_id")
    if not isinstance(source_sha256, str) or not isinstance(source_uri, str):
        raise RunIntegrationError("Run source identity is incomplete.")
    if not isinstance(rights_id, str):
        raise RunIntegrationError("Run source rights identity is incomplete.")
    rights = unique_record(payload, "rights", rights_id)
    return PublicationSourceIdentity(
        source_id=source_id,
        source_sha256=source_sha256,
        source_uri=source_uri,
        rights_id=rights_id,
        source_record_sha256=json_sha256(source),
        rights_record_sha256=json_sha256(rights),
    )


def select_publication_records(
    payload: Mapping[str, Any],
    *,
    actor_ids: Iterable[str],
    generator_ids: Iterable[str],
    target_ids: Iterable[str],
    event_ids: Iterable[str],
) -> dict[str, tuple[Mapping[str, Any], ...]]:
    """Select the exact graph records produced by a run projection."""

    planned = {
        "actors": tuple(dict.fromkeys(actor_ids)),
        "generators": tuple(dict.fromkeys(generator_ids)),
        "targets": tuple(dict.fromkeys(target_ids)),
        "events": tuple(dict.fromkeys(event_ids)),
    }
    return {
        name: tuple(
            copy.deepcopy(unique_record(payload, name, record_id))
            for record_id in planned[name]
        )
        for name in PUBLICATION_COLLECTIONS
    }


def append_publication_records(
    payload: dict[str, Any], publication: RunPublication
) -> int:
    """Append an all-or-nothing publication projection, if absent."""

    require_current_source(payload, publication.source)
    states = publication_presence(payload, publication)
    require_complete_evidence(states)
    return append_missing_records(payload, publication, states)


def require_current_source(
    payload: Mapping[str, Any], expected: PublicationSourceIdentity
) -> None:
    current = capture_source_identity(payload, expected.source_id)
    if current != expected:
        raise RunIntegrationError(
            "Project source or rights changed after the model run completed."
        )


def publication_presence(
    payload: Mapping[str, Any], publication: RunPublication
) -> dict[str, tuple[bool, ...]]:
    return {
        collection: collection_presence(
            collection_value(payload, collection),
            publication.records[collection],
            collection,
        )
        for collection in PUBLICATION_COLLECTIONS
    }


def collection_presence(
    current: list[dict[str, Any]],
    records: tuple[Mapping[str, Any], ...],
    collection: str,
) -> tuple[bool, ...]:
    presence: list[bool] = []
    for record in records:
        matches = [item for item in current if item.get("id") == record["id"]]
        if len(matches) > 1 or (matches and matches[0] != record):
            raise RunIntegrationError(
                f"Existing {collection} record {record['id']!r} conflicts "
                "with the completed run."
            )
        presence.append(bool(matches))
    return tuple(presence)


def require_complete_evidence(states: Mapping[str, tuple[bool, ...]]) -> None:
    evidence_presence = states["targets"] + states["events"]
    if evidence_presence and any(evidence_presence) and not all(evidence_presence):
        raise RunIntegrationError(
            "Completed run evidence is only partially present in the project."
        )


def append_missing_records(
    payload: dict[str, Any],
    publication: RunPublication,
    states: Mapping[str, tuple[bool, ...]],
) -> int:
    added = 0
    for collection in PUBLICATION_COLLECTIONS:
        current = collection_value(payload, collection)
        for record, present in zip(
            publication.records[collection], states[collection], strict=True
        ):
            if not present:
                current.append(copy.deepcopy(dict(record)))
                added += 1
    return added


def collection_value(payload: Mapping[str, Any], name: str) -> list[dict[str, Any]]:
    value = payload.get(name)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise RunIntegrationError(f"Project collection {name!r} is malformed.")
    return value


def unique_record(
    payload: Mapping[str, Any], collection: str, record_id: str
) -> dict[str, Any]:
    matches = [
        item
        for item in collection_value(payload, collection)
        if item.get("id") == record_id
    ]
    if len(matches) != 1:
        raise RunIntegrationError(
            f"Project requires exactly one {collection} record {record_id!r}."
        )
    return matches[0]
