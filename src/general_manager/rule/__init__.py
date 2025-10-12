"""Helpers for defining rule-based validations."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["Rule", "BaseRuleHandler"]

_MODULE_MAP = {
    "Rule": ("general_manager.rule.rule", "Rule"),
    "BaseRuleHandler": ("general_manager.rule.handler", "BaseRuleHandler"),
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
