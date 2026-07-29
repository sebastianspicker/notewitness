"""Durable, idempotent publication of completed local model runs."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any, Iterable, Mapping

from notewitness.application._run_publication_contract import (
    PUBLICATION_COLLECTIONS as _PUBLICATION_COLLECTIONS,
    RUN_ID_PATTERN as _RUN_ID,
    PublicationSourceIdentity,
    RunPublication,
)
from notewitness.local_artifacts import (
    MAX_LOCAL_ARTIFACT_BYTES,
    write_new_private_json,
)
from notewitness.project_store import ProjectStore


PUBLICATION_FILENAME = "publication.completed.json"
MAX_PUBLICATION_BYTES = 16 * 1024 * 1024


class RunIntegrationError(RuntimeError):
    """A completed private run cannot be integrated safely."""


@dataclass(frozen=True, slots=True)
class RunIntegrationResult:
    kind: str
    run_id: str
    event_ids: tuple[str, ...]
    target_ids: tuple[str, ...]
    project_sha256: str
    already_integrated: bool


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
        for name in _PUBLICATION_COLLECTIONS
    }


def completed_artifact_sha256s(
    run_directory: Path, relative_paths: Iterable[str]
) -> dict[str, str]:
    """Identify completed private artifacts before sealing a publication."""

    result: dict[str, str] = {}
    for relative_path in relative_paths:
        normalized = _artifact_relative_path(relative_path)
        if normalized in result:
            raise RunIntegrationError("Publication artifact paths must be unique.")
        path = run_directory.joinpath(*PurePosixPath(normalized).parts)
        digest, _ = _private_file_identity(path, MAX_LOCAL_ARTIFACT_BYTES)
        result[normalized] = digest
    return result


def write_completed_publication(
    run_directory: Path, publication: RunPublication
) -> Path:
    """Seal one immutable integration envelope beside completed model output."""

    return write_new_private_json(
        run_directory / PUBLICATION_FILENAME,
        publication.as_dict(),
    )


def integrate_completed_run(
    project_root: str | Path, run_id: str
) -> RunIntegrationResult:
    """Validate and idempotently append one completed private run."""

    store = ProjectStore(project_root)
    run_directory = _run_directory(store, run_id)
    publication = _read_publication(run_directory / PUBLICATION_FILENAME)
    if publication.run_id != run_id:
        raise RunIntegrationError("Publication run identity does not match the request.")
    _verify_completed_artifacts(run_directory, publication)
    _verify_manifest_identity(run_directory, publication)

    added_record_count = 0

    def integrate(payload: dict[str, Any]) -> None:
        nonlocal added_record_count
        added_record_count = _append_publication_records(payload, publication)

    updated = store.mutate(integrate)
    return RunIntegrationResult(
        kind=publication.kind,
        run_id=publication.run_id,
        event_ids=publication.event_ids,
        target_ids=publication.target_ids,
        project_sha256=updated.sha256,
        already_integrated=added_record_count == 0,
    )


def _read_publication(path: Path) -> RunPublication:
    payload = _decode_publication(_read_private_file(path, MAX_PUBLICATION_BYTES))
    source, artifacts, models, records = _publication_fields(payload)
    normalized_records = _publication_records(records)
    try:
        return RunPublication(
            kind=payload["kind"],
            run_id=payload["run_id"],
            source=PublicationSourceIdentity(**source),
            model_sha256s=tuple(models),
            artifact_sha256s=artifacts,
            records=normalized_records,
        )
    except (TypeError, ValueError) as exc:
        raise RunIntegrationError("Run publication violates its contract.") from exc


def _decode_publication(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RunIntegrationError("Run publication contains invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise RunIntegrationError("Run publication has an invalid schema.")
    return payload


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key {key!r}")
        result[key] = value
    return result


def _publication_fields(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, str], list[str], dict[str, Any]]:
    _require_publication_schema(payload)
    source = payload["source"]
    records = payload["records"]
    artifacts = payload["artifact_sha256s"]
    models = payload["model_sha256s"]
    _require_publication_source(source)
    _require_publication_records(records)
    _require_publication_artifacts(artifacts)
    _require_publication_models(models)
    return source, artifacts, models, records


def _require_publication_schema(payload: Mapping[str, Any]) -> None:
    if set(payload) != {
        "artifact_sha256s",
        "kind",
        "model_sha256s",
        "records",
        "run_id",
        "schema_version",
        "source",
    }:
        raise RunIntegrationError("Run publication has an invalid schema.")
    if payload["schema_version"] != 1:
        raise RunIntegrationError("Run publication schema version is unsupported.")


def _require_publication_artifacts(artifacts: Any) -> None:
    if not isinstance(artifacts, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in artifacts.items()
    ):
        raise RunIntegrationError("Run publication artifacts are malformed.")


def _require_publication_models(models: Any) -> None:
    if not isinstance(models, list) or any(
        not isinstance(item, str) for item in models
    ):
        raise RunIntegrationError("Run publication model identities are malformed.")


def _require_publication_source(source: Any) -> None:
    if not isinstance(source, dict) or set(source) != {
        "rights_id",
        "rights_record_sha256",
        "source_id",
        "source_record_sha256",
        "source_sha256",
        "source_uri",
    }:
        raise RunIntegrationError("Run publication source identity is malformed.")


def _require_publication_records(records: Any) -> None:
    if not isinstance(records, dict) or set(records) != set(_PUBLICATION_COLLECTIONS):
        raise RunIntegrationError("Run publication records are malformed.")


def _publication_records(
    records: Mapping[str, Any],
) -> dict[str, tuple[Mapping[str, Any], ...]]:
    normalized_records: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for collection in _PUBLICATION_COLLECTIONS:
        items = records[collection]
        if not isinstance(items, list) or any(
            not isinstance(item, dict) for item in items
        ):
            raise RunIntegrationError("Run publication record list is malformed.")
        normalized_records[collection] = tuple(items)
    return normalized_records


def _append_publication_records(
    payload: dict[str, Any], publication: RunPublication
) -> int:
    _require_current_source(payload, publication.source)
    states = _publication_presence(payload, publication)
    _require_complete_evidence(states)
    return _append_missing_records(payload, publication, states)


def _publication_presence(
    payload: Mapping[str, Any], publication: RunPublication
) -> dict[str, tuple[bool, ...]]:
    return {
        collection: _collection_presence(
            _collection(payload, collection), publication.records[collection], collection
        )
        for collection in _PUBLICATION_COLLECTIONS
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
    for collection in _PUBLICATION_COLLECTIONS:
        current = _collection(payload, collection)
        for record, present in zip(
            publication.records[collection], states[collection], strict=True
        ):
            if not present:
                current.append(copy.deepcopy(dict(record)))
                added += 1
    return added


def _artifact_relative_path(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Publication artifact path must be a string.")
    relative = PurePosixPath(value)
    if _unsafe_artifact_path(relative, value):
        raise ValueError("Publication artifact path is unsafe.")
    return relative.as_posix()


def _unsafe_artifact_path(relative: PurePosixPath, value: str) -> bool:
    return any(
        (
            _invalid_artifact_root(relative, value),
            _invalid_artifact_parts(relative),
            relative.name == PUBLICATION_FILENAME,
            _invalid_nested_artifact(relative),
        )
    )


def _invalid_artifact_root(relative: PurePosixPath, value: str) -> bool:
    return relative.is_absolute() or "\\" in value or not relative.parts


def _invalid_artifact_parts(relative: PurePosixPath) -> bool:
    return len(relative.parts) > 2 or any(
        part in {"", ".", ".."} for part in relative.parts
    )


def _invalid_nested_artifact(relative: PurePosixPath) -> bool:
    return len(relative.parts) == 2 and relative.parts[0] != "raw"


def _verify_completed_artifacts(
    run_directory: Path, publication: RunPublication
) -> None:
    for relative_path, expected in publication.artifact_sha256s.items():
        relative = PurePosixPath(_artifact_relative_path(relative_path))
        actual, _ = _private_file_identity(
            run_directory.joinpath(*relative.parts),
            MAX_LOCAL_ARTIFACT_BYTES,
        )
        if actual != expected:
            raise RunIntegrationError(
                f"Completed run artifact changed: {relative_path}"
            )


def _verify_manifest_identity(
    run_directory: Path, publication: RunPublication
) -> None:
    manifest = _completed_manifest(run_directory, publication)
    _verify_normalized_identity(run_directory, publication)
    if publication.kind == "analysis":
        model_hashes = _analysis_model_hashes(manifest, publication.source)
    else:
        model_hashes = _transcript_model_hashes(run_directory, manifest, publication)
    if model_hashes != publication.model_sha256s:
        raise RunIntegrationError("Completed run model identity changed.")


def _completed_manifest(run_directory: Path, publication: RunPublication) -> dict[str, Any]:
    manifest = _read_json_artifact(
        run_directory / "manifest.completed.json",
        MAX_PUBLICATION_BYTES,
        "Completed run manifest",
    )
    if manifest.get("state") != "completed":
        raise RunIntegrationError("Run manifest is not completed.")
    if manifest.get("run_id") != publication.run_id:
        raise RunIntegrationError("Completed manifest run identity changed.")
    return manifest


def _verify_normalized_identity(run_directory: Path, publication: RunPublication) -> None:
    name = "analysis.normalized.json" if publication.kind == "analysis" else "transcript.normalized.json"
    normalized = _read_json_artifact(
        run_directory / name, MAX_LOCAL_ARTIFACT_BYTES, "Completed normalized output"
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
        digest for path, digest in publication.artifact_sha256s.items() if path.startswith("raw/")
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


def _require_current_source(
    payload: Mapping[str, Any], expected: PublicationSourceIdentity
) -> None:
    current = capture_source_identity(payload, expected.source_id)
    if current != expected:
        raise RunIntegrationError(
            "Project source or rights changed after the model run completed."
        )


def _run_directory(store: ProjectStore, run_id: str) -> Path:
    match = _RUN_ID.fullmatch(run_id)
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
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise RunIntegrationError("Completed run directory is not owner-private.")


def _read_json_artifact(path: Path, maximum: int, label: str) -> dict[str, Any]:
    raw = _read_private_file(path, maximum)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunIntegrationError(f"{label} contains invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise RunIntegrationError(f"{label} must be a JSON object.")
    return payload


def _read_private_file(path: Path, maximum: int) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_size > maximum
        ):
            raise RunIntegrationError("Completed run artifact is not a bounded private file.")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if remaining:
            raise RunIntegrationError("Completed run artifact changed while reading.")
        return b"".join(chunks)
    except OSError as exc:
        raise RunIntegrationError("Completed run artifact is unavailable.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _private_file_identity(path: Path, maximum: int) -> tuple[str, int]:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) & 0o077
            or before.st_size > maximum
        ):
            raise RunIntegrationError("Completed run artifact is not a bounded private file.")
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise RunIntegrationError("Completed run artifact is unavailable.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if _stat_identity(before) != _stat_identity(after):
        raise RunIntegrationError("Completed run artifact changed while reading.")
    return digest.hexdigest(), size


def _stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _collection(
    payload: Mapping[str, Any], name: str
) -> list[dict[str, Any]]:
    value = payload.get(name)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise RunIntegrationError(f"Project collection {name!r} is malformed.")
    return value


def _unique_record(
    payload: Mapping[str, Any], collection: str, record_id: str
) -> dict[str, Any]:
    matches = [
        item
        for item in _collection(payload, collection)
        if item.get("id") == record_id
    ]
    if len(matches) != 1:
        raise RunIntegrationError(
            f"Project requires exactly one {collection} record {record_id!r}."
        )
    return matches[0]


def _json_sha256(value: Mapping[str, Any]) -> str:
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
