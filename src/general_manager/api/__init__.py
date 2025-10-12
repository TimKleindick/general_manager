"""GraphQL helpers for GeneralManager."""

from __future__ import annotations

from typing import Any

from general_manager.utils.public_api import build_module_dir, resolve_export

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
    return resolve_export(
        name,
        module_all=__all__,
        module_map=_MODULE_MAP,
        module_globals=globals(),
    )


def __dir__() -> list[str]:
    return build_module_dir(module_all=__all__, module_globals=globals())
