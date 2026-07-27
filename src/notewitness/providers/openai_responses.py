"""Structured, suggestion-only integration with OpenAI's Responses API."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
import re
from typing import Any, Iterable, Mapping

from notewitness.evidence import CORE_RELATION_TYPES, EvidenceGraph
from notewitness.network import MAX_API_KEY_CHARS, OpenAIHTTPTransport


MAX_SELECTED_EVENTS = 32
MAX_EVENT_TEXT_CHARS = 4_000
MAX_OUTPUT_TOKENS = 1_024
MAX_MODEL_CHARS = 200
USAGE_FIELDS = ("input_tokens", "output_tokens", "total_tokens")
RELATION_ARGUMENT_ROLES: dict[str, tuple[str, str]] = {
    "demonstrates": ("example", "instruction"),
    "attempts": ("attempt", "task"),
    "feedback_on": ("feedback", "attempt"),
    "refers_to": ("utterance", "referent"),
    "repeats": ("repeat", "earlier_attempt"),
    "revises": ("revision", "earlier_attempt"),
    "contrasts_with": ("first_example", "second_example"),
}
_RESPONSE_ID_PATTERN = re.compile(r"^resp_[A-Za-z0-9_-]{1,200}$")

SUGGESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "relation_type": {
                        "type": "string",
                        "enum": sorted(CORE_RELATION_TYPES),
                    },
                    "arguments": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {
                                    "type": "string",
                                    "enum": sorted(
                                        {
                                            role
                                            for roles in RELATION_ARGUMENT_ROLES.values()
                                            for role in roles
                                        }
                                    ),
                                },
                                "event_ref": {"type": "string"},
                            },
                            "required": ["role", "event_ref"],
                            "additionalProperties": False,
                        },
                    },
                    "rationale": {"type": "string"},
                },
                "required": ["relation_type", "arguments", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["suggestions"],
    "additionalProperties": False,
}

INSTRUCTIONS = """You assist with qualitative analysis of music-teaching evidence.
Use only the explicitly supplied event excerpts. Propose zero or more observable
relations from the allowed vocabulary. Do not infer identity, emotion, talent,
engagement, teaching quality, diagnosis, or grades. Treat every result as a
machine suggestion requiring human review. If the evidence is insufficient,
return an empty suggestions array. Output only the requested JSON structure."""


class OpenAIConfigurationError(ValueError):
    pass


class OpenAIOutputError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OpenAISettings:
    api_key: str = field(repr=False)
    model: str

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "OpenAISettings":
        env = os.environ if environment is None else environment
        api_key = env.get("OPENAI_API_KEY", "")
        model_value = env.get("NOTEWITNESS_OPENAI_MODEL", "")
        if not isinstance(api_key, str) or not api_key:
            raise OpenAIConfigurationError("OPENAI_API_KEY is not configured.")
        if (
            len(api_key) > MAX_API_KEY_CHARS
            or any(
                ord(character) < 0x21 or ord(character) > 0x7E
                for character in api_key
            )
        ):
            raise OpenAIConfigurationError(
                "OPENAI_API_KEY is not in a valid header-safe format."
            )
        if not isinstance(model_value, str) or not model_value.strip():
            raise OpenAIConfigurationError(
                "NOTEWITNESS_OPENAI_MODEL is not configured."
            )
        model = model_value.strip()
        if len(model) > MAX_MODEL_CHARS or any(
            ord(char) < 0x21 or ord(char) > 0x7E for char in model
        ):
            raise OpenAIConfigurationError(
                "NOTEWITNESS_OPENAI_MODEL has an invalid format."
            )
        return cls(api_key=api_key, model=model)


@dataclass(frozen=True, slots=True)
class RemoteProjection:
    input_json: str
    request_view_sha256: str
    alias_to_event_id: tuple[tuple[str, str], ...]
    text_char_counts: tuple[int, ...]

    def preview_dict(self, *, include_text: bool = False) -> dict[str, Any]:
        request_view = json.loads(self.input_json)
        projected_events = request_view["events"]
        events: list[dict[str, Any]] = []
        for index, (alias, event_id) in enumerate(self.alias_to_event_id):
            event = {
                "ref": alias,
                "source_event_id": event_id,
                "text_chars": self.text_char_counts[index],
            }
            if include_text:
                event["text"] = projected_events[index]["text"]
            events.append(event)
        return {
            "purpose": "relation_suggestions",
            "request_view_sha256": self.request_view_sha256,
            "contains_selected_text": True,
            "events": events,
        }


@dataclass(frozen=True, slots=True)
class RelationArgument:
    role: str
    event_id: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "ref_kind": "event", "ref_id": self.event_id}


@dataclass(frozen=True, slots=True)
class RelationSuggestion:
    relation_type: str
    arguments: tuple[RelationArgument, ...]
    rationale: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.relation_type,
            "arguments": [argument.as_dict() for argument in self.arguments],
            "rationale": self.rationale,
            "review_status": "machine_suggested",
        }

    def as_safe_dict(self) -> dict[str, Any]:
        return {
            "type": self.relation_type,
            "arguments": [argument.as_dict() for argument in self.arguments],
            "review_status": "machine_suggested",
        }


@dataclass(frozen=True, slots=True)
class SuggestionResult:
    response_id: str
    requested_model: str
    returned_model: str
    request_view_sha256: str
    suggestions: tuple[RelationSuggestion, ...]
    usage: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": "openai",
            "response_id": self.response_id,
            "requested_model": self.requested_model,
            "returned_model": self.returned_model,
            "weight_hash_state": "not_available_remote",
            "request_view_sha256": self.request_view_sha256,
            "review_status": "machine_suggested",
            "suggestions": [suggestion.as_dict() for suggestion in self.suggestions],
            "usage": dict(self.usage),
        }

    def as_safe_dict(self) -> dict[str, Any]:
        return {
            "provider": "openai",
            "weight_hash_state": "not_available_remote",
            "request_view_sha256": self.request_view_sha256,
            "review_status": "machine_suggested",
            "suggestion_count": len(self.suggestions),
            "suggestions": [
                suggestion.as_safe_dict() for suggestion in self.suggestions
            ],
            "usage": dict(self.usage),
        }


class OpenAIRelationSuggester:
    def __init__(
        self,
        *,
        settings: OpenAISettings,
        transport: OpenAIHTTPTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport or OpenAIHTTPTransport()

    @classmethod
    def suggest_authorized(
        cls,
        *,
        graph: EvidenceGraph,
        event_ids: Iterable[str],
        confirmed: bool,
        environment: Mapping[str, str] | None = None,
        transport: OpenAIHTTPTransport | None = None,
    ) -> SuggestionResult:
        selected_ids = cls._normalize_event_ids(event_ids)
        graph.require_valid()
        projection = cls._prepare_projection(graph, selected_ids)
        # Policy and exact selected-event rights are checked before credential lookup.
        graph.network_policy().require_remote_inference(
            confirmed=confirmed,
            rights_allow_remote=graph.selected_events_allow_remote(selected_ids),
        )
        settings = OpenAISettings.from_environment(environment)
        return cls(settings=settings, transport=transport)._suggest(projection)

    @classmethod
    def preview(
        cls, *, graph: EvidenceGraph, event_ids: Iterable[str]
    ) -> RemoteProjection:
        selected_ids = cls._normalize_event_ids(event_ids)
        graph.require_valid()
        return cls._prepare_projection(graph, selected_ids)

    @staticmethod
    def _normalize_event_ids(event_ids: Iterable[str]) -> tuple[str, ...]:
        selected_ids: list[str] = []
        seen: set[str] = set()
        for event_id in event_ids:
            if not isinstance(event_id, str):
                raise OpenAIOutputError("Selected event IDs must be strings.")
            if event_id not in seen:
                selected_ids.append(event_id)
                seen.add(event_id)
        return tuple(selected_ids)

    @staticmethod
    def _prepare_projection(
        graph: EvidenceGraph, selected_ids: tuple[str, ...]
    ) -> RemoteProjection:
        if not selected_ids:
            raise OpenAIOutputError("At least one event must be selected.")
        if len(selected_ids) > MAX_SELECTED_EVENTS:
            raise OpenAIOutputError(
                f"At most {MAX_SELECTED_EVENTS} events may be selected."
            )

        event_index = graph.index("events")
        selected_events: list[dict[str, Any]] = []
        alias_to_event_id: list[tuple[str, str]] = []
        text_char_counts: list[int] = []
        for index, event_id in enumerate(selected_ids):
            event = event_index.get(event_id)
            if event is None:
                raise OpenAIOutputError(f"Unknown selected event: {event_id}")
            body = event.get("body")
            value = body.get("value") if isinstance(body, dict) else None
            if not isinstance(value, str):
                raise OpenAIOutputError(
                    f"Selected event {event_id} does not contain text."
                )
            if len(value) > MAX_EVENT_TEXT_CHARS:
                raise OpenAIOutputError(
                    f"Selected event {event_id} exceeds the text limit."
                )
            alias = f"e{index}"
            selected_events.append({"ref": alias, "text": value})
            alias_to_event_id.append((alias, event_id))
            text_char_counts.append(len(value))

        request_view = {
            "schema_version": "0.1.0",
            "purpose": "relation_suggestions",
            "events": selected_events,
        }
        input_text = json.dumps(
            request_view, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        request_hash = hashlib.sha256(input_text.encode("utf-8")).hexdigest()
        return RemoteProjection(
            input_json=input_text,
            request_view_sha256=request_hash,
            alias_to_event_id=tuple(alias_to_event_id),
            text_char_counts=tuple(text_char_counts),
        )

    def _suggest(self, projection: RemoteProjection) -> SuggestionResult:
        payload = {
            "model": self._settings.model,
            "instructions": INSTRUCTIONS,
            "input": projection.input_json,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "notewitness_relation_suggestions",
                    "strict": True,
                    "schema": SUGGESTION_SCHEMA,
                }
            },
        }
        response = self._transport.post(
            api_key=self._settings.api_key, payload=payload
        )
        return self._parse_response(response, projection)

    def _parse_response(
        self,
        response: Mapping[str, Any],
        projection: RemoteProjection,
    ) -> SuggestionResult:
        if response.get("status") != "completed":
            raise OpenAIOutputError("OpenAI response did not complete.")
        if response.get("error") is not None:
            raise OpenAIOutputError("OpenAI response contained an API error.")

        text_parts: list[str] = []
        refused = False
        output = response.get("output")
        if not isinstance(output, list):
            raise OpenAIOutputError("OpenAI response output must be an array.")
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "refusal":
                    refused = True
                elif part.get("type") == "output_text" and isinstance(
                    part.get("text"), str
                ):
                    text_parts.append(part["text"])
        if refused:
            raise OpenAIOutputError("OpenAI declined to produce relation suggestions.")
        if not text_parts:
            raise OpenAIOutputError("OpenAI response contained no output text.")

        try:
            decoded = json.loads("".join(text_parts))
        except json.JSONDecodeError as exc:
            raise OpenAIOutputError(
                "OpenAI structured output was not valid JSON."
            ) from exc
        suggestions = self._normalize_suggestions(
            decoded, dict(projection.alias_to_event_id)
        )
        usage = self._normalize_usage(response.get("usage"))
        return SuggestionResult(
            response_id=self._response_identifier(response.get("id")),
            requested_model=self._settings.model,
            returned_model=self._provider_identifier(
                response.get("model"), "returned model"
            ),
            request_view_sha256=projection.request_view_sha256,
            suggestions=suggestions,
            usage=usage,
        )

    @staticmethod
    def _normalize_suggestions(
        decoded: object, alias_to_event_id: Mapping[str, str]
    ) -> tuple[RelationSuggestion, ...]:
        if not isinstance(decoded, dict) or set(decoded) != {"suggestions"}:
            raise OpenAIOutputError("OpenAI output has an unexpected root shape.")
        raw_suggestions = decoded.get("suggestions")
        if not isinstance(raw_suggestions, list) or len(raw_suggestions) > 32:
            raise OpenAIOutputError("OpenAI suggestions must be a bounded array.")

        normalized: list[RelationSuggestion] = []
        for raw in raw_suggestions:
            if not isinstance(raw, dict) or set(raw) != {
                "relation_type",
                "arguments",
                "rationale",
            }:
                raise OpenAIOutputError("OpenAI suggestion has an unexpected shape.")
            relation_type = raw.get("relation_type")
            if (
                not isinstance(relation_type, str)
                or relation_type not in CORE_RELATION_TYPES
            ):
                raise OpenAIOutputError("OpenAI suggested an unsupported relation type.")
            raw_arguments = raw.get("arguments")
            expected_roles = RELATION_ARGUMENT_ROLES[str(relation_type)]
            if not isinstance(raw_arguments, list) or len(raw_arguments) != len(
                expected_roles
            ):
                raise OpenAIOutputError("OpenAI relation arguments have invalid arity.")
            arguments: list[RelationArgument] = []
            for index, raw_argument in enumerate(raw_arguments):
                if not isinstance(raw_argument, dict) or set(raw_argument) != {
                    "role",
                    "event_ref",
                }:
                    raise OpenAIOutputError(
                        "OpenAI relation argument has an unexpected shape."
                    )
                role = raw_argument.get("role")
                event_ref = raw_argument.get("event_ref")
                if role != expected_roles[index]:
                    raise OpenAIOutputError(
                        "OpenAI relation arguments use unexpected semantic roles."
                    )
                if not isinstance(event_ref, str) or event_ref not in alias_to_event_id:
                    raise OpenAIOutputError(
                        "OpenAI relation referenced an unselected event."
                    )
                arguments.append(
                    RelationArgument(
                        role=role,
                        event_id=alias_to_event_id[event_ref],
                    )
                )
            rationale = raw.get("rationale")
            if not isinstance(rationale, str) or len(rationale) > 1_000:
                raise OpenAIOutputError("OpenAI rationale is invalid.")
            normalized.append(
                RelationSuggestion(
                    relation_type=str(relation_type),
                    arguments=tuple(arguments),
                    rationale=rationale,
                )
            )
        return tuple(normalized)

    @staticmethod
    def _normalize_usage(value: object) -> dict[str, int]:
        if not isinstance(value, dict):
            return {}
        usage: dict[str, int] = {}
        for field_name in USAGE_FIELDS:
            field_value = value.get(field_name)
            if (
                isinstance(field_value, int)
                and not isinstance(field_value, bool)
                and field_value >= 0
            ):
                usage[field_name] = field_value
        return usage

    @staticmethod
    def _provider_identifier(value: object, label: str) -> str:
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 256
            or any(
                ord(character) < 0x20 or ord(character) == 0x7F
                for character in value
            )
        ):
            raise OpenAIOutputError(f"OpenAI {label} is invalid.")
        return value

    @staticmethod
    def _response_identifier(value: object) -> str:
        if not isinstance(value, str) or not _RESPONSE_ID_PATTERN.fullmatch(value):
            raise OpenAIOutputError("OpenAI response ID is invalid.")
        return value
