"""Registry for capability-provided startup hooks."""

from __future__ import annotations

from typing import Callable, Dict, Iterator, List, Tuple, Type

StartupHook = Callable[[], None]
InterfaceType = Type[object]

_REGISTRY: Dict[InterfaceType, List[StartupHook]] = {}


def register_startup_hook(
    interface_cls: InterfaceType,
    hook: StartupHook,
) -> None:
    """
    Register a startup hook for an interface class.

    If the same `hook` is already registered for `interface_cls`, it will not be added again.

    Parameters:
        interface_cls (InterfaceType): The interface class that the hook is associated with.
        hook (StartupHook): A callable to be invoked at startup for implementations of the interface.
    """
    hooks = _REGISTRY.setdefault(interface_cls, [])
    if hook not in hooks:
        hooks.append(hook)


def iter_interface_startup_hooks() -> Iterator[Tuple[InterfaceType, StartupHook]]:
    """
    Iterate over all registered startup hooks paired with their interface classes.

    Returns:
        iterator of tuples (InterfaceType, StartupHook): Each yielded tuple contains an interface class and one of its registered startup hooks.
    """
    for interface_cls, hooks in _REGISTRY.items():
        for hook in hooks:
            yield interface_cls, hook


def registered_startup_hooks() -> Dict[InterfaceType, Tuple[StartupHook, ...]]:
    """
    Provide a shallow snapshot of currently registered startup hooks keyed by interface type.

    The returned mapping contains tuples of the registered StartupHook callables for each interface; modifying the returned mapping or tuples does not affect the internal registry.

    Returns:
        Dict[InterfaceType, Tuple[StartupHook, ...]]: Mapping from each interface type to a tuple of its registered hooks.
    """
    return {interface: tuple(hooks) for interface, hooks in _REGISTRY.items()}


def clear_startup_hooks() -> None:
    """
    Clear the internal registry of all registered startup hooks.
    """
    _REGISTRY.clear()
