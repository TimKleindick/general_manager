"""Registry for capability-provided startup hooks."""

from __future__ import annotations

from typing import Callable, Dict, Iterable, Iterator, List, Tuple, Type

StartupHook = Callable[[], None]
InterfaceType = Type[object]

_REGISTRY: Dict[InterfaceType, List[StartupHook]] = {}


def register_startup_hook(
    interface_cls: InterfaceType,
    hook: StartupHook,
) -> None:
    """Register a startup hook for the provided interface class."""
    hooks = _REGISTRY.setdefault(interface_cls, [])
    if hook not in hooks:
        hooks.append(hook)


def iter_interface_startup_hooks() -> Iterator[Tuple[InterfaceType, StartupHook]]:
    """Yield (interface, hook) pairs currently registered in the registry."""
    for interface_cls, hooks in _REGISTRY.items():
        for hook in hooks:
            yield interface_cls, hook


def registered_startup_hooks() -> Dict[InterfaceType, Tuple[StartupHook, ...]]:
    """Return a snapshot of the registered hooks for inspection/testing."""
    return {interface: tuple(hooks) for interface, hooks in _REGISTRY.items()}


def clear_startup_hooks() -> None:
    """Remove all registered startup hooks (primarily for tests)."""
    _REGISTRY.clear()
