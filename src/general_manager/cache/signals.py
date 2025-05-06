from django.dispatch import Signal
from typing import Callable, Any
from functools import wraps

post_data_change = Signal()

pre_data_change = Signal()


def dataChange(func: Callable[..., Any]) -> Callable:
    """
    Signal to indicate that data has changed.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
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
        result = (
            func.__func__(*args, **kwargs)
            if isinstance(func, classmethod)
            else func(*args, **kwargs)
        )
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
