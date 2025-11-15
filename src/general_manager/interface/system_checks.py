"""Registry for capability-provided system check hooks."""

from __future__ import annotations

from typing import Callable, Dict, Iterator, List, Tuple, Type

InterfaceType = Type[object]
SystemCheckHook = Callable[[], list]

_REGISTRY: Dict[InterfaceType, List[SystemCheckHook]] = {}


def register_system_check(
    interface_cls: InterfaceType,
    hook: SystemCheckHook,
) -> None:
    """Register a system-check hook for the provided interface class."""
    hooks = _REGISTRY.setdefault(interface_cls, [])
    if hook not in hooks:
        hooks.append(hook)


def iter_interface_system_checks() -> Iterator[Tuple[InterfaceType, SystemCheckHook]]:
    """Yield (interface, hook) pairs currently registered."""
    for interface_cls, hooks in _REGISTRY.items():
        for hook in hooks:
            yield interface_cls, hook


def registered_system_checks() -> Dict[InterfaceType, Tuple[SystemCheckHook, ...]]:
    """Return a snapshot of the registered system checks (for tests)."""
    return {interface: tuple(hooks) for interface, hooks in _REGISTRY.items()}


def clear_system_checks() -> None:
    """Remove all registered system checks (primarily for tests)."""
    _REGISTRY.clear()
