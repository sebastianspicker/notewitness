"""Durable, idempotent publication of completed local model runs."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Iterable, Mapping

from notewitness.evidence_contract import ID_PATTERN, SHA256_PATTERN
from notewitness.local_artifacts import (
    MAX_LOCAL_ARTIFACT_BYTES,
    write_new_private_json,
)
from notewitness.project_store import ProjectStore


PUBLICATION_FILENAME = "publication.completed.json"
MAX_PUBLICATION_BYTES = 16 * 1024 * 1024
_PUBLICATION_COLLECTIONS = ("actors", "generators", "targets", "events")
_RUN_ID = re.compile(r"^run:(?:(analysis)-)?([0-9a-f]{32})$")


class RunIntegrationError(RuntimeError):
    """A completed private run cannot be integrated safely."""


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
        for value in (self.source_record_sha256, self.rights_record_sha256):
            if not SHA256_PATTERN.fullmatch(value):
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
        if self.kind not in {"analysis", "transcription"}:
            raise ValueError("Publication kind is invalid.")
        match = _RUN_ID.fullmatch(self.run_id)
        if match is None or (match.group(1) == "analysis") != (
            self.kind == "analysis"
        ):
            raise ValueError("Publication run_id does not match its kind.")
        if not isinstance(self.source, PublicationSourceIdentity):
            raise ValueError("Publication source identity is invalid.")
        if (
            not self.model_sha256s
            or tuple(sorted(set(self.model_sha256s))) != self.model_sha256s
            or any(not SHA256_PATTERN.fullmatch(item) for item in self.model_sha256s)
        ):
            raise ValueError("Publication model identities are invalid.")
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
                for name in _PUBLICATION_COLLECTIONS
            },
            "run_id": self.run_id,
            "schema_version": 1,
            "source": self.source.as_dict(),
        }


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
        _require_current_source(payload, publication.source)
        states: dict[str, tuple[bool, ...]] = {}
        for collection in _PUBLICATION_COLLECTIONS:
            current = _collection(payload, collection)
            presence: list[bool] = []
            for record in publication.records[collection]:
                matches = [item for item in current if item.get("id") == record["id"]]
                if not matches:
                    presence.append(False)
                    continue
                if len(matches) != 1 or matches[0] != record:
                    raise RunIntegrationError(
                        f"Existing {collection} record {record['id']!r} conflicts "
                        "with the completed run."
                    )
                presence.append(True)
            states[collection] = tuple(presence)

        evidence_presence = states["targets"] + states["events"]
        if evidence_presence and any(evidence_presence) and not all(evidence_presence):
            raise RunIntegrationError(
                "Completed run evidence is only partially present in the project."
            )
        for collection in _PUBLICATION_COLLECTIONS:
            current = _collection(payload, collection)
            for record, present in zip(
                publication.records[collection], states[collection], strict=True
            ):
                if not present:
                    current.append(copy.deepcopy(dict(record)))
                    added_record_count += 1

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
    raw = _read_private_file(path, MAX_PUBLICATION_BYTES)

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate object key {key!r}")
            result[key] = value
        return result

    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RunIntegrationError("Run publication contains invalid JSON.") from exc
    if not isinstance(payload, dict) or set(payload) != {
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
    source = payload["source"]
    records = payload["records"]
    artifacts = payload["artifact_sha256s"]
    models = payload["model_sha256s"]
    if not isinstance(source, dict) or set(source) != {
        "rights_id",
        "rights_record_sha256",
        "source_id",
        "source_record_sha256",
        "source_sha256",
        "source_uri",
    }:
        raise RunIntegrationError("Run publication source identity is malformed.")
    if not isinstance(records, dict) or set(records) != set(
        _PUBLICATION_COLLECTIONS
    ):
        raise RunIntegrationError("Run publication records are malformed.")
    if not isinstance(artifacts, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in artifacts.items()
    ):
        raise RunIntegrationError("Run publication artifacts are malformed.")
    if not isinstance(models, list) or any(
        not isinstance(item, str) for item in models
    ):
        raise RunIntegrationError("Run publication model identities are malformed.")
    normalized_records: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for collection in _PUBLICATION_COLLECTIONS:
        items = records[collection]
        if not isinstance(items, list) or any(
            not isinstance(item, dict) for item in items
        ):
            raise RunIntegrationError("Run publication record list is malformed.")
        normalized_records[collection] = tuple(items)
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
    required = {
        "manifest.completed.json",
        (
            "analysis.normalized.json"
            if kind == "analysis"
            else "transcript.normalized.json"
        ),
    }
    if kind == "transcription":
        required.add("transcript.evidence.json")
    if not required.issubset(artifacts):
        raise ValueError("Publication does not identify all required artifacts.")


def _validate_records(publication: RunPublication) -> None:
    if not isinstance(publication.records, Mapping) or set(
        publication.records
    ) != set(_PUBLICATION_COLLECTIONS):
        raise ValueError("Publication record collections are invalid.")
    all_ids: list[str] = []
    for collection in _PUBLICATION_COLLECTIONS:
        records = publication.records[collection]
        if not isinstance(records, tuple):
            raise ValueError("Publication record collections must be immutable.")
        for record in records:
            if not isinstance(record, Mapping):
                raise ValueError("Publication records must be mappings.")
            record_id = record.get("id")
            if not isinstance(record_id, str) or not ID_PATTERN.fullmatch(record_id):
                raise ValueError("Publication record ID is invalid.")
            all_ids.append(record_id)
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("Publication record IDs must be globally unique.")
    try:
        json.dumps(publication.as_dict(), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Publication records must be finite JSON data.") from exc

    generators = {
        str(item["id"]): item for item in publication.records["generators"]
    }
    actors = {str(item["id"]) for item in publication.records["actors"]}
    targets = {str(item["id"]): item for item in publication.records["targets"]}
    events = {str(item["id"]): item for item in publication.records["events"]}
    if {item.removeprefix("target:") for item in targets} != {
        item.removeprefix("event:") for item in events
    }:
        raise ValueError("Publication targets and events are not paired.")
    for target in targets.values():
        if target.get("source_id") != publication.source.source_id:
            raise ValueError("Publication target source identity is invalid.")
    for event in events.values():
        target_ids = event.get("target_ids")
        expected_target_id = f"target:{str(event['id']).removeprefix('event:')}"
        if (
            not isinstance(target_ids, list)
            or target_ids != [expected_target_id]
            or expected_target_id not in targets
        ):
            raise ValueError("Publication event target identity is invalid.")
        generator_id = event.get("generator_id")
        if generator_id not in generators:
            raise ValueError("Publication event generator identity is invalid.")
        actor_id = event.get("actor_id")
        if actor_id is not None and actor_id not in actors:
            raise ValueError("Publication event actor identity is invalid.")
        if event.get("rights_id") != publication.source.rights_id:
            raise ValueError("Publication event rights identity is invalid.")
    model_states = {
        f"sha256:{digest}" for digest in publication.model_sha256s
    }
    if any(
        generator.get("weight_hash_state") not in model_states
        for generator in generators.values()
    ):
        raise ValueError("Publication generator model identity is invalid.")
    _validate_run_record_ids(publication, targets, events)


def _validate_run_record_ids(
    publication: RunPublication,
    targets: Mapping[str, Mapping[str, Any]],
    events: Mapping[str, Mapping[str, Any]],
) -> None:
    if publication.kind == "transcription":
        token = hashlib.sha256(publication.run_id.encode("utf-8")).hexdigest()[:16]
        expected_targets = {
            f"target:asr-{token}-{index}"
            for index in range(1, len(targets) + 1)
        }
        expected_events = {
            f"event:asr-{token}-{index}"
            for index in range(1, len(events) + 1)
        }
        if set(targets) != expected_targets or set(events) != expected_events:
            raise ValueError("Transcript publication IDs are not run-derived.")
        if any(
            not isinstance(event.get("body"), Mapping)
            or event["body"].get("run_id") != publication.run_id
            for event in events.values()
        ):
            raise ValueError("Transcript publication body run identity is invalid.")
        return
    token = publication.run_id.removeprefix("run:analysis-")
    if any(
        not record_id.startswith(f"target:analysis-{token}-")
        for record_id in targets
    ) or any(
        not record_id.startswith(f"event:analysis-{token}-")
        for record_id in events
    ):
        raise ValueError("Analysis publication IDs are not run-derived.")


def _artifact_relative_path(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Publication artifact path must be a string.")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or "\\" in value
        or not relative.parts
        or len(relative.parts) > 2
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.name == PUBLICATION_FILENAME
        or (len(relative.parts) == 2 and relative.parts[0] != "raw")
    ):
        raise ValueError("Publication artifact path is unsafe.")
    return relative.as_posix()


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
    manifest = _read_json_artifact(
        run_directory / "manifest.completed.json",
        MAX_PUBLICATION_BYTES,
        "Completed run manifest",
    )
    if manifest.get("state") != "completed":
        raise RunIntegrationError("Run manifest is not completed.")
    if manifest.get("run_id") != publication.run_id:
        raise RunIntegrationError("Completed manifest run identity changed.")
    normalized_name = (
        "analysis.normalized.json"
        if publication.kind == "analysis"
        else "transcript.normalized.json"
    )
    normalized = _read_json_artifact(
        run_directory / normalized_name,
        MAX_LOCAL_ARTIFACT_BYTES,
        "Completed normalized output",
    )
    if (
        normalized.get("run_id") != publication.run_id
        or normalized.get("source_id") != publication.source.source_id
    ):
        raise RunIntegrationError("Completed normalized run identity changed.")
    if publication.kind == "analysis":
        if (
            manifest.get("source_id") != publication.source.source_id
            or manifest.get("source_sha256") != publication.source.source_sha256
        ):
            raise RunIntegrationError("Completed analysis source identity changed.")
        stages = manifest.get("stages")
        if not isinstance(stages, list):
            raise RunIntegrationError("Completed analysis manifest is malformed.")
        model_hashes = tuple(
            sorted(
                {
                    str(item.get("model_sha256"))
                    for item in stages
                    if isinstance(item, dict)
                }
            )
        )
    else:
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
        checksums = manifest.get("source_checksums")
        if not isinstance(checksums, list) or not any(
            isinstance(item, dict)
            and item.get("source_id") == publication.source.source_id
            and item.get("sha256") == publication.source.source_sha256
            for item in checksums
        ):
            raise RunIntegrationError("Completed transcript source identity changed.")
        models = manifest.get("model_artifacts")
        if not isinstance(models, list):
            raise RunIntegrationError("Completed transcript manifest is malformed.")
        model_hashes = tuple(
            sorted(
                {
                    str(item.get("sha256"))
                    for item in models
                    if isinstance(item, dict)
                }
            )
        )
    if model_hashes != publication.model_sha256s:
        raise RunIntegrationError("Completed run model identity changed.")


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
