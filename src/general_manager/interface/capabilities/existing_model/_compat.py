"""Compatibility helper for existing model capability observability patches."""

from __future__ import annotations

from typing import Any


def call_with_observability(*args: Any, **kwargs: Any) -> Any:
    """
    Delegate invocation to the package-level `with_observability`, resolving the target at call time so runtime patches are honored.

    Returns:
        The result returned by the delegated `with_observability` call.
    """
    from general_manager.interface.capabilities import existing_model as package

    return package.with_observability(*args, **kwargs)
