from django.dispatch import Signal
from typing import Callable, TypeVar, ParamSpec, cast

from functools import wraps

post_data_change = Signal()

pre_data_change = Signal()

P = ParamSpec("P")
R = TypeVar("R")


def dataChange(func: Callable[P, R]) -> Callable[P, R]:
    """
    Decorator that emits pre- and post-data change signals around the execution of the decorated function.
    
    Sends the `pre_data_change` signal before the wrapped function is called and the `post_data_change` signal after it completes. The signals include information about the sender, action, and relevant instance state before and after the change. Handles both regular functions and classmethods. Intended for use with functions that modify data to enable signal-based hooks for data change events.
    """

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        """
        Wraps a function to emit pre- and post-data change signals around its execution.
        
        Sends the `pre_data_change` signal before the wrapped function is called and the `post_data_change` signal after, providing context such as the sender, action name, and relevant instance data. Handles both regular functions and classmethods, and distinguishes the "create" action by omitting a pre-existing instance.
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
