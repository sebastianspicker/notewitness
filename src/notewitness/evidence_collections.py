"""Read-only collection and rights queries over evidence graph payloads."""

from __future__ import annotations

from typing import Any, Iterable, Mapping


def records(
    payload: Mapping[str, Any], collection: str
) -> tuple[dict[str, Any], ...]:
    raw = payload.get(collection, [])
    if not isinstance(raw, list):
        return ()
    return tuple(record for record in raw if isinstance(record, dict))


def index(
    payload: Mapping[str, Any], collection: str
) -> dict[str, dict[str, Any]]:
    return {
        str(record["id"]): record
        for record in records(payload, collection)
        if isinstance(record.get("id"), str)
    }


def selected_events_allow_remote(
    payload: Mapping[str, Any], event_ids: Iterable[str]
) -> bool:
    events = index(payload, "events")
    targets = index(payload, "targets")
    sources = index(payload, "sources")
    rights = index(payload, "rights")
    selected = tuple(event_ids)
    if not selected:
        return False
    for event_id in selected:
        event = events.get(event_id)
        if event is None:
            return False
        target_ids = event.get("target_ids")
        if (
            event.get("scope") != "evidence"
            or not isinstance(target_ids, list)
            or not target_ids
        ):
            return False
        event_rights = rights.get(str(event.get("rights_id")))
        if not event_rights or event_rights.get("remote_processing") is not True:
            return False
        for target_id in target_ids:
            target = targets.get(str(target_id))
            source = sources.get(str(target.get("source_id"))) if target else None
            source_rights = (
                rights.get(str(source.get("rights_id"))) if source else None
            )
            if (
                not source_rights
                or source_rights.get("remote_processing") is not True
            ):
                return False
    return True
