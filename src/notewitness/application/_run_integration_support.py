"""Shared public-facing constants and failures for run integration."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


PUBLICATION_FILENAME = "publication.completed.json"
MAX_PUBLICATION_BYTES = 16 * 1024 * 1024


class RunIntegrationError(RuntimeError):
    """A completed private run cannot be integrated safely."""


def json_sha256(value: Mapping[str, Any]) -> str:
    """Return the canonical identity digest for one project record."""

    try:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RunIntegrationError("Project identity record is not finite JSON.") from exc
    return hashlib.sha256(raw).hexdigest()
