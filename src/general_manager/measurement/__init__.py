from __future__ import annotations

"""Public API for measurement utilities."""


from importlib import import_module
from typing import Any

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
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_path, attr = _MODULE_MAP[name]
    module = import_module(module_path)
    value = getattr(module, attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)
