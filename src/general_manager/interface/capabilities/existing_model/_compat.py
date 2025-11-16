"""Compatibility helper for existing model capability observability patches."""

from __future__ import annotations

from typing import Any


def call_with_observability(*args: Any, **kwargs: Any) -> Any:
    """
    Resolve `with_observability` through the package to honor patched targets.
    """
    from general_manager.interface.capabilities import existing_model as package

    return package.with_observability(*args, **kwargs)
