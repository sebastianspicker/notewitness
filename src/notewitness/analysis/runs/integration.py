"""Durable, idempotent integration of completed local model runs.

Integration verifies every sealed artifact and the completed manifest before
the single atomic project append, so a run is either fully present in the
evidence graph or not present at all.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any, Iterable, Mapping

from notewitness.analysis.runs.publication import (
    MAX_PUBLICATION_BYTES,
    PUBLICATION_COLLECTIONS,
    PUBLICATION_FILENAME,
    RUN_ID_PATTERN,
    PublicationSourceIdentity,
    RunIntegrationError,
    RunPublication,
    artifact_relative_path,
    private_file_identity,
    read_private_file,
    read_publication,
)
from notewitness.projects.artifacts import MAX_LOCAL_ARTIFACT_BYTES
from notewitness.projects.private_fs import is_owner_private
from notewitness.projects.store import ProjectStore


@dataclass(frozen=True, slots=True)
class RunIntegrationResult:
    kind: str
    run_id: str
    event_ids: tuple[str, ...]
    target_ids: tuple[str, ...]
    project_sha256: str
    already_integrated: bool


def integrate_completed_run(
    project_root: str | Path, run_id: str
) -> RunIntegrationResult:
    """Validate and idempotently append one completed private run."""

    integrated = _integrate_publication(ProjectStore(project_root), run_id)
    publication = integrated.publication
    return RunIntegrationResult(
        kind=publication.kind,
        run_id=publication.run_id,
        event_ids=publication.event_ids,
        target_ids=publication.target_ids,
        project_sha256=integrated.project_sha256,
        already_integrated=integrated.added_record_count == 0,
    )


@dataclass(frozen=True, slots=True)
class _IntegratedPublication:
    """A verified publication and the result of its atomic project append."""

    publication: RunPublication
    project_sha256: str
    added_record_count: int


def _integrate_publication(store: ProjectStore, run_id: str) -> _IntegratedPublication:
    """Verify all private evidence before atomically appending its records."""

    completed_run = _completed_run_directory(store, run_id)
    publication = read_publication(completed_run / PUBLICATION_FILENAME)
    if publication.run_id != run_id:
        raise RunIntegrationError("Publication run identity does not match the request.")
    _verify_completed_artifacts(completed_run, publication)
    _verify_manifest_identity(completed_run, publication)

    added_record_count = 0

    def append(payload: dict[str, Any]) -> None:
        nonlocal added_record_count
        added_record_count = _append_publication_records(payload, publication)

    updated = store.mutate(append)
    return _IntegratedPublication(publication, updated.sha256, added_record_count)


def capture_source_identity(
    payload: Mapping[str, Any], source_id: str
) -> PublicationSourceIdentity:
    """Capture the source and rights records that authorize one completed run."""

    source = _unique_record(payload, "sources", source_id)
    source_sha256 = source.get("sha256")
    source_uri = source.get("uri")
    rights_id = source.get("rights_id")
    if not isinstance(source_sha256, str) or not isinstance(source_uri, str):
        raise RunIntegrationError("Run source identity is incomplete.")
    if not isinstance(rights_id, str):
        raise RunIntegrationError("Run source rights identity is incomplete.")
    rights = _unique_record(payload, "rights", rights_id)
    return PublicationSourceIdentity(
        source_id=source_id,
        source_sha256=source_sha256,
        source_uri=source_uri,
        rights_id=rights_id,
        source_record_sha256=_json_sha256(source),
        rights_record_sha256=_json_sha256(rights),
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
            copy.deepcopy(_unique_record(payload, name, record_id))
            for record_id in planned[name]
        )
        for name in PUBLICATION_COLLECTIONS
    }


def _append_publication_records(
    payload: dict[str, Any], publication: RunPublication
) -> int:
    """Append an all-or-nothing publication projection, if absent."""

    _require_current_source(payload, publication.source)
    states = _publication_presence(payload, publication)
    _require_complete_evidence(states)
    return _append_missing_records(payload, publication, states)


def _require_current_source(
    payload: Mapping[str, Any], expected: PublicationSourceIdentity
) -> None:
    current = capture_source_identity(payload, expected.source_id)
    if current != expected:
        raise RunIntegrationError(
            "Project source or rights changed after the model run completed."
        )


def _publication_presence(
    payload: Mapping[str, Any], publication: RunPublication
) -> dict[str, tuple[bool, ...]]:
    return {
        collection: _collection_presence(
            _collection_value(payload, collection),
            publication.records[collection],
            collection,
        )
        for collection in PUBLICATION_COLLECTIONS
    }


def _collection_presence(
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


def _require_complete_evidence(states: Mapping[str, tuple[bool, ...]]) -> None:
    evidence_presence = states["targets"] + states["events"]
    if evidence_presence and any(evidence_presence) and not all(evidence_presence):
        raise RunIntegrationError(
            "Completed run evidence is only partially present in the project."
        )


def _append_missing_records(
    payload: dict[str, Any],
    publication: RunPublication,
    states: Mapping[str, tuple[bool, ...]],
) -> int:
    added = 0
    for collection in PUBLICATION_COLLECTIONS:
        current = _collection_value(payload, collection)
        for record, present in zip(
            publication.records[collection], states[collection], strict=True
        ):
            if not present:
                current.append(copy.deepcopy(dict(record)))
                added += 1
    return added


def _collection_value(payload: Mapping[str, Any], name: str) -> list[dict[str, Any]]:
    value = payload.get(name)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise RunIntegrationError(f"Project collection {name!r} is malformed.")
    return value


def _unique_record(
    payload: Mapping[str, Any], collection: str, record_id: str
) -> dict[str, Any]:
    matches = [
        item
        for item in _collection_value(payload, collection)
        if item.get("id") == record_id
    ]
    if len(matches) != 1:
        raise RunIntegrationError(
            f"Project requires exactly one {collection} record {record_id!r}."
        )
    return matches[0]


def _verify_completed_artifacts(run_directory: Path, publication: RunPublication) -> None:
    """Verify each sealed artifact before any project-store mutation."""

    for relative_path, expected in publication.artifact_sha256s.items():
        relative = PurePosixPath(artifact_relative_path(relative_path))
        actual, _ = private_file_identity(
            run_directory.joinpath(*relative.parts),
            MAX_LOCAL_ARTIFACT_BYTES,
        )
        if actual != expected:
            raise RunIntegrationError(f"Completed run artifact changed: {relative_path}")


def _verify_manifest_identity(run_directory: Path, publication: RunPublication) -> None:
    """Verify manifest, source, model, and normalized-output identity."""

    manifest = _completed_manifest(run_directory, publication.run_id)
    _verify_normalized_identity(run_directory, publication)
    if publication.kind == "analysis":
        model_hashes = _analysis_model_hashes(manifest, publication.source)
    else:
        model_hashes = _transcript_model_hashes(run_directory, manifest, publication)
    if model_hashes != publication.model_sha256s:
        raise RunIntegrationError("Completed run model identity changed.")


def _verify_normalized_identity(run_directory: Path, publication: RunPublication) -> None:
    name = (
        "analysis.normalized.json"
        if publication.kind == "analysis"
        else "transcript.normalized.json"
    )
    normalized = _read_json_artifact(
        run_directory / name,
        MAX_LOCAL_ARTIFACT_BYTES,
        "Completed normalized output",
    )
    if (
        normalized.get("run_id") != publication.run_id
        or normalized.get("source_id") != publication.source.source_id
    ):
        raise RunIntegrationError("Completed normalized run identity changed.")


def _analysis_model_hashes(
    manifest: Mapping[str, Any], source: PublicationSourceIdentity
) -> tuple[str, ...]:
    if (
        manifest.get("source_id") != source.source_id
        or manifest.get("source_sha256") != source.source_sha256
    ):
        raise RunIntegrationError("Completed analysis source identity changed.")
    stages = manifest.get("stages")
    if not isinstance(stages, list):
        raise RunIntegrationError("Completed analysis manifest is malformed.")
    return _artifact_hashes(stages, "model_sha256")


def _transcript_model_hashes(
    run_directory: Path, manifest: Mapping[str, Any], publication: RunPublication
) -> tuple[str, ...]:
    _verify_transcript_evidence(run_directory, publication)
    checksums = manifest.get("source_checksums")
    if not isinstance(checksums, list) or not _contains_source_checksum(
        checksums, publication.source
    ):
        raise RunIntegrationError("Completed transcript source identity changed.")
    models = manifest.get("model_artifacts")
    if not isinstance(models, list):
        raise RunIntegrationError("Completed transcript manifest is malformed.")
    return _artifact_hashes(models, "sha256")


def _verify_transcript_evidence(run_directory: Path, publication: RunPublication) -> None:
    canonical = _read_json_artifact(
        run_directory / "transcript.evidence.json",
        MAX_PUBLICATION_BYTES,
        "Completed transcript evidence",
    )
    raw_hashes = {
        digest
        for path, digest in publication.artifact_sha256s.items()
        if path.startswith("raw/")
    }
    if (
        canonical.get("run_id") != publication.run_id
        or canonical.get("normalized_transcript_sha256")
        != publication.artifact_sha256s["transcript.normalized.json"]
        or canonical.get("raw_response_sha256") not in raw_hashes
    ):
        raise RunIntegrationError("Completed transcript evidence identity changed.")


def _contains_source_checksum(
    checksums: list[Any], source: PublicationSourceIdentity
) -> bool:
    return any(
        isinstance(item, dict)
        and item.get("source_id") == source.source_id
        and item.get("sha256") == source.source_sha256
        for item in checksums
    )


def _artifact_hashes(items: list[Any], field: str) -> tuple[str, ...]:
    return tuple(sorted({str(item.get(field)) for item in items if isinstance(item, dict)}))


def _completed_run_directory(store: ProjectStore, run_id: str) -> Path:
    match = RUN_ID_PATTERN.fullmatch(run_id)
    if match is None:
        raise RunIntegrationError("Run ID must identify a completed local run.")
    token = match.group(2)
    name = f"analysis-{token}" if match.group(1) == "analysis" else token
    runs = store.root / "runs"
    _require_private_directory(runs)
    directory = runs / name
    _require_private_directory(directory)
    return directory


def _require_private_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RunIntegrationError("Completed run directory is unavailable.") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or not is_owner_private(metadata)
    ):
        raise RunIntegrationError("Completed run directory is not owner-private.")


def _read_json_artifact(path: Path, maximum: int, label: str) -> dict[str, Any]:
    raw = read_private_file(path, maximum)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunIntegrationError(f"{label} contains invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise RunIntegrationError(f"{label} must be a JSON object.")
    return payload


def _completed_manifest(run_directory: Path, run_id: str) -> dict[str, Any]:
    manifest = _read_json_artifact(
        run_directory / "manifest.completed.json",
        MAX_PUBLICATION_BYTES,
        "Completed run manifest",
    )
    if manifest.get("state") != "completed":
        raise RunIntegrationError("Run manifest is not completed.")
    if manifest.get("run_id") != run_id:
        raise RunIntegrationError("Completed manifest run identity changed.")
    return manifest


def _json_sha256(value: Mapping[str, Any]) -> str:
    """Return the canonical identity digest for one project record."""

    try:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RunIntegrationError("Project identity record is not finite JSON.") from exc
    return hashlib.sha256(raw).hexdigest()
