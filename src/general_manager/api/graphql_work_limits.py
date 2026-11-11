"""Server-owned optional GraphQL workload budgets, independent of permissions."""

from __future__ import annotations

import os

from graphql import GraphQLError

from general_manager.conf import get_setting

_UNSET = object()


def get_graphql_work_limit(name: str, default: int | None = None) -> int | None:
    """Resolve Django settings before environment; explicit None means unlimited."""
    value = get_setting(name, _UNSET)
    if value is _UNSET:
        raw = os.environ.get(f"GENERAL_MANAGER_{name}")
        if raw is None:
            return default
        if raw.strip().lower() in {"none", "unlimited"}:
            return None
        try:
            value = int(raw)
        except ValueError:
            value = raw
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    message = f"{name} must be a positive integer or None (unlimited)."
    raise GraphQLError(
        message,
        extensions={"code": "BAD_USER_INPUT"},
    )
