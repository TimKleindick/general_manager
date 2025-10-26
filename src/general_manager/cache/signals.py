"""Signals and decorators for tracking GeneralManager data changes."""

from django.dispatch import Signal
from typing import Callable, TypeVar, ParamSpec, cast

from functools import wraps

post_data_change = Signal()

pre_data_change = Signal()

P = ParamSpec("P")
R = TypeVar("R")


def data_change(func: Callable[P, R]) -> Callable[P, R]:
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
        Emit pre_data_change and post_data_change signals around the wrapped function call.

        Emits a pre_data_change signal before invoking the wrapped function and a post_data_change signal afterwards. Signals are sent with `sender`, `instance`, and `action`; the post-change signal also includes `old_relevant_values`. After signaling, the wrapper removes the `_old_values` attribute from the pre-change instance if it exists.

        Parameters:
            *args: Positional arguments forwarded to the wrapped function.
            **kwargs: Keyword arguments forwarded to the wrapped function.

        Returns:
            R: The result returned by the wrapped function.
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
        if instance_before is not None:
            try:
                delattr(instance_before, "_old_values")
            except AttributeError:
                pass
        return result

    return wrapper
