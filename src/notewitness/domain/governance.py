"""Governance workflow contracts; these do not make legal determinations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ProjectRole(StrEnum):
    OWNER = "owner"
    DATA_STEWARD = "data_steward"
    ANNOTATOR = "annotator"
    VIEWER = "viewer"


class ParticipantRequestKind(StrEnum):
    ACCESS = "access"
    CORRECTION = "correction"
    WITHDRAWAL = "withdrawal"
    ERASURE = "erasure"


class ParticipantRequestState(StrEnum):
    RECEIVED = "received"
    AUTHORITY_PENDING = "authority_pending"
    IMPACT_REVIEW = "impact_review"
    AUTHORIZED = "authorized"
    COMPLETED = "completed"
    DENIED = "denied"


class PackageTrustState(StrEnum):
    AUTHORIZED = "authorized"
    QUARANTINED = "quarantined"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RoleAssignment:
    actor_id: str
    role: ProjectRole
    granted_by: str
    authority_record_id: str


@dataclass(frozen=True, slots=True)
class ParticipantRequest:
    request_id: str
    kind: ParticipantRequestKind
    state: ParticipantRequestState
    project_actor_id: str | None
    requested_scope: tuple[str, ...]
    authority_evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.request_id:
            raise ValueError("request_id must not be empty.")
        if not self.requested_scope:
            raise ValueError("Participant requests require an explicit scope.")
        if self.state in {
            ParticipantRequestState.AUTHORIZED,
            ParticipantRequestState.COMPLETED,
        } and not self.authority_evidence_ids:
            raise ValueError("Authorized requests require authority evidence.")


@dataclass(frozen=True, slots=True)
class DeletionImpactItem:
    record_or_path_id: str
    category: str
    action: str


@dataclass(frozen=True, slots=True)
class DeletionImpactPlan:
    request_id: str
    generated_by: str
    items: tuple[DeletionImpactItem, ...]
    backup_expiry_documented: bool
    approved_by: str | None = None

    @property
    def executable(self) -> bool:
        return bool(self.items and self.backup_expiry_documented and self.approved_by)


@dataclass(frozen=True, slots=True)
class AnnotationPackageManifest:
    source_project_id: str
    schema_version: str
    media_checksums: tuple[str, ...]
    annotator_id: str
    revision_parent_ids: tuple[str, ...]
    exported_at: str
    signature: str | None = None


@dataclass(frozen=True, slots=True)
class PackageImportDecision:
    state: PackageTrustState
    mapped_role: ProjectRole | None
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.state is PackageTrustState.AUTHORIZED and self.mapped_role is None:
            raise ValueError("Authorized packages require an explicit local role mapping.")
        if self.state is not PackageTrustState.AUTHORIZED and not self.reasons:
            raise ValueError("Non-authorized package decisions require reasons.")

