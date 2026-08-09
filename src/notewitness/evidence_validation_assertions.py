"""Compatibility facade for event and relation validation."""

from notewitness.evidence_validation_events import RecordIndex, validate_events
from notewitness.evidence_validation_relations import validate_relations

__all__ = ("RecordIndex", "validate_events", "validate_relations")
