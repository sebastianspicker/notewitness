"""Immutable contract checks for one completed private run publication."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any, Mapping

from notewitness.evidence_contract import ID_PATTERN, SHA256_PATTERN


PUBLICATION_COLLECTIONS = ("actors", "generators", "targets", "events")
RUN_ID_PATTERN = re.compile(r"^run:(?:(analysis)-)?([0-9a-f]{32})$")


@dataclass(frozen=True, slots=True)
class PublicationSourceIdentity:
    source_id: str
    source_sha256: str
    source_uri: str
    rights_id: str
    source_record_sha256: str
    rights_record_sha256: str

    def __post_init__(self) -> None:
        if not ID_PATTERN.fullmatch(self.source_id):
            raise ValueError("Publication source_id is invalid.")
        if not SHA256_PATTERN.fullmatch(self.source_sha256):
            raise ValueError("Publication source_sha256 is invalid.")
        if not self.source_uri or "\\" in self.source_uri:
            raise ValueError("Publication source_uri is invalid.")
        if not ID_PATTERN.fullmatch(self.rights_id):
            raise ValueError("Publication rights_id is invalid.")
        if any(
            not SHA256_PATTERN.fullmatch(value)
            for value in (self.source_record_sha256, self.rights_record_sha256)
        ):
            raise ValueError("Publication record identity is invalid.")

    def as_dict(self) -> dict[str, str]:
        return {
            "rights_id": self.rights_id,
            "rights_record_sha256": self.rights_record_sha256,
            "source_id": self.source_id,
            "source_record_sha256": self.source_record_sha256,
            "source_sha256": self.source_sha256,
            "source_uri": self.source_uri,
        }


@dataclass(frozen=True, slots=True)
class RunPublication:
    kind: str
    run_id: str
    source: PublicationSourceIdentity
    model_sha256s: tuple[str, ...]
    artifact_sha256s: Mapping[str, str]
    records: Mapping[str, tuple[Mapping[str, Any], ...]]

    def __post_init__(self) -> None:
        _validate_publication_identity(self)
        _validate_artifact_map(self.kind, self.artifact_sha256s)
        _validate_records(self)

    @property
    def event_ids(self) -> tuple[str, ...]:
        return tuple(str(item["id"]) for item in self.records["events"])

    @property
    def target_ids(self) -> tuple[str, ...]:
        return tuple(str(item["id"]) for item in self.records["targets"])

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_sha256s": dict(self.artifact_sha256s),
            "kind": self.kind,
            "model_sha256s": list(self.model_sha256s),
            "records": {
                name: [copy.deepcopy(dict(item)) for item in self.records[name]]
                for name in PUBLICATION_COLLECTIONS
            },
            "run_id": self.run_id,
            "schema_version": 1,
            "source": self.source.as_dict(),
        }


def _validate_publication_identity(publication: RunPublication) -> None:
    if publication.kind not in {"analysis", "transcription"}:
        raise ValueError("Publication kind is invalid.")
    match = RUN_ID_PATTERN.fullmatch(publication.run_id)
    if match is None or (match.group(1) == "analysis") != (
        publication.kind == "analysis"
    ):
        raise ValueError("Publication run_id does not match its kind.")
    if not isinstance(publication.source, PublicationSourceIdentity):
        raise ValueError("Publication source identity is invalid.")
    if (
        not publication.model_sha256s
        or tuple(sorted(set(publication.model_sha256s))) != publication.model_sha256s
        or any(
            not SHA256_PATTERN.fullmatch(item) for item in publication.model_sha256s
        )
    ):
        raise ValueError("Publication model identities are invalid.")


def _validate_artifact_map(kind: str, artifacts: Mapping[str, str]) -> None:
    if not isinstance(artifacts, Mapping) or any(
        not isinstance(path, str)
        or not isinstance(digest, str)
        or not SHA256_PATTERN.fullmatch(digest)
        for path, digest in artifacts.items()
    ):
        raise ValueError("Publication artifact identities are invalid.")
    normalized = tuple(_artifact_relative_path(path) for path in artifacts)
    if len(normalized) != len(set(normalized)):
        raise ValueError("Publication artifact paths are not unique.")
    required = {"manifest.completed.json", _normalized_artifact_name(kind)}
    if kind == "transcription":
        required.add("transcript.evidence.json")
    if not required.issubset(artifacts):
        raise ValueError("Publication does not identify all required artifacts.")


def _normalized_artifact_name(kind: str) -> str:
    return "analysis.normalized.json" if kind == "analysis" else "transcript.normalized.json"


def _artifact_relative_path(value: str) -> str:
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or "\\" in value
        or not relative.parts
        or len(relative.parts) > 2
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.name == "publication.completed.json"
        or (len(relative.parts) == 2 and relative.parts[0] != "raw")
    ):
        raise ValueError("Publication artifact path is unsafe.")
    return relative.as_posix()


def _validate_records(publication: RunPublication) -> None:
    _validate_record_collections(publication.records)
    _validate_finite_json(publication)
    generators, actors, targets, events = _records_by_relationship(publication)
    _validate_target_event_pairs(targets, events)
    _validate_target_sources(targets, publication.source.source_id)
    _validate_event_relations(events, targets, generators, actors, publication)
    _validate_generator_models(generators, publication.model_sha256s)
    _validate_run_record_ids(publication, targets, events)


def _validate_record_collections(records: Mapping[str, tuple[Mapping[str, Any], ...]]) -> None:
    if not isinstance(records, Mapping) or set(records) != set(PUBLICATION_COLLECTIONS):
        raise ValueError("Publication record collections are invalid.")
    record_ids: list[str] = []
    for collection in PUBLICATION_COLLECTIONS:
        items = records[collection]
        if not isinstance(items, tuple):
            raise ValueError("Publication record collections must be immutable.")
        for record in items:
            if not isinstance(record, Mapping):
                raise ValueError("Publication records must be mappings.")
            record_id = record.get("id")
            if not isinstance(record_id, str) or not ID_PATTERN.fullmatch(record_id):
                raise ValueError("Publication record ID is invalid.")
            record_ids.append(record_id)
    if len(record_ids) != len(set(record_ids)):
        raise ValueError("Publication record IDs must be globally unique.")


def _validate_finite_json(publication: RunPublication) -> None:
    try:
        json.dumps(publication.as_dict(), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Publication records must be finite JSON data.") from exc


def _records_by_relationship(
    publication: RunPublication,
) -> tuple[
    dict[str, Mapping[str, Any]], set[str], dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]
]:
    records = publication.records
    return (
        {str(item["id"]): item for item in records["generators"]},
        {str(item["id"]) for item in records["actors"]},
        {str(item["id"]): item for item in records["targets"]},
        {str(item["id"]): item for item in records["events"]},
    )


def _validate_target_event_pairs(
    targets: Mapping[str, Mapping[str, Any]], events: Mapping[str, Mapping[str, Any]]
) -> None:
    if {item.removeprefix("target:") for item in targets} != {
        item.removeprefix("event:") for item in events
    }:
        raise ValueError("Publication targets and events are not paired.")


def _validate_target_sources(
    targets: Mapping[str, Mapping[str, Any]], source_id: str
) -> None:
    if any(target.get("source_id") != source_id for target in targets.values()):
        raise ValueError("Publication target source identity is invalid.")


def _validate_event_relations(
    events: Mapping[str, Mapping[str, Any]],
    targets: Mapping[str, Mapping[str, Any]],
    generators: Mapping[str, Mapping[str, Any]],
    actors: set[str],
    publication: RunPublication,
) -> None:
    for event in events.values():
        _validate_event_target(event, targets)
        if event.get("generator_id") not in generators:
            raise ValueError("Publication event generator identity is invalid.")
        actor_id = event.get("actor_id")
        if actor_id is not None and actor_id not in actors:
            raise ValueError("Publication event actor identity is invalid.")
        if event.get("rights_id") != publication.source.rights_id:
            raise ValueError("Publication event rights identity is invalid.")


def _validate_event_target(
    event: Mapping[str, Any], targets: Mapping[str, Mapping[str, Any]]
) -> None:
    expected_target_id = f"target:{str(event['id']).removeprefix('event:')}"
    if event.get("target_ids") != [expected_target_id] or expected_target_id not in targets:
        raise ValueError("Publication event target identity is invalid.")


def _validate_generator_models(
    generators: Mapping[str, Mapping[str, Any]], model_sha256s: tuple[str, ...]
) -> None:
    model_states = {f"sha256:{digest}" for digest in model_sha256s}
    if any(
        generator.get("weight_hash_state") not in model_states
        for generator in generators.values()
    ):
        raise ValueError("Publication generator model identity is invalid.")


def _validate_run_record_ids(
    publication: RunPublication,
    targets: Mapping[str, Mapping[str, Any]],
    events: Mapping[str, Mapping[str, Any]],
) -> None:
    if publication.kind == "transcription":
        _validate_transcript_record_ids(publication, targets, events)
        return
    token = publication.run_id.removeprefix("run:analysis-")
    if not _all_analysis_ids_match(targets, "target", token) or not _all_analysis_ids_match(events, "event", token):
        raise ValueError("Analysis publication IDs are not run-derived.")


def _validate_transcript_record_ids(
    publication: RunPublication,
    targets: Mapping[str, Mapping[str, Any]],
    events: Mapping[str, Mapping[str, Any]],
) -> None:
    token = hashlib.sha256(publication.run_id.encode("utf-8")).hexdigest()[:16]
    if _record_ids(targets, "target", token) != set(targets) or _record_ids(events, "event", token) != set(events):
        raise ValueError("Transcript publication IDs are not run-derived.")
    if any(
        not isinstance(event.get("body"), Mapping)
        or event["body"].get("run_id") != publication.run_id
        for event in events.values()
    ):
        raise ValueError("Transcript publication body run identity is invalid.")


def _record_ids(records: Mapping[str, Mapping[str, Any]], prefix: str, token: str) -> set[str]:
    return {f"{prefix}:asr-{token}-{index}" for index in range(1, len(records) + 1)}


def _all_analysis_ids_match(records: Mapping[str, Mapping[str, Any]], prefix: str, token: str) -> bool:
    return all(record_id.startswith(f"{prefix}:analysis-{token}-") for record_id in records)
