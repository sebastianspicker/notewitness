"""Bounded loading of NoteWitness evidence project documents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from notewitness.core.evidence.contract import (
    EvidenceGraphError,
    MAX_JSON_DEPTH,
    MAX_PROJECT_BYTES,
    ValidationIssue,
)
from notewitness.core.evidence.graph import EvidenceGraph
from notewitness.core.strict_json import reject_duplicate_keys


class _DuplicateKeyError(ValueError):
    pass


_object_without_duplicate_keys = reject_duplicate_keys(
    lambda key: _DuplicateKeyError(f"duplicate object key {key!r}")
)


def _reject_nonstandard_number(value: str) -> None:
    raise ValueError(f"non-standard JSON number {value!r}")


def _exceeds_json_depth(value: object) -> bool:
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            return True
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
    return False


def load_payload(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        with source.open("rb") as handle:
            raw = handle.read(MAX_PROJECT_BYTES + 1)
    except OSError as exc:
        raise EvidenceGraphError(
            [ValidationIssue("$", f"cannot read {source}: {exc.strerror or 'I/O error'}")]
        ) from exc
    if len(raw) > MAX_PROJECT_BYTES:
        raise EvidenceGraphError(
            [ValidationIssue("$", f"project document exceeds {MAX_PROJECT_BYTES} bytes")]
        )
    try:
        text = raw.decode("utf-8")
        payload = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonstandard_number,
        )
    except UnicodeDecodeError as exc:
        raise EvidenceGraphError(
            [ValidationIssue("$", "project document must be UTF-8")]
        ) from exc
    except json.JSONDecodeError as exc:
        raise EvidenceGraphError(
            [ValidationIssue("$", f"invalid JSON at line {exc.lineno}, column {exc.colno}")]
        ) from exc
    except (_DuplicateKeyError, ValueError) as exc:
        raise EvidenceGraphError([ValidationIssue("$", f"invalid JSON: {exc}")]) from exc
    except RecursionError as exc:
        raise EvidenceGraphError(
            [ValidationIssue("$", "project document is nested too deeply")]
        ) from exc
    if not isinstance(payload, dict):
        raise EvidenceGraphError(
            [ValidationIssue("$", "project document must be a JSON object")]
        )
    if _exceeds_json_depth(payload):
        raise EvidenceGraphError(
            [ValidationIssue("$", "project document is nested too deeply")]
        )
    return payload


def load_evidence_graph(path: str | Path) -> EvidenceGraph:
    """Load one bounded document into the pure evidence-graph model."""

    return EvidenceGraph(load_payload(path))
