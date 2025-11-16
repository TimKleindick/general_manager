"""Compatibility helper for read-only capability observability patches."""

from __future__ import annotations

from typing import Any


def call_with_observability(*args: Any, **kwargs: Any) -> Any:
    """
    Resolve `with_observability` through the package to honor patched targets.

    Tests patch `general_manager.interface.capabilities.read_only.with_observability`,
    so this helper fetches the callable from the package each time.
    """
    from general_manager.interface.capabilities import read_only as read_only_package

    return read_only_package.with_observability(*args, **kwargs)
