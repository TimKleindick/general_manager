"""GraphQL property warm-up execution helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, SupportsFloat, SupportsIndex, SupportsInt, TypeAlias, cast

from django.core.cache import cache as django_cache
from django.utils import timezone
from django.utils.module_loading import import_string

from general_manager.as_of import as_of
from general_manager.api.property import GraphQLProperty
from general_manager.cache.run_context import CalculationRunContext
from general_manager.conf import get_setting
from general_manager.logging import get_logger
from general_manager.utils.make_cache_key import make_cache_key
from general_manager.api.graphql_warmup_registry import (
    GraphQLWarmUpIdentification,
    GraphQLWarmUpRecipe,
    acquire_graphql_warmup_recipe_lock,
    due_timeout_graphql_warmup_recipe_keys,
    get_graphql_warmup_recipe,
    register_graphql_warmup_recipe,
    release_graphql_warmup_recipe_lock,
)

logger = get_logger("api.graphql_warmup")

IntCoercible: TypeAlias = str | bytes | bytearray | SupportsInt | SupportsIndex
FloatCoercible: TypeAlias = str | bytes | bytearray | SupportsFloat | SupportsIndex


class GraphQLWarmUpManagerClass(Protocol):
    """
    Class-level manager surface required for all-entry GraphQL warm-up.

    The object is expected to be an importable manager class with a bound
    `all()` method that yields manager instances. Local or nested classes can be
    evaluated but cannot produce reconstructable warm-up recipes.
    """

    __name__: str
    __module__: str
    __qualname__: str

    def all(self) -> Iterable[object]:
        """Yield manager instances that should be considered for warm-up."""
        ...


class _ClassImportSurface(Protocol):
    """Class metadata required to build an import path."""

    __name__: str
    __module__: str
    __qualname__: str


class _GraphQLPropertyProvider(Protocol):
    """Interface surface used to discover GraphQL properties."""

    def get_graph_ql_properties(self) -> Mapping[str, object]:
        """Return GraphQL property descriptors keyed by public property name."""
        ...


class _GraphQLWarmUpFactory(Protocol):
    """Callable manager class surface used when reconstructing recipes."""

    def __call__(self, **kwargs: object) -> object: ...


class _InvalidCacheKeyIterableError(TypeError):
    """Raised when enqueueing receives a single string instead of key iterable."""

    def __init__(self) -> None:
        """Build the validation error message."""
        super().__init__("cache_keys must be an iterable of strings, not a string")


class _InvalidCacheKeyError(TypeError):
    """Raised when enqueueing or recipe warm-up receives a non-string key."""

    def __init__(self) -> None:
        """Build the validation error message."""
        super().__init__("cache_keys entries must be strings")


class _InvalidLimitError(TypeError):
    """Raised when a due-refresh limit is not `None` or an integer."""

    def __init__(self) -> None:
        """Build the validation error message."""
        super().__init__("limit must be None or an integer")


class _InvalidIdentificationError(TypeError):
    """Raised when a warm-up instance exposes non-mapping identification."""

    def __init__(self) -> None:
        """Build the validation error message."""
        super().__init__("warm-up instance identification must be a mapping")


class _InvalidGraphQLPropertyRegistryError(TypeError):
    """Raised when an Interface returns malformed GraphQL property metadata."""

    def __init__(self) -> None:
        """Build the validation error message."""
        super().__init__("get_graph_ql_properties() must return a mapping")


@dataclass(frozen=True, slots=True)
class GraphQLWarmUpSummary:
    """
    Counts from one all-entry warm-up execution.

    `evaluated` counts successful property evaluations, not manager instances.
    `failed` counts property evaluations that raised and were logged by the
    executor. `recipes` counts successfully built recipe payloads handed to the
    registry for persistence.
    """

    evaluated: int = 0
    failed: int = 0
    recipes: int = 0


def warmable_graphql_properties(
    manager_class: GraphQLWarmUpManagerClass,
    property_names: Iterable[str] | None = None,
) -> dict[str, GraphQLProperty]:
    """
    Return warm-up-eligible GraphQL properties for a manager class.

    A property is warmable only when the manager has an `Interface` with
    `get_graph_ql_properties()`, the descriptor is a `GraphQLProperty`, the
    property opted in with `warm_up=True`, and its cache scope is `"dependency"`
    or `"timeout"`. `property_names=None` allows every warmable property;
    otherwise the provided names are matched against the keys returned by
    `get_graph_ql_properties()` and used as a global allow-list. Unknown names,
    non-GraphQL descriptors, malformed mapping values, and non-warmable cache
    scopes are ignored. Duplicate names in `property_names` have no additional
    effect. Missing `Interface` or missing `get_graph_ql_properties()` returns
    an empty dictionary.

    Raises:
        TypeError: If `get_graph_ql_properties()` returns a non-mapping value.
        Exception: Propagates errors raised by `get_graph_ql_properties()`.
    """
    interface_cls = getattr(manager_class, "Interface", None)
    if interface_cls is None:
        return {}
    raw_getter = getattr(interface_cls, "get_graph_ql_properties", None)
    if not callable(raw_getter):
        return {}
    get_properties = cast(Callable[[], Mapping[str, object]], raw_getter)
    available_properties = get_properties()
    if not isinstance(available_properties, Mapping):
        raise _InvalidGraphQLPropertyRegistryError
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
    manager_classes: Iterable[GraphQLWarmUpManagerClass] | None = None,
    property_names: Iterable[str] | None = None,
) -> GraphQLWarmUpSummary:
    """
    Warm opted-in GraphQL properties by enumerating each manager's `.all()`.

    Returns zero counts when `GRAPHQL_WARMUP_ENABLED` is false. When
    `manager_classes=None`, managers come from the GraphQL manager registry in
    registry order; otherwise the supplied iterable is consumed in order and
    duplicate classes are processed repeatedly. `property_names` is applied to
    every manager. The executor batches instances by
    `GRAPHQL_WARMUP_BATCH_SIZE`, logs once per manager after
    `GRAPHQL_WARMUP_WARNING_ITEMS_PER_MANAGER` entries, isolates property
    evaluation failures by logging and incrementing `failed`, and continues with
    later properties/managers. Local or nested manager classes are evaluated and
    counted, but they cannot be imported by future workers, so their successful
    property reads do not create recipes.

    Exceptions from manager enumeration, recipe construction, recipe registry
    writes, non-mapping instance `identification`, invalid manager surfaces, or
    settings access propagate.
    """
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
    selected_property_names = (
        tuple(property_names) if property_names is not None else None
    )

    for manager_class in classes:
        properties = warmable_graphql_properties(manager_class, selected_property_names)
        if not properties:
            continue
        manager_path = _manager_path(manager_class)
        instances_seen = 0
        warned = False
        batch: list[object] = []
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
    """
    Reconstruct and warm one recipe-backed GraphQL property.

    Returns `False` when warm-up is disabled, the recipe is missing or
    version-incompatible, the per-recipe lock is already held, the manager lacks
    a usable GraphQL property, or reconstruction/evaluation/recipe persistence
    raises inside the guarded warm-up attempt. Those guarded failures are logged
    with the cache key. Dependency recipes re-run the descriptor path in a
    `CalculationRunContext`; timeout recipes refresh the cached value directly
    without evicting the previous value first. `cache_key` is validated before
    the enabled setting is checked, so invalid key types raise even when warm-up
    is disabled.

    Raises:
        TypeError: If `cache_key` is not a string.
        Exception: Propagates lock acquisition and lock release failures.
    """
    _validate_cache_key(cache_key)
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
        execution_context = (
            nullcontext()
            if recipe.search_date is None
            else as_of(search_date=recipe.search_date)
        )
        with execution_context:
            warmed = _execute_graphql_warmup_recipe(recipe)
    except Exception:
        logger.exception(
            "graphql warm-up recipe failed",
            context={"cache_key": cache_key},
        )
    finally:
        release_graphql_warmup_recipe_lock(lock)
    return warmed


def _execute_graphql_warmup_recipe(recipe: GraphQLWarmUpRecipe) -> bool:
    """Reconstruct and evaluate one recipe inside its execution context."""
    manager_class = cast(_GraphQLWarmUpFactory, import_string(recipe.manager_path))
    instance = manager_class(**recipe.identification)
    interface_cls = getattr(manager_class, "Interface", None)
    raw_getter = getattr(interface_cls, "get_graph_ql_properties", None)
    if not callable(raw_getter):
        return False
    get_properties = cast(Callable[[], Mapping[str, object]], raw_getter)
    prop = get_properties().get(recipe.property_name)
    if not isinstance(prop, GraphQLProperty):
        return False
    if recipe.cache == "timeout":
        return _refresh_timeout_recipe(instance, prop, recipe)
    with CalculationRunContext():
        getattr(instance, recipe.property_name)
    register_graphql_warmup_recipe(_recipe_for(instance, prop, recipe.property_name))
    return True


def refresh_due_graphql_warmup_recipes(limit: int | None = None) -> int:
    """
    Refresh due timeout recipes and return the number refreshed.

    Returns `0` when warm-up is disabled. `limit=None` refreshes every due key;
    `0` and negative limits refresh no keys; positive limits cap the sorted due
    key list from the registry. `limit` is validated before the enabled setting
    is checked, so invalid limits raise even when warm-up is disabled. The
    returned integer counts successful recipe refreshes only, not due keys found
    or attempted.

    Raises:
        TypeError: If `limit` is not `None` or an integer. Boolean values are
            rejected even though `bool` is an `int` subclass.
        Exception: Propagates registry due-key lookup, lock acquisition, and
            lock release failures.
    """
    _validate_limit(limit)
    if not graphql_warmup_enabled():
        return 0
    refreshed = 0
    for cache_key in due_timeout_graphql_warmup_recipe_keys(limit=limit):
        if warm_up_graphql_recipe(cache_key):
            refreshed += 1
    return refreshed


def enqueue_graphql_warmup(
    manager_classes: Iterable[GraphQLWarmUpManagerClass] | None = None,
) -> bool:
    """
    Enqueue all-entry warm-up through the built-in task adapter.

    Returns `False` when `GRAPHQL_WARMUP_ENABLED` is false. Otherwise delegates
    to `dispatch_graphql_warmup(...)` and returns whether the task adapter
    accepted the enqueue request; it does not mean warm-up work has completed.
    Task-adapter validation errors and iterator errors propagate. Enqueue
    failures handled by the adapter are returned as `False`.
    """
    if not graphql_warmup_enabled():
        return False
    from general_manager.api.graphql_warmup_tasks import dispatch_graphql_warmup

    return bool(dispatch_graphql_warmup(manager_classes))


def enqueue_graphql_recipe_warmup(cache_keys: Iterable[str]) -> bool:
    """
    Enqueue recipe re-warm through the built-in task adapter.

    Returns `False` when warm-up is disabled, re-warm after invalidation is
    disabled, or the deduplicated key list is empty. Duplicate keys are removed
    while preserving first-seen order before dispatch. A `True` result means the
    task adapter accepted the enqueue request, not that recipe work has
    completed. The settings gates are checked before `cache_keys` is consumed;
    when both gates are enabled, the iterable is consumed once, entries are
    validated as strings before dispatch, and adapter enqueue failures are
    returned as `False`.

    Raises:
        TypeError: If `cache_keys` is a single string or contains non-string
            values.
        Exception: Propagates errors raised while iterating `cache_keys`.
    """
    if not (
        graphql_warmup_enabled()
        and bool(get_setting("GRAPHQL_WARMUP_REWARM_AFTER_INVALIDATION", True))
    ):
        return False
    keys = tuple(_dedupe_cache_keys(cache_keys))
    if not keys:
        return False
    from general_manager.api.graphql_warmup_tasks import dispatch_graphql_recipe_warmup

    return bool(dispatch_graphql_recipe_warmup(keys))


def graphql_warmup_enabled() -> bool:
    """
    Return whether framework-owned GraphQL warm-up behavior is enabled.

    Missing `GRAPHQL_WARMUP_ENABLED` defaults to `False`. Otherwise the value is
    read through GeneralManager's settings resolver and converted with normal
    Python truthiness.
    """
    return bool(get_setting("GRAPHQL_WARMUP_ENABLED", False))


def _warm_batch(
    instances: list[object],
    properties: dict[str, GraphQLProperty],
    manager_path: str | None,
) -> tuple[int, int, int]:
    """Evaluate one batch of instances and persist successful recipes."""
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
    instance: object,
    prop: GraphQLProperty,
    recipe: GraphQLWarmUpRecipe,
) -> bool:
    """Refresh one timeout-backed recipe without evicting the old value first."""
    result = prop._raw_fget(instance)
    django_cache.set(recipe.cache_key, result, recipe.timeout)
    register_graphql_warmup_recipe(_recipe_for(instance, prop, recipe.property_name))
    return True


def _recipe_for(
    instance: object,
    prop: GraphQLProperty,
    property_name: str,
) -> GraphQLWarmUpRecipe:
    """Build the registry payload for a warmed instance/property pair."""
    cache_key = make_cache_key(prop._get_cached_fget(), (instance,), {})
    timeout = prop.timeout if prop.cache == "timeout" else None
    refresh_at = None
    if prop.cache == "timeout" and timeout is not None:
        refresh_at = timezone.now() + timedelta(
            seconds=timeout * _timeout_refresh_ratio()
        )
    effective_search_date = getattr(instance, "_effective_search_date", None)
    return GraphQLWarmUpRecipe(
        cache_key=cache_key,
        manager_path=_manager_path(cast(_ClassImportSurface, instance.__class__)) or "",
        property_name=property_name,
        identification=_identification_for(instance),
        cache="timeout" if prop.cache == "timeout" else "dependency",
        timeout=timeout,
        refresh_at=refresh_at,
        search_date=(
            effective_search_date
            if isinstance(effective_search_date, datetime)
            else None
        ),
    )


def _identification_for(instance: object) -> GraphQLWarmUpIdentification:
    """Return a serializable recipe identification mapping for one instance."""
    identification = getattr(instance, "identification", {})
    if not isinstance(identification, Mapping):
        raise _InvalidIdentificationError
    if any(not isinstance(key, str) for key in identification):
        raise _InvalidIdentificationError
    return dict(cast(Mapping[str, object], identification))


def _manager_path(manager_class: _ClassImportSurface) -> str | None:
    """Return an import path for reconstructable manager classes."""
    qualname = manager_class.__qualname__
    if "<locals>" in qualname:
        return None
    return f"{manager_class.__module__}.{qualname}"


def _all_managers() -> tuple[GraphQLWarmUpManagerClass, ...]:
    """Return all manager classes registered with the GraphQL integration."""
    from general_manager.api.graphql import GraphQL

    return cast(
        tuple[GraphQLWarmUpManagerClass, ...],
        tuple(GraphQL.manager_registry.values()),
    )


def _positive_int_setting(key: str, default: int) -> int:
    """Read a positive integer warm-up setting with a fallback."""
    raw = get_setting(key, default)
    try:
        return max(1, int(cast(IntCoercible, raw)))
    except (TypeError, ValueError):
        return default


def _validate_cache_key(cache_key: str) -> None:
    """Validate a recipe cache key argument."""
    if not isinstance(cache_key, str):
        raise _InvalidCacheKeyError


def _validate_limit(limit: int | None) -> None:
    """Validate a due-refresh limit argument."""
    if limit is None:
        return
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise _InvalidLimitError


def _dedupe_cache_keys(cache_keys: Iterable[str]) -> list[str]:
    """Return distinct cache key strings in first-seen order."""
    if isinstance(cache_keys, str):
        raise _InvalidCacheKeyIterableError
    keys: list[str] = []
    seen: set[str] = set()
    for cache_key in cache_keys:
        if not isinstance(cache_key, str):
            raise _InvalidCacheKeyError
        if cache_key not in seen:
            keys.append(cache_key)
            seen.add(cache_key)
    return keys


def _timeout_refresh_ratio() -> float:
    """Read and clamp the timeout refresh ratio setting."""
    raw = get_setting("GRAPHQL_WARMUP_TIMEOUT_REFRESH_RATIO", 0.8)
    try:
        return max(0.0, min(1.0, float(cast(FloatCoercible, raw))))
    except (TypeError, ValueError):
        return 0.8
