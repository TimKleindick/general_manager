"""Compatibility helpers for the refactored ORM capability package."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import inspect
from typing import TypeVar

from django.db import models
from simple_history.utils import (
    update_change_reason as _SIMPLE_HISTORY_UPDATE_CHANGE_REASON,
)


ResultT = TypeVar("ResultT")


def call_with_observability(
    target: object,
    *,
    operation: str,
    payload: Mapping[str, object],
    func: Callable[[], ResultT],
) -> ResultT:
    """
    Delegate invocation to the package-level `with_observability` resolved at call time.

    This function resolves and calls `general_manager.interface.capabilities.orm.with_observability`
    when invoked so that runtime patches to the package-level attribute are respected.
    Mutation callers use operation labels `mutation.assign_simple`,
    `mutation.save_with_history`, `mutation.apply_many_to_many`, `create`,
    `update`, `delete`, and `validation.normalize`. Payloads are shallow
    metadata dictionaries for observability only, such as `{"kwargs": ...}`,
    `{"pk": ...}`, `{"keys": ...}`, or relation-name snapshots; they are not
    used as mutation inputs by this helper.

    Returns:
        The value returned by the underlying `with_observability` call.

    Raises:
        Exception: Exceptions from the package-level hook or `func` are not
            wrapped.
    """
    from general_manager.interface.capabilities import orm as orm_package

    return orm_package.with_observability(
        target,
        operation=operation,
        payload=dict(payload),
        func=func,
    )


def call_update_change_reason(instance: models.Model, reason: str) -> None:
    """
    Delegate invocation to the package-level `update_change_reason` callable.

    This resolves the callable from `general_manager.interface.capabilities.orm` at call time so that runtime patches to that attribute are respected.
    The default package-level callable is django-simple-history's
    `update_change_reason`. If simple-history is absent from the model, if a
    patched callable is missing expected behavior, or if the callable raises,
    that exception propagates unchanged.

    Returns:
        None.
    """
    from general_manager.interface.capabilities import orm as orm_package

    orm_package.update_change_reason(instance, reason)


def uses_default_update_change_reason() -> bool:
    """Return whether the package still exposes simple-history's updater."""
    from general_manager.interface.capabilities import orm as orm_package

    return (
        inspect.getattr_static(orm_package, "update_change_reason", None)
        is _SIMPLE_HISTORY_UPDATE_CHANGE_REASON
    )
