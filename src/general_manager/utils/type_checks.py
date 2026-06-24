"""Type-checking helpers shared across the codebase."""

from __future__ import annotations

from typing import TypeGuard


def safe_issubclass(
    candidate: object,
    parent: type | tuple[type, ...],
) -> TypeGuard[type]:
    """
    Return whether ``candidate`` is a class and a subclass of ``parent``.

    This is a guard around ``issubclass()`` for dynamic metadata paths where the
    candidate may be an instance, ``None``, or another non-class object. A
    ``True`` result narrows ``candidate`` to ``type`` for static checkers. The
    Non-class candidates return ``False`` before ``issubclass()`` is called.
    Class candidates still rely on Python's ``issubclass()`` for the actual
    subclass check, so invalid ``parent`` tuples propagate ``TypeError`` only
    when ``candidate`` is itself a type.

    Parameters:
        candidate: Object to inspect.
        parent: Class or tuple of classes accepted by ``issubclass()``.

    Returns:
        ``True`` when ``candidate`` is a class and is a subclass of ``parent``;
        otherwise ``False``.

    Raises:
        TypeError: Propagated from ``issubclass()`` when ``candidate`` is a
            type and ``parent`` is not a valid class or tuple of classes.
    """
    return isinstance(candidate, type) and issubclass(candidate, parent)
