"""Convenience access to GeneralManager core components."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "GraphQL",
    "GeneralManager",
    "GeneralManagerMeta",
    "Input",
    "graphQlProperty",
    "graphQlMutation",
    "Bucket",
    "DatabaseBucket",
    "CalculationBucket",
    "GroupBucket",
]

_MODULE_MAP = {
    "GraphQL": ("general_manager.api", "GraphQL"),
    "graphQlProperty": ("general_manager.api", "graphQlProperty"),
    "graphQlMutation": ("general_manager.api", "graphQlMutation"),
    "GeneralManager": ("general_manager.manager", "GeneralManager"),
    "GeneralManagerMeta": ("general_manager.manager", "GeneralManagerMeta"),
    "Input": ("general_manager.manager", "Input"),
    "Bucket": ("general_manager.bucket", "Bucket"),
    "DatabaseBucket": ("general_manager.bucket", "DatabaseBucket"),
    "CalculationBucket": ("general_manager.bucket", "CalculationBucket"),
    "GroupBucket": ("general_manager.bucket", "GroupBucket"),
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
