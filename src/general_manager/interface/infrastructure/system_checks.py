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
    """
    Register a system-check hook for an interface type.

    Parameters:
        interface_cls (InterfaceType): The interface class to associate the hook with.
        hook (SystemCheckHook): A no-argument callable that returns a list of system-check results.

    Notes:
        If the same hook is already registered for the interface, this function leaves registrations unchanged.
    """
    hooks = _REGISTRY.setdefault(interface_cls, [])
    if hook not in hooks:
        hooks.append(hook)


def iter_interface_system_checks() -> Iterator[Tuple[InterfaceType, SystemCheckHook]]:
    """
    Iterate over all registered system-check hooks, yielding an (interface, hook) pair for each.

    Returns:
        iterator (Iterator[Tuple[InterfaceType, SystemCheckHook]]): An iterator that yields (interface_cls, hook) pairs for every hook currently registered in the module registry.
    """
    for interface_cls, hooks in _REGISTRY.items():
        for hook in hooks:
            yield interface_cls, hook


def registered_system_checks() -> Dict[InterfaceType, Tuple[SystemCheckHook, ...]]:
    """
    Map interface types to tuples of their registered system-check hooks.

    Returns:
        Dict[InterfaceType, Tuple[SystemCheckHook, ...]]: A snapshot mapping each interface class to a tuple of its registered system-check hooks. Subsequent modifications to the registry do not affect the returned tuples.
    """
    return {interface: tuple(hooks) for interface, hooks in _REGISTRY.items()}


def clear_system_checks() -> None:
    """Remove all registered system checks (primarily for tests)."""
    _REGISTRY.clear()
