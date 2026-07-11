"""Signals and decorators for tracking GeneralManager data changes."""

from __future__ import annotations

from copy import deepcopy
from django.dispatch import Signal
from typing import Callable, TypeVar, ParamSpec, cast, overload

from functools import wraps

from general_manager.logging import get_logger

post_data_change = Signal()

pre_data_change = Signal()

P = ParamSpec("P")
R = TypeVar("R")

logger = get_logger("cache.signals")


@overload
def data_change(func: Callable[P, R]) -> Callable[P, R]: ...


@overload
def data_change(func: classmethod[object, P, R]) -> Callable[P, R]: ...


def data_change(
    func: Callable[P, R] | classmethod[object, P, R],
) -> Callable[P, R]:
    """
    Wrap a data-modifying function with pre- and post-change signal dispatching.

    The wrapper preserves the wrapped callable's metadata with `functools.wraps`.
    It opens a dependency-cache publish barrier before the mutation,
    clears run-scoped ORM bucket/index caches, emits `pre_data_change`, invokes
    the wrapped callable, then emits `post_data_change` with the changed
    instance, previous instance, action name, identification, and copied
    `_old_values` payload. GraphQL warm-up requeue keys collected during signal
    handling are drained only after the outermost active data-change barrier has
    closed; failed mutations drain pending keys but do not enqueue rewarm work.

    Parameters:
        func: Function that performs a data mutation. Methods named `create`
            are treated as class-level creates; every other name is treated as
            an instance mutation. Raw `classmethod` descriptor objects are
            accepted for compatibility; normal class methods should still prefer
            `@classmethod` outside `@data_change`.

    Returns:
        Wrapped function that returns the wrapped callable's result.

    Raises:
        BaseException: Exceptions from the wrapped callable and signal
            receivers propagate. Cleanup errors propagate when the wrapped
            callable succeeded; if the wrapped callable already failed, cleanup
            errors are logged and the original exception is re-raised. GraphQL
            warm-up enqueue errors are logged and suppressed.
    """
    decorator_source = (
        cast(Callable[P, R], func.__func__) if isinstance(func, classmethod) else func
    )

    @wraps(decorator_source)
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
            drain_invalidated_cache_keys_for_graphql_rewarm,
            end_dependency_data_change,
            is_dependency_data_change_active,
        )
        from general_manager.cache.run_context import current_calculation_run_context

        primary_exc: BaseException | None = None
        completed = False
        begin_dependency_data_change()
        context = current_calculation_run_context()
        if context is not None:
            context.clear_mutation_cache()
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

            context = current_calculation_run_context()
            if context is not None:
                context.clear_mutation_cache()

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
            completed = True
        except BaseException as error:
            primary_exc = error
            raise
        else:
            return result
        finally:
            cache_keys: tuple[str, ...] = ()
            try:
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
            finally:
                try:
                    if not is_dependency_data_change_active():
                        cache_keys = drain_invalidated_cache_keys_for_graphql_rewarm()
                except Exception:
                    if primary_exc is not None:
                        logger.exception(
                            "Dependency data-change cleanup failed while handling "
                            "another exception."
                        )
                    else:
                        raise
            if completed and cache_keys:
                try:
                    from general_manager.api.graphql_warmup import (
                        enqueue_graphql_recipe_warmup,
                    )

                    enqueue_graphql_recipe_warmup(cache_keys)
                except Exception:
                    logger.exception("GraphQL warm-up requeue failed.")

    return wrapper
