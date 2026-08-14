"""Durable, idempotent public facade for completed local model runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from notewitness.application._run_integration_artifacts import completed_artifact_sha256s
from notewitness.application._run_integration_orchestration import integrate_publication
from notewitness.application._run_integration_records import (
    capture_source_identity,
    select_publication_records,
)
from notewitness.application._run_integration_support import (
    MAX_PUBLICATION_BYTES,
    PUBLICATION_FILENAME,
    RunIntegrationError,
)
from notewitness.application._run_publication_contract import (
    PublicationSourceIdentity,
    RunPublication,
)
from notewitness.local_artifacts import write_new_private_json
from notewitness.project_store import ProjectStore


__all__ = (
    "MAX_PUBLICATION_BYTES",
    "PUBLICATION_FILENAME",
    "PublicationSourceIdentity",
    "RunIntegrationError",
    "RunIntegrationResult",
    "RunPublication",
    "capture_source_identity",
    "completed_artifact_sha256s",
    "integrate_completed_run",
    "select_publication_records",
    "write_completed_publication",
)


@dataclass(frozen=True, slots=True)
class RunIntegrationResult:
    kind: str
    run_id: str
    event_ids: tuple[str, ...]
    target_ids: tuple[str, ...]
    project_sha256: str
    already_integrated: bool


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

    integrated = integrate_publication(ProjectStore(project_root), run_id)
    publication = integrated.publication
    return RunIntegrationResult(
        kind=publication.kind,
        run_id=publication.run_id,
        event_ids=publication.event_ids,
        target_ids=publication.target_ids,
        project_sha256=integrated.project_sha256,
        already_integrated=integrated.added_record_count == 0,
    )
