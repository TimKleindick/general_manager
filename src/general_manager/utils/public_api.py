from __future__ import annotations

"""Utility helpers for building lazy-loading public package APIs."""

from importlib import import_module
from typing import Iterable, Mapping, MutableMapping, cast, overload

from general_manager.logging import get_logger


class MissingExportError(AttributeError):
    """Raised when a requested export is not defined in the public API."""

    def __init__(self, module_name: str, attribute: str) -> None:
        """
        Initialize the MissingExportError with the originating module name and the missing attribute.

        Constructs the exception message "module 'module_name' has no attribute 'attribute'".

        Parameters:
            module_name (str): Name of the module where the attribute was expected.
            attribute (str): Name of the missing attribute.
        """
        super().__init__(f"module {module_name!r} has no attribute {attribute!r}")


ModuleTarget = tuple[str, str]
ModuleMap = Mapping[str, str | ModuleTarget]
logger = get_logger("utils.public_api")


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
    module_globals: MutableMapping[str, object],
) -> object:
    """
    Resolve and cache a lazily-loaded export for a package __init__ module.

    ``module_map`` values may be target module path strings or
    ``(module_path, attribute_name)`` tuples. String targets use the requested
    public ``name`` as the target attribute name. Tuple targets use the supplied
    attribute name instead.

    Parameters:
        name (str): The public export name to resolve.
        module_all (Iterable[str]): Iterable of names declared in the module's __all__; used to validate that `name` is an allowed export.
        module_map: Mapping from public export names to target module paths or
            ``(module_path, attribute_name)`` pairs used to locate the actual
            object.
        module_globals: The module's globals dict; the resolved value will be stored here under `name`.

    Returns:
        The resolved attribute value for `name`.

    Raises:
        MissingExportError: If `name` is not present in `module_all`.
    """
    if name not in module_all:
        logger.warning(
            "missing public api export",
            context={
                "module": module_globals["__name__"],
                "export": name,
            },
        )
        raise MissingExportError(cast(str, module_globals["__name__"]), name)
    module_path, attr_name = _normalize_target(name, module_map[name])
    module = import_module(module_path)
    value = getattr(module, attr_name)
    module_globals[name] = value
    logger.debug(
        "resolved public api export",
        context={
            "module": module_globals["__name__"],
            "export": name,
            "target_module": module_path,
            "target_attribute": attr_name,
        },
    )
    return value


def build_module_dir(
    *,
    module_all: Iterable[str],
    module_globals: MutableMapping[str, object],
) -> list[str]:
    """Return a sorted directory listing for a package __init__ module."""
    return sorted(list(module_globals.keys()) + list(module_all))
