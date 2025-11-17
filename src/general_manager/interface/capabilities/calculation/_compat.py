"""Compatibility helper for calculation capability observability patches."""

from __future__ import annotations

from typing import Any


def call_with_observability(*args: Any, **kwargs: Any) -> Any:
    """
    Delegate invocation to the package-level `with_observability` function.

    This resolves the helper through the package on each call (tests patch
    `general_manager.interface.capabilities.calculation.with_observability` directly).

    Returns:
        The value returned by the package-level `with_observability` call.
    """
    from general_manager.interface.capabilities import (
        calculation as calculation_package,
    )

    return calculation_package.with_observability(*args, **kwargs)
