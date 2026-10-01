"""Pure strict-JSON primitives whose failures stay in each caller's vocabulary."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


ObjectPairsHook = Callable[[list[tuple[str, Any]]], dict[str, Any]]

_LOWERCASE_HEX = frozenset("0123456789abcdef")


def reject_duplicate_keys(error_factory: Callable[[str], BaseException]) -> ObjectPairsHook:
    """Return a ``json`` object_pairs_hook raising ``error_factory(key)`` on repeats."""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise error_factory(key)
            result[key] = value
        return result

    return unique_object


def is_sha256_hex(value: object) -> bool:
    """Return whether ``value`` is exactly 64 lowercase hexadecimal characters."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _LOWERCASE_HEX for character in value)
    )
