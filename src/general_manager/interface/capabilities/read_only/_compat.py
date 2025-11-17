"""Compatibility helper for read-only capability observability patches."""

from __future__ import annotations

from typing import Any


def call_with_observability(*args: Any, **kwargs: Any) -> Any:
    """
    Delegate the call to the package-level `with_observability`, resolving it at runtime so patched implementations are honored.

    Returns:
        The value returned by the package's `with_observability` callable.
    """
    from general_manager.interface.capabilities import read_only as read_only_package

    return read_only_package.with_observability(*args, **kwargs)
