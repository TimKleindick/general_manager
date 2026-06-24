"""Registry for capability-provided system check hooks."""

from __future__ import annotations

from collections.abc import Callable, Iterator

InterfaceType = type[object]
SystemCheckHook = Callable[[], list[object]]

_REGISTRY: dict[InterfaceType, list[SystemCheckHook]] = {}


def register_system_check(
    interface_cls: InterfaceType,
    hook: SystemCheckHook,
) -> None:
    """
    Register a system-check hook for an interface type.

    Parameters:
        interface_cls (InterfaceType): The interface class to associate the hook with.
        hook (SystemCheckHook): No-argument callable that returns a list of
            Django system-check result objects.

    Notes:
        If the same hook is already registered for the interface, this function leaves registrations unchanged.
    """
    hooks = _REGISTRY.setdefault(interface_cls, [])
    if hook not in hooks:
        hooks.append(hook)


def iter_interface_system_checks() -> Iterator[tuple[InterfaceType, SystemCheckHook]]:
    """
    Iterate over all registered system-check hooks, yielding an (interface, hook) pair for each.

    Returns:
        iterator: An iterator that yields `(interface_cls, hook)` pairs for
            every hook currently registered in the module registry.
    """
    for interface_cls, hooks in _REGISTRY.items():
        for hook in hooks:
            yield interface_cls, hook


def registered_system_checks() -> dict[InterfaceType, tuple[SystemCheckHook, ...]]:
    """
    Map interface types to tuples of their registered system-check hooks.

    Returns:
        A detached snapshot mapping each interface class to a tuple of its
        registered system-check hooks. Mutating the returned dict is harmless
        and does not mutate the registry; subsequent registry changes do not
        affect the returned tuples.
    """
    return {interface: tuple(hooks) for interface, hooks in _REGISTRY.items()}


def clear_system_checks() -> None:
    """Remove all registered system checks, primarily for test isolation."""
    _REGISTRY.clear()
