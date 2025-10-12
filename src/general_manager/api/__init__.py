"""GraphQL helpers for GeneralManager."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "GraphQL",
    "MeasurementType",
    "MeasurementScalar",
    "graphQlProperty",
    "graphQlMutation",
]

_MODULE_MAP = {
    "GraphQL": ("general_manager.api.graphql", "GraphQL"),
    "MeasurementType": ("general_manager.api.graphql", "MeasurementType"),
    "MeasurementScalar": ("general_manager.api.graphql", "MeasurementScalar"),
    "graphQlProperty": ("general_manager.api.property", "graphQlProperty"),
    "graphQlMutation": ("general_manager.api.mutation", "graphQlMutation"),
}


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_path, attr = _MODULE_MAP[name]
    module = import_module(module_path)
    value = getattr(module, attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)
