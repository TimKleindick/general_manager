"""Type-checking helpers shared across the codebase."""

from __future__ import annotations


def safe_issubclass(candidate: object, parent: type | tuple[type, ...]) -> bool:
    """Return True when *candidate* is a class and a subclass of *parent*."""
    return isinstance(candidate, type) and issubclass(candidate, parent)
