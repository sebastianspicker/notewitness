"""Ordering boundary for verified publication and atomic project append."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from notewitness.application._run_integration_artifacts import run_directory
from notewitness.application._run_integration_publication import read_publication
from notewitness.application._run_integration_records import append_publication_records
from notewitness.application._run_integration_support import (
    PUBLICATION_FILENAME,
    RunIntegrationError,
)
from notewitness.application._run_integration_verification import (
    verify_completed_artifacts,
    verify_manifest_identity,
)
from notewitness.application._run_publication_contract import RunPublication
from notewitness.project_store import ProjectStore


@dataclass(frozen=True, slots=True)
class IntegratedPublication:
    """A verified publication and the result of its atomic project append."""

    publication: RunPublication
    project_sha256: str
    added_record_count: int


def integrate_publication(store: ProjectStore, run_id: str) -> IntegratedPublication:
    """Verify all private evidence before atomically appending its records."""

    completed_run = run_directory(store, run_id)
    publication = read_publication(completed_run / PUBLICATION_FILENAME)
    if publication.run_id != run_id:
        raise RunIntegrationError("Publication run identity does not match the request.")
    verify_completed_artifacts(completed_run, publication)
    verify_manifest_identity(completed_run, publication)

    added_record_count = 0

    def append(payload: dict[str, Any]) -> None:
        nonlocal added_record_count
        added_record_count = append_publication_records(payload, publication)

    updated = store.mutate(append)
    return IntegratedPublication(publication, updated.sha256, added_record_count)
