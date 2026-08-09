"""Strict decoding of sealed completed-run publication envelopes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from notewitness.application._run_integration_artifacts import read_private_file
from notewitness.application._run_integration_support import (
    MAX_PUBLICATION_BYTES,
    RunIntegrationError,
)
from notewitness.application._run_publication_contract import (
    PUBLICATION_COLLECTIONS,
    PublicationSourceIdentity,
    RunPublication,
)


def read_publication(path: Path) -> RunPublication:
    """Load and validate a completed private-run publication envelope."""

    payload = decode_publication(read_private_file(path, MAX_PUBLICATION_BYTES))
    source, artifacts, models, records = publication_fields(payload)
    normalized_records = publication_records(records)
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


def decode_publication(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RunIntegrationError("Run publication contains invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise RunIntegrationError("Run publication has an invalid schema.")
    return payload


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key {key!r}")
        result[key] = value
    return result


def publication_fields(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, str], list[str], dict[str, Any]]:
    require_publication_schema(payload)
    source = payload["source"]
    records = payload["records"]
    artifacts = payload["artifact_sha256s"]
    models = payload["model_sha256s"]
    require_publication_source(source)
    require_publication_records(records)
    require_publication_artifacts(artifacts)
    require_publication_models(models)
    return source, artifacts, models, records


def require_publication_schema(payload: Mapping[str, Any]) -> None:
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


def require_publication_artifacts(artifacts: Any) -> None:
    if not isinstance(artifacts, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in artifacts.items()
    ):
        raise RunIntegrationError("Run publication artifacts are malformed.")


def require_publication_models(models: Any) -> None:
    if not isinstance(models, list) or any(
        not isinstance(item, str) for item in models
    ):
        raise RunIntegrationError("Run publication model identities are malformed.")


def require_publication_source(source: Any) -> None:
    if not isinstance(source, dict) or set(source) != {
        "rights_id",
        "rights_record_sha256",
        "source_id",
        "source_record_sha256",
        "source_sha256",
        "source_uri",
    }:
        raise RunIntegrationError("Run publication source identity is malformed.")


def require_publication_records(records: Any) -> None:
    if not isinstance(records, dict) or set(records) != set(PUBLICATION_COLLECTIONS):
        raise RunIntegrationError("Run publication records are malformed.")


def publication_records(
    records: Mapping[str, Any],
) -> dict[str, tuple[Mapping[str, Any], ...]]:
    normalized_records: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for collection in PUBLICATION_COLLECTIONS:
        items = records[collection]
        if not isinstance(items, list) or any(
            not isinstance(item, dict) for item in items
        ):
            raise RunIntegrationError("Run publication record list is malformed.")
        normalized_records[collection] = tuple(items)
    return normalized_records
