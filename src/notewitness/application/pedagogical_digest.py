"""Conservative, offline pedagogical relation suggestions from transcript evidence.

The digest deliberately does not summarize, paraphrase, or infer a learner's
state.  It only marks an explicit instructional utterance as a possible
practice assignment, preserving the original speech event as every relation
anchor.  A named human must separately accept both the speech evidence and the
relation before it appears in the practice-plan projection.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping

from notewitness.project_store import ProjectStore


_GENERATOR_ID = "generator:local-lesson-digest-v1"
_ACTOR_ID = "actor:local-lesson-digest"
_INSTRUCTION_PREFIXES = (
    "again",
    "listen",
    "play",
    "practice",
    "repeat",
    "remember",
    "sing",
    "try",
    "work on",
)


@dataclass(frozen=True, slots=True)
class PedagogicalDigestResult:
    """IDs appended by one deterministic digest pass."""

    relation_ids: tuple[str, ...]
    project_sha256: str


def suggest_practice_relations(project_root: str) -> PedagogicalDigestResult:
    """Append reviewable assignment relations for explicit speech instructions.

    The operation is idempotent for a given source event.  It uses only local
    graph data and creates no prose beyond the transcript itself.
    """

    appended: list[str] = []

    def append(payload: dict[str, Any]) -> None:
        events = payload.get("events")
        if not isinstance(events, list):
            raise PedagogicalDigestError("Project events collection is malformed.")
        existing_ids = {
            item.get("id") for item in payload.get("relations", []) if isinstance(item, dict)
        }
        candidates = [
            event
            for event in events
            if isinstance(event, dict) and _is_explicit_instruction(event)
        ]
        if not candidates:
            return
        _ensure_provenance(payload)
        for event in candidates:
            event_id = str(event["id"])
            relation_id = _relation_id(event_id)
            if relation_id in existing_ids:
                continue
            payload["relations"].append(
                {
                    "id": relation_id,
                    "type": "local:assigned_for_practice",
                    "arguments": [
                        {"role": "assignment", "ref_kind": "event", "ref_id": event_id},
                        {"role": "instruction", "ref_kind": "event", "ref_id": event_id},
                    ],
                    "generator_id": _GENERATOR_ID,
                    "annotator_id": _ACTOR_ID,
                    "rights_id": str(event["rights_id"]),
                    "layer": "normalized_hypothesis",
                    "confidence": {
                        "kind": "deterministic_rule",
                        "rule": "explicit_instruction_prefix_v1",
                    },
                    "review_status": "machine_suggested",
                }
            )
            appended.append(relation_id)
            existing_ids.add(relation_id)

    snapshot = ProjectStore(project_root).mutate(append)
    return PedagogicalDigestResult(tuple(appended), snapshot.sha256)


class PedagogicalDigestError(RuntimeError):
    """The bounded local digest cannot safely inspect this project."""


def _is_explicit_instruction(event: Mapping[str, Any]) -> bool:
    if (
        event.get("type") not in {"speech", "speech_over_music"}
        or event.get("layer") != "normalized_hypothesis"
        or event.get("review_status") != "machine_suggested"
    ):
        return False
    body = event.get("body")
    text = body.get("value") if isinstance(body, Mapping) else None
    if not isinstance(text, str):
        return False
    normalized = " ".join(text.split()).casefold()
    return bool(normalized) and any(
        normalized.startswith(prefix + " ") or normalized == prefix
        for prefix in _INSTRUCTION_PREFIXES
    )


def _ensure_provenance(payload: dict[str, Any]) -> None:
    actors = payload.get("actors")
    generators = payload.get("generators")
    relations = payload.get("relations")
    if not all(isinstance(value, list) for value in (actors, generators, relations)):
        raise PedagogicalDigestError("Project evidence collections are malformed.")
    if not any(isinstance(item, dict) and item.get("id") == _ACTOR_ID for item in actors):
        actors.append({"id": _ACTOR_ID, "role": "unknown", "visibility": "restricted"})
    if not any(isinstance(item, dict) and item.get("id") == _GENERATOR_ID for item in generators):
        generators.append(
            {
                "id": _GENERATOR_ID,
                "kind": "machine",
                "name": "Deterministic local lesson digest",
                "version": "1",
                "model": "explicit_instruction_prefix_rules_v1",
                "weight_hash_state": "not_applicable:deterministic_rules",
                "parameters": {"network_used": False, "rule_set": "explicit_instruction_prefix_v1"},
            }
        )


def _relation_id(event_id: str) -> str:
    token = sha256(event_id.encode("utf-8")).hexdigest()[:24]
    return f"relation:local-digest-assignment-{token}"
