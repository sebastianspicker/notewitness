"""Completed-run identity verification before a project mutation may begin."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from notewitness.application._run_integration_artifacts import (
    artifact_relative_path,
    completed_manifest,
    private_file_identity,
    read_json_artifact,
)
from notewitness.application._run_integration_support import (
    MAX_PUBLICATION_BYTES,
    RunIntegrationError,
)
from notewitness.application._run_publication_contract import (
    PublicationSourceIdentity,
    RunPublication,
)
from notewitness.local_artifacts import MAX_LOCAL_ARTIFACT_BYTES


def verify_completed_artifacts(run_directory: Path, publication: RunPublication) -> None:
    """Verify each sealed artifact before any project-store mutation."""

    for relative_path, expected in publication.artifact_sha256s.items():
        relative = PurePosixPath(artifact_relative_path(relative_path))
        actual, _ = private_file_identity(
            run_directory.joinpath(*relative.parts),
            MAX_LOCAL_ARTIFACT_BYTES,
        )
        if actual != expected:
            raise RunIntegrationError(f"Completed run artifact changed: {relative_path}")


def verify_manifest_identity(run_directory: Path, publication: RunPublication) -> None:
    """Verify manifest, source, model, and normalized-output identity."""

    manifest = completed_manifest(run_directory, publication.run_id)
    verify_normalized_identity(run_directory, publication)
    if publication.kind == "analysis":
        model_hashes = analysis_model_hashes(manifest, publication.source)
    else:
        model_hashes = transcript_model_hashes(run_directory, manifest, publication)
    if model_hashes != publication.model_sha256s:
        raise RunIntegrationError("Completed run model identity changed.")


def verify_normalized_identity(run_directory: Path, publication: RunPublication) -> None:
    name = (
        "analysis.normalized.json"
        if publication.kind == "analysis"
        else "transcript.normalized.json"
    )
    normalized = read_json_artifact(
        run_directory / name,
        MAX_LOCAL_ARTIFACT_BYTES,
        "Completed normalized output",
    )
    if (
        normalized.get("run_id") != publication.run_id
        or normalized.get("source_id") != publication.source.source_id
    ):
        raise RunIntegrationError("Completed normalized run identity changed.")


def analysis_model_hashes(
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
    return artifact_hashes(stages, "model_sha256")


def transcript_model_hashes(
    run_directory: Path, manifest: Mapping[str, Any], publication: RunPublication
) -> tuple[str, ...]:
    verify_transcript_evidence(run_directory, publication)
    checksums = manifest.get("source_checksums")
    if not isinstance(checksums, list) or not contains_source_checksum(
        checksums, publication.source
    ):
        raise RunIntegrationError("Completed transcript source identity changed.")
    models = manifest.get("model_artifacts")
    if not isinstance(models, list):
        raise RunIntegrationError("Completed transcript manifest is malformed.")
    return artifact_hashes(models, "sha256")


def verify_transcript_evidence(run_directory: Path, publication: RunPublication) -> None:
    canonical = read_json_artifact(
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


def contains_source_checksum(
    checksums: list[Any], source: PublicationSourceIdentity
) -> bool:
    return any(
        isinstance(item, dict)
        and item.get("source_id") == source.source_id
        and item.get("sha256") == source.source_sha256
        for item in checksums
    )


def artifact_hashes(items: list[Any], field: str) -> tuple[str, ...]:
    return tuple(sorted({str(item.get(field)) for item in items if isinstance(item, dict)}))
