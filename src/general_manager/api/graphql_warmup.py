"""GraphQL property warm-up execution helpers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.core.cache import cache as django_cache
from django.utils import timezone
from django.utils.module_loading import import_string

from general_manager.api.property import GraphQLProperty
from general_manager.cache.run_context import CalculationRunContext
from general_manager.conf import get_setting
from general_manager.logging import get_logger
from general_manager.utils.make_cache_key import make_cache_key
from general_manager.api.graphql_warmup_registry import (
    GraphQLWarmUpRecipe,
    acquire_graphql_warmup_recipe_lock,
    due_timeout_graphql_warmup_recipe_keys,
    get_graphql_warmup_recipe,
    register_graphql_warmup_recipe,
    release_graphql_warmup_recipe_lock,
)

logger = get_logger("api.graphql_warmup")


@dataclass(frozen=True, slots=True)
class GraphQLWarmUpSummary:
    """Counts from one warm-up execution."""

    evaluated: int = 0
    failed: int = 0
    recipes: int = 0


def warmable_graphql_properties(
    manager_class: type[Any],
    property_names: Iterable[str] | None = None,
) -> dict[str, GraphQLProperty]:
    """Return warm-up-eligible GraphQL properties for a manager class."""
    interface_cls = getattr(manager_class, "Interface", None)
    if interface_cls is None:
        return {}
    available_properties = interface_cls.get_graph_ql_properties()
    selected = set(property_names) if property_names is not None else None
    return {
        name: prop
        for name, prop in available_properties.items()
        if isinstance(prop, GraphQLProperty)
        and prop.warm_up
        and prop.cache in {"dependency", "timeout"}
        and (selected is None or name in selected)
    }


def warm_up_graphql_properties(
    manager_classes: Iterable[type[Any]] | None = None,
    property_names: Iterable[str] | None = None,
) -> GraphQLWarmUpSummary:
    """Warm opted-in GraphQL properties by enumerating each manager's ``.all()``."""
    if not graphql_warmup_enabled():
        return GraphQLWarmUpSummary()
    classes = tuple(manager_classes) if manager_classes is not None else _all_managers()
    batch_size = _positive_int_setting("GRAPHQL_WARMUP_BATCH_SIZE", 100)
    warning_threshold = _positive_int_setting(
        "GRAPHQL_WARMUP_WARNING_ITEMS_PER_MANAGER",
        1000,
    )
    total_evaluated = 0
    total_failed = 0
    total_recipes = 0

    for manager_class in classes:
        properties = warmable_graphql_properties(manager_class, property_names)
        if not properties:
            continue
        manager_path = _manager_path(manager_class)
        instances_seen = 0
        warned = False
        batch: list[Any] = []
        for instance in manager_class.all():
            instances_seen += 1
            if not warned and instances_seen > warning_threshold:
                warned = True
                logger.warning(
                    "graphql warm-up manager crossed warning threshold",
                    context={
                        "manager": manager_class.__name__,
                        "threshold": warning_threshold,
                    },
                )
            batch.append(instance)
            if len(batch) >= batch_size:
                evaluated, failed, recipes = _warm_batch(
                    batch,
                    properties,
                    manager_path,
                )
                total_evaluated += evaluated
                total_failed += failed
                total_recipes += recipes
                batch = []
        if batch:
            evaluated, failed, recipes = _warm_batch(batch, properties, manager_path)
            total_evaluated += evaluated
            total_failed += failed
            total_recipes += recipes

    return GraphQLWarmUpSummary(
        evaluated=total_evaluated,
        failed=total_failed,
        recipes=total_recipes,
    )


def warm_up_graphql_recipe(cache_key: str) -> bool:
    """Reconstruct and warm one recipe-backed GraphQL property."""
    if not graphql_warmup_enabled():
        return False
    recipe = get_graphql_warmup_recipe(cache_key)
    if recipe is None:
        return False
    lock = acquire_graphql_warmup_recipe_lock(cache_key)
    if lock is None:
        return False
    warmed = False
    try:
        manager_class = import_string(recipe.manager_path)
        instance = manager_class(**recipe.identification)
        interface_cls = getattr(manager_class, "Interface", None)
        if interface_cls is not None:
            prop = interface_cls.get_graph_ql_properties().get(recipe.property_name)
            if isinstance(prop, GraphQLProperty):
                if recipe.cache == "timeout":
                    warmed = _refresh_timeout_recipe(instance, prop, recipe)
                else:
                    with CalculationRunContext():
                        getattr(instance, recipe.property_name)
                    register_graphql_warmup_recipe(
                        _recipe_for(instance, prop, recipe.property_name)
                    )
                    warmed = True
    except Exception:
        logger.exception(
            "graphql warm-up recipe failed",
            context={"cache_key": cache_key},
        )
    finally:
        release_graphql_warmup_recipe_lock(lock)
    return warmed


def refresh_due_graphql_warmup_recipes(limit: int | None = None) -> int:
    """Refresh due timeout recipes and return the number refreshed."""
    if not graphql_warmup_enabled():
        return 0
    refreshed = 0
    for cache_key in due_timeout_graphql_warmup_recipe_keys(limit=limit):
        if warm_up_graphql_recipe(cache_key):
            refreshed += 1
    return refreshed


def enqueue_graphql_warmup(
    manager_classes: Iterable[type[Any]] | None = None,
) -> bool:
    """Enqueue or run all-entry warm-up through the built-in task adapter."""
    if not graphql_warmup_enabled():
        return False
    from general_manager.api.graphql_warmup_tasks import dispatch_graphql_warmup

    return dispatch_graphql_warmup(manager_classes)


def enqueue_graphql_recipe_warmup(cache_keys: Iterable[str]) -> bool:
    """Enqueue recipe re-warm through the built-in task adapter."""
    if not (
        graphql_warmup_enabled()
        and bool(get_setting("GRAPHQL_WARMUP_REWARM_AFTER_INVALIDATION", True))
    ):
        return False
    keys = tuple(dict.fromkeys(cache_keys))
    if not keys:
        return False
    from general_manager.api.graphql_warmup_tasks import dispatch_graphql_recipe_warmup

    return dispatch_graphql_recipe_warmup(keys)


def graphql_warmup_enabled() -> bool:
    """Return whether framework-owned GraphQL warm-up behavior is enabled."""
    return bool(get_setting("GRAPHQL_WARMUP_ENABLED", False))


def _warm_batch(
    instances: list[Any],
    properties: dict[str, GraphQLProperty],
    manager_path: str | None,
) -> tuple[int, int, int]:
    candidates: list[GraphQLWarmUpRecipe] = []
    evaluated = 0
    failed = 0
    with CalculationRunContext():
        for instance in instances:
            for property_name, prop in properties.items():
                try:
                    getattr(instance, property_name)
                except Exception:
                    failed += 1
                    logger.exception(
                        "graphql property warm-up failed",
                        context={
                            "manager": instance.__class__.__name__,
                            "property": property_name,
                            "identification": getattr(
                                instance,
                                "identification",
                                None,
                            ),
                        },
                    )
                    continue
                evaluated += 1
                if manager_path is not None:
                    candidates.append(_recipe_for(instance, prop, property_name))
    for recipe in candidates:
        register_graphql_warmup_recipe(recipe)
    return evaluated, failed, len(candidates)


def _refresh_timeout_recipe(
    instance: Any,
    prop: GraphQLProperty,
    recipe: GraphQLWarmUpRecipe,
) -> bool:
    result = prop._raw_fget(instance)
    django_cache.set(recipe.cache_key, result, recipe.timeout)
    register_graphql_warmup_recipe(_recipe_for(instance, prop, recipe.property_name))
    return True


def _recipe_for(
    instance: Any,
    prop: GraphQLProperty,
    property_name: str,
) -> GraphQLWarmUpRecipe:
    cache_key = make_cache_key(prop._get_cached_fget(), (instance,), {})
    timeout = prop.timeout if prop.cache == "timeout" else None
    refresh_at = None
    if prop.cache == "timeout" and timeout is not None:
        refresh_at = timezone.now() + timedelta(
            seconds=timeout * _timeout_refresh_ratio()
        )
    return GraphQLWarmUpRecipe(
        cache_key=cache_key,
        manager_path=_manager_path(instance.__class__) or "",
        property_name=property_name,
        identification=dict(getattr(instance, "identification", {})),
        cache="timeout" if prop.cache == "timeout" else "dependency",
        timeout=timeout,
        refresh_at=refresh_at,
    )


def _manager_path(manager_class: type[Any]) -> str | None:
    qualname = getattr(manager_class, "__qualname__", manager_class.__name__)
    if "<locals>" in qualname:
        return None
    return f"{manager_class.__module__}.{qualname}"


def _all_managers() -> tuple[type[Any], ...]:
    from general_manager.api.graphql import GraphQL

    return tuple(GraphQL.manager_registry.values())


def _positive_int_setting(key: str, default: int) -> int:
    raw = get_setting(key, default)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default


def _timeout_refresh_ratio() -> float:
    raw = get_setting("GRAPHQL_WARMUP_TIMEOUT_REFRESH_RATIO", 0.8)
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.8
