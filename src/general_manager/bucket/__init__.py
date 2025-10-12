"""Bucket utilities for GeneralManager."""

from __future__ import annotations

from typing import Any

from general_manager.utils.public_api import build_module_dir, resolve_export

__all__ = ["Bucket", "DatabaseBucket", "CalculationBucket", "GroupBucket"]

_MODULE_MAP = {
    "Bucket": ("general_manager.bucket.baseBucket", "Bucket"),
    "DatabaseBucket": ("general_manager.bucket.databaseBucket", "DatabaseBucket"),
    "CalculationBucket": (
        "general_manager.bucket.calculationBucket",
        "CalculationBucket",
    ),
    "GroupBucket": ("general_manager.bucket.groupBucket", "GroupBucket"),
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
