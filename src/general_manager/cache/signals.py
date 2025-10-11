"""Signals and decorators for tracking GeneralManager data changes."""

from django.dispatch import Signal
from typing import Callable, TypeVar, ParamSpec, cast

from functools import wraps

post_data_change = Signal()

pre_data_change = Signal()

P = ParamSpec("P")
R = TypeVar("R")


def dataChange(func: Callable[P, R]) -> Callable[P, R]:
    """
    Wrap a data-modifying function with pre- and post-change signal dispatching.

    Parameters:
        func (Callable[P, R]): Function that performs a data mutation.

    Returns:
        Callable[P, R]: Wrapped function that sends `pre_data_change` and `post_data_change` signals.
    """

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        """
        Execute the wrapped function while emitting data change signals.

        Parameters:
            *args: Positional arguments forwarded to the wrapped function.
            **kwargs: Keyword arguments forwarded to the wrapped function.

        Returns:
            R: Result produced by the wrapped function.
        """
        action = func.__name__
        if func.__name__ == "create":
            sender = args[0]
            instance_before = None
        else:
            instance = args[0]
            sender = instance.__class__
            instance_before = instance
        pre_data_change.send(
            sender=sender,
            instance=instance_before,
            action=action,
            **kwargs,
        )
        old_relevant_values = getattr(instance_before, "_old_values", {})
        if isinstance(func, classmethod):
            inner = cast(Callable[P, R], func.__func__)
            result = inner(*args, **kwargs)
        else:
            result = func(*args, **kwargs)

        instance = result

        post_data_change.send(
            sender=sender,
            instance=instance,
            action=action,
            old_relevant_values=old_relevant_values,
            **kwargs,
        )
        return result

    return wrapper
