"""Compatibility helpers for the refactored ORM capability package."""

from __future__ import annotations

from typing import Any


def call_with_observability(*args: Any, **kwargs: Any) -> Any:
    """
    Delegate to the package-level `with_observability` attribute at runtime.

    Tests patch `general_manager.interface.capabilities.orm.with_observability`
    directly, so our capability modules should always resolve the function via the
    package rather than capturing the core helper at import time.
    """
    from general_manager.interface.capabilities import orm as orm_package

    return orm_package.with_observability(*args, **kwargs)


def call_update_change_reason(*args: Any, **kwargs: Any) -> Any:
    """
    Delegate to the package-level `update_change_reason` attribute.

    Tests patch `general_manager.interface.capabilities.orm.update_change_reason`;
    resolving the callable via the package keeps those patches effective.
    """
    from general_manager.interface.capabilities import orm as orm_package

    return orm_package.update_change_reason(*args, **kwargs)
