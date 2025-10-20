from __future__ import annotations

"""Utility helpers for building lazy-loading public package APIs."""

from importlib import import_module
from typing import Any, Iterable, Mapping, MutableMapping, overload


class MissingExportError(AttributeError):
    """Raised when a requested export is not defined in the public API."""

    def __init__(self, module_name: str, attribute: str) -> None:
        super().__init__(f"module {module_name!r} has no attribute {attribute!r}")


ModuleTarget = tuple[str, str]
ModuleMap = Mapping[str, str | ModuleTarget]


@overload
def _normalize_target(name: str, target: str) -> ModuleTarget: ...


@overload
def _normalize_target(name: str, target: ModuleTarget) -> ModuleTarget: ...


def _normalize_target(name: str, target: str | ModuleTarget) -> ModuleTarget:
    if isinstance(target, tuple):
        return target
    return target, name


def resolve_export(
    name: str,
    *,
    module_all: Iterable[str],
    module_map: ModuleMap,
    module_globals: MutableMapping[str, Any],
) -> Any:
    """Resolve a lazily-loaded export for a package __init__ module."""
    if name not in module_all:
        raise MissingExportError(module_globals["__name__"], name)
    module_path, attr_name = _normalize_target(name, module_map[name])
    module = import_module(module_path)
    value = getattr(module, attr_name)
    module_globals[name] = value
    return value


def build_module_dir(
    *,
    module_all: Iterable[str],
    module_globals: MutableMapping[str, Any],
) -> list[str]:
    """Return a sorted directory listing for a package __init__ module."""
    return sorted(list(module_globals.keys()) + list(module_all))
