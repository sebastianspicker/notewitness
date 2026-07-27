"""Machine-readable adapter, model-weight, and installation contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Mapping


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ArtifactKind(StrEnum):
    CODE = "code"
    WEIGHTS = "weights"
    VOCABULARY = "vocabulary"
    CONFIGURATION = "configuration"


class NetworkRequirement(StrEnum):
    NONE = "none"
    INSTALL_ONLY = "install_only"
    RUNTIME = "runtime"


@dataclass(frozen=True, slots=True)
class ModelArtifact:
    artifact_id: str
    kind: ArtifactKind
    name: str
    version: str
    source_url: str
    sha256: str
    license_expression: str
    size_bytes: int
    network_requirement: NetworkRequirement
    intended_purposes: tuple[str, ...]
    known_limitations: tuple[str, ...]
    redistribution_permitted: bool
    commercial_use_permitted: bool | None

    def __post_init__(self) -> None:
        if not all((self.artifact_id, self.name, self.version, self.source_url)):
            raise ValueError("Model artifacts require identity, version, and source.")
        if not _SHA256.fullmatch(self.sha256):
            raise ValueError("Model artifacts require a lowercase SHA-256 digest.")
        if (
            not isinstance(self.size_bytes, int)
            or isinstance(self.size_bytes, bool)
            or self.size_bytes < 0
        ):
            raise ValueError("size_bytes must be a non-negative integer.")
        if not self.license_expression:
            raise ValueError("Code and weights require an explicit license expression.")


@dataclass(frozen=True, slots=True)
class ModelInstallPlan:
    artifact_ids: tuple[str, ...]
    total_size_bytes: int
    requires_network: bool
    licenses_presented: bool
    user_confirmed: bool

    def __post_init__(self) -> None:
        if not self.artifact_ids or any(not value for value in self.artifact_ids):
            raise ValueError("Model install plans require artifact IDs.")
        if len(self.artifact_ids) != len(set(self.artifact_ids)):
            raise ValueError("Model install artifact IDs must be unique.")
        if (
            not isinstance(self.total_size_bytes, int)
            or isinstance(self.total_size_bytes, bool)
            or self.total_size_bytes < 0
        ):
            raise ValueError("Model install size must be a non-negative integer.")
        if any(
            not isinstance(value, bool)
            for value in (
                self.requires_network,
                self.licenses_presented,
                self.user_confirmed,
            )
        ):
            raise ValueError("Model install decisions must be boolean values.")

    @property
    def executable(self) -> bool:
        return bool(
            self.artifact_ids and self.licenses_presented and self.user_confirmed
        )


@dataclass(frozen=True, slots=True)
class ArtifactVerification:
    artifact_id: str
    observed_sha256: str
    observed_size_bytes: int

    def __post_init__(self) -> None:
        if not self.artifact_id or not _SHA256.fullmatch(self.observed_sha256):
            raise ValueError(
                "Artifact verification requires an ID and lowercase SHA-256."
            )
        if (
            not isinstance(self.observed_size_bytes, int)
            or isinstance(self.observed_size_bytes, bool)
            or self.observed_size_bytes < 0
        ):
            raise ValueError(
                "Artifact verification size must be a non-negative integer."
            )

    def issues_for(self, artifact: ModelArtifact) -> tuple[str, ...]:
        issues: list[str] = []
        if self.artifact_id != artifact.artifact_id:
            issues.append("verification artifact ID does not match the ledger")
        if self.observed_sha256 != artifact.sha256:
            issues.append(f"artifact {artifact.artifact_id!r} checksum is not verified")
        if self.observed_size_bytes != artifact.size_bytes:
            issues.append(f"artifact {artifact.artifact_id!r} size is not verified")
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class AdapterManifest:
    adapter_id: str
    code_artifact_id: str
    weight_artifact_ids: tuple[str, ...]
    supported_stages: tuple[str, ...]
    supported_hardware: tuple[str, ...]
    intended_domain: str
    supported_features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not all(
            (self.adapter_id, self.code_artifact_id, self.intended_domain)
        ):
            raise ValueError("Adapter manifests require adapter, code, and domain IDs.")
        if self.code_artifact_id in self.weight_artifact_ids:
            raise ValueError("Adapter code and weight artifact IDs must be distinct.")
        for values, label in (
            (self.weight_artifact_ids, "weight artifact"),
            (self.supported_stages, "supported stage"),
            (self.supported_hardware, "supported hardware"),
            (self.supported_features, "supported feature"),
        ):
            if any(not value for value in values):
                raise ValueError(f"Adapter {label} values must not be empty.")
            if len(values) != len(set(values)):
                raise ValueError(f"Adapter {label} values must be unique.")


def strict_local_adapter_issues(
    manifest: AdapterManifest,
    artifacts: Mapping[str, ModelArtifact],
) -> tuple[str, ...]:
    """Explain why an installed adapter cannot run with networking disabled."""

    issues: list[str] = []
    artifact_ids = (manifest.code_artifact_id, *manifest.weight_artifact_ids)
    for artifact_id in artifact_ids:
        artifact = artifacts.get(artifact_id)
        if artifact is None:
            issues.append(f"missing artifact {artifact_id!r}")
        elif artifact.network_requirement is NetworkRequirement.RUNTIME:
            issues.append(f"artifact {artifact_id!r} requires runtime networking")
    if not manifest.supported_stages:
        issues.append("adapter declares no supported analysis stages")
    return tuple(issues)
