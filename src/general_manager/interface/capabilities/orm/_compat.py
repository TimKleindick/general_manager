"""Compatibility helpers for the refactored ORM capability package."""

from __future__ import annotations

from typing import Any


def call_with_observability(*args: Any, **kwargs: Any) -> Any:
    """
    Delegate invocation to the package-level `with_observability` resolved at call time.

    This function resolves and calls `general_manager.interface.capabilities.orm.with_observability`
    when invoked so that runtime patches to the package-level attribute are respected.

    Returns:
        Any: The value returned by the underlying `with_observability` call.
    """
    from general_manager.interface.capabilities import orm as orm_package

    return orm_package.with_observability(*args, **kwargs)


def call_update_change_reason(*args: Any, **kwargs: Any) -> Any:
    """
    Delegate invocation to the package-level `update_change_reason` callable.

    This resolves the callable from `general_manager.interface.capabilities.orm` at call time so that runtime patches to that attribute are respected.

    Returns:
        The value returned by the underlying `update_change_reason` callable.
    """
    from general_manager.interface.capabilities import orm as orm_package

    return orm_package.update_change_reason(*args, **kwargs)
