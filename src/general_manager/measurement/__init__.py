"""Public API for measurement utilities."""

from __future__ import annotations

from typing import Any

from general_manager.utils.public_api import build_module_dir, resolve_export

__all__ = [
    "Measurement",
    "MeasurementField",
    "ureg",
    "currency_units",
]

_MODULE_MAP = {
    "Measurement": ("general_manager.measurement.measurement", "Measurement"),
    "ureg": ("general_manager.measurement.measurement", "ureg"),
    "currency_units": ("general_manager.measurement.measurement", "currency_units"),
    "MeasurementField": (
        "general_manager.measurement.measurementField",
        "MeasurementField",
    ),
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
