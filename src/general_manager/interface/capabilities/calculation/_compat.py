"""Compatibility helper for calculation capability observability patches."""

from __future__ import annotations

from typing import Any


def call_with_observability(*args: Any, **kwargs: Any) -> Any:
    """
    Delegate to the package-level `with_observability` attribute at runtime.

    Tests patch `general_manager.interface.capabilities.calculation.with_observability`
    directly, so we resolve the helper through the package each time.
    """
    from general_manager.interface.capabilities import (
        calculation as calculation_package,
    )

    return calculation_package.with_observability(*args, **kwargs)
