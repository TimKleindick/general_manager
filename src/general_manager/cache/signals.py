"""Signals and decorators for tracking GeneralManager data changes."""

from copy import deepcopy
from django.dispatch import Signal
from typing import Callable, TypeVar, ParamSpec, cast

from functools import wraps

from general_manager.logging import get_logger

post_data_change = Signal()

pre_data_change = Signal()

P = ParamSpec("P")
R = TypeVar("R")

logger = get_logger("cache.signals")


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
        from general_manager.cache.dependency_index import (
            begin_dependency_data_change,
            end_dependency_data_change,
        )
        from general_manager.cache.run_context import current_calculation_run_context

        primary_exc: BaseException | None = None
        begin_dependency_data_change()
        context = current_calculation_run_context()
        if context is not None:
            context.clear_orm_bucket_results()
            context.clear_bucket_indexes()
        try:
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
            pre_identification = deepcopy(
                getattr(instance_before, "identification", None)
            )
            if isinstance(func, classmethod):
                inner = cast(Callable[P, R], func.__func__)
                result = inner(*args, **kwargs)
            else:
                result = func(*args, **kwargs)

            instance = result
            identification = getattr(instance, "identification", None)
            if identification is None:
                identification = pre_identification

            post_data_change.send(
                sender=sender,
                instance=instance,
                previous_instance=instance_before,
                identification=identification,
                action=action,
                old_relevant_values=old_relevant_values,
                **kwargs,
            )
            if instance_before is not None:
                try:
                    delattr(instance_before, "_old_values")
                except AttributeError:
                    pass
        except BaseException as error:
            primary_exc = error
            raise
        else:
            return result
        finally:
            try:
                end_dependency_data_change()
            except Exception:
                if primary_exc is not None:
                    logger.exception(
                        "Dependency data-change cleanup failed while handling "
                        "another exception."
                    )
                else:
                    raise

    return wrapper
