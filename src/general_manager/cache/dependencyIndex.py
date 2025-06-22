from __future__ import annotations
import time
import ast
import re
import logging

from django.core.cache import cache
from general_manager.cache.signals import post_data_change, pre_data_change
from django.dispatch import receiver
from typing import Literal, Any, Iterable, TYPE_CHECKING, Type, Tuple

if TYPE_CHECKING:
    from general_manager.manager.generalManager import GeneralManager

type general_manager_name = str  # e.g. "Project", "Derivative", "User"
type attribute = str  # e.g. "field", "name", "id"
type lookup = str  # e.g. "field__gt", "field__in", "field__contains", "field"
type cache_keys = set[str]  # e.g. "cache_key_1", "cache_key_2"
type identifier = str  # e.g. "{'id': 1}"", "{'project': Project(**{'id': 1})}", ...
type dependency_index = dict[
    Literal["filter", "exclude"],
    dict[
        general_manager_name,
        dict[attribute, dict[lookup, cache_keys]],
    ],
]

type filter_type = Literal["filter", "exclude", "identification"]
type Dependency = Tuple[general_manager_name, filter_type, str]

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------------------
INDEX_KEY = "dependency_index"  # Key unter dem der gesamte Index liegt
LOCK_KEY = "dependency_index_lock"  # Key für das Sperr‑Mutex
LOCK_TIMEOUT = 5  # Sekunden TTL für den Lock
UNDEFINED = object()  # Dummy für nicht definierte Werte


# -----------------------------------------------------------------------------
# LOCKING HELPERS
# -----------------------------------------------------------------------------
def acquire_lock(timeout: int = LOCK_TIMEOUT) -> bool:
    """Atomar: create Lock key if it doesn't exist."""
    return cache.add(LOCK_KEY, "1", timeout)


def release_lock() -> None:
    """Release Lock key."""
    cache.delete(LOCK_KEY)


# -----------------------------------------------------------------------------
# INDEX ACCESS
# -----------------------------------------------------------------------------
def get_full_index() -> dependency_index:
    """Load or initialize the full index."""
    idx = cache.get(INDEX_KEY, None)
    if idx is None:
        idx: dependency_index = {"filter": {}, "exclude": {}}
        cache.set(INDEX_KEY, idx, None)
    return idx


def set_full_index(idx: dependency_index) -> None:
    """Write the complete index back to the cache."""
    cache.set(INDEX_KEY, idx, None)


# -----------------------------------------------------------------------------
# DEPENDENCY RECORDING
# -----------------------------------------------------------------------------
def record_dependencies(
    cache_key: str,
    dependencies: Iterable[
        tuple[
            general_manager_name,
            Literal["filter", "exclude", "identification"],
            identifier,
        ]
    ],
) -> None:
    start = time.time()
    while not acquire_lock():
        if time.time() - start > LOCK_TIMEOUT:
            raise TimeoutError("Could not aquire lock for record_dependencies")
        time.sleep(0.05)

    try:
        idx = get_full_index()
        for model_name, action, identifier in dependencies:
            if action in ("filter", "exclude"):
                params = ast.literal_eval(identifier)
                section = idx[action].setdefault(model_name, {})
                for lookup, val in params.items():
                    lookup_map = section.setdefault(lookup, {})
                    val_key = repr(val)
                    lookup_map.setdefault(val_key, set()).add(cache_key)

            else:
                # director ID Lookup as simple filter on 'id'
                section = idx["filter"].setdefault(model_name, {})
                lookup_map = section.setdefault("identification", {})
                val_key = identifier
                lookup_map.setdefault(val_key, set()).add(cache_key)

        set_full_index(idx)

    finally:
        release_lock()


# -----------------------------------------------------------------------------
# INDEX CLEANUP
# -----------------------------------------------------------------------------
def remove_cache_key_from_index(cache_key: str) -> None:
    """Remove a cache key from the index."""
    start = time.time()
    while not acquire_lock():
        if time.time() - start > LOCK_TIMEOUT:
            raise TimeoutError("Could not aquire lock for remove_cache_key_from_index")
        time.sleep(0.05)

    try:
        idx = get_full_index()
        for action in ("filter", "exclude"):
            action_section = idx.get(action, {})
            for mname, model_section in list(action_section.items()):
                for lookup, lookup_map in list(model_section.items()):
                    for val_key, key_set in list(lookup_map.items()):
                        if cache_key in key_set:
                            key_set.remove(cache_key)
                            if not key_set:
                                del lookup_map[val_key]
                    if not lookup_map:
                        del model_section[lookup]
                if not model_section:
                    del action_section[mname]
        set_full_index(idx)
    finally:
        release_lock()


# -----------------------------------------------------------------------------
# CACHE INVALIDATION
# -----------------------------------------------------------------------------
def invalidate_cache_key(cache_key: str) -> None:
    cache.delete(cache_key)


@receiver(pre_data_change)
def capture_old_values(
    sender: Type[GeneralManager], instance: GeneralManager | None, **kwargs
) -> None:
    if instance is None:
        return
    manager_name = sender.__name__
    idx = get_full_index()
    # get all lookups for this model
    lookups = set()
    for action in ("filter", "exclude"):
        lookups |= set(idx.get(action, {}).get(manager_name, {}))
    if lookups and instance.identification:
        # save old values for later comparison
        vals = {}
        for lookup in lookups:
            attr_path = lookup.split("__")
            obj = instance
            for i, attr in enumerate(attr_path):
                if getattr(obj, attr, UNDEFINED) is UNDEFINED:
                    lookup = "__".join(attr_path[:i])
                    break
                obj = getattr(obj, attr, None)
            vals[lookup] = obj
        setattr(instance, "_old_values", vals)


@receiver(post_data_change)
def generic_cache_invalidation(
    sender: type[GeneralManager],
    instance: GeneralManager,
    old_relevant_values: dict[str, Any],
    **kwargs,
):
    """
    Invalidates cached query results related to a model instance when its data changes.
    
    This function is intended to be used as a Django signal handler. It compares old and new values of relevant fields on a model instance against registered cache dependencies (filters and excludes). If a change affects any cached queryset result, the corresponding cache keys are invalidated and removed from the dependency index.
    """
    manager_name = sender.__name__
    idx = get_full_index()

    def matches(op: str, value: Any, val_key: Any) -> bool:
        if value is None:
            return False

        # eq
        if op == "eq":
            return repr(value) == val_key

        # in
        if op == "in":
            try:
                seq = ast.literal_eval(val_key)
                return value in seq
            except:
                return False

        # range
        if op in ("gt", "gte", "lt", "lte"):
            try:
                thr = type(value)(ast.literal_eval(val_key))
            except:
                return False
            if op == "gt":
                return value > thr
            if op == "gte":
                return value >= thr
            if op == "lt":
                return value < thr
            if op == "lte":
                return value <= thr

        # wildcard / regex
        if op in ("contains", "startswith", "endswith", "regex"):
            try:
                literal = ast.literal_eval(val_key)
            except Exception:
                literal = val_key

            # ensure we always work with strings to avoid TypeErrors
            text = "" if value is None else str(value)
            if op == "contains":
                return literal in text
            if op == "startswith":
                return text.startswith(literal)
            if op == "endswith":
                return text.endswith(literal)
            # regex: val_key selbst als Pattern benutzen
            if op == "regex":
                try:
                    pattern = re.compile(val_key)
                except re.error:
                    return False
                return bool(pattern.search(text))

        return False

    for action in ("filter", "exclude"):
        model_section = idx.get(action, {}).get(manager_name, {})
        for lookup, lookup_map in model_section.items():
            # 1) get operator and attribute path
            parts = lookup.split("__")
            if parts[-1] in (
                "gt",
                "gte",
                "lt",
                "lte",
                "in",
                "contains",
                "startswith",
                "endswith",
                "regex",
            ):
                op = parts[-1]
                attr_path = parts[:-1]
            else:
                op = "eq"
                attr_path = parts

            # 2) get old & new value
            old_val = old_relevant_values.get("__".join(attr_path))

            obj = instance
            for attr in attr_path:
                obj = getattr(obj, attr, None)
                if obj is None:
                    break
            new_val = obj

            # 3) check against all cache_keys
            for val_key, cache_keys in list(lookup_map.items()):
                old_match = matches(op, old_val, val_key)
                new_match = matches(op, new_val, val_key)

                if action == "filter":
                    # Filter: invalidate if new match or old match
                    if new_match or old_match:
                        logger.info(
                            f"Invalidate cache key {cache_keys} for filter {lookup} with value {val_key}"
                        )
                        for ck in list(cache_keys):
                            invalidate_cache_key(ck)
                            remove_cache_key_from_index(ck)

                else:  # action == 'exclude'
                    # Excludes: invalidate only if matches changed
                    if old_match != new_match:
                        logger.info(
                            f"Invalidate cache key {cache_keys} for exclude {lookup} with value {val_key}"
                        )
                        for ck in list(cache_keys):
                            invalidate_cache_key(ck)
                            remove_cache_key_from_index(ck)
