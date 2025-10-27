"""Dependency index management for cached GeneralManager query results."""

from __future__ import annotations

import ast
import re
import time
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Iterable, Literal, Tuple, Type, cast

from django.core.cache import cache
from django.dispatch import receiver

from general_manager.cache.signals import post_data_change, pre_data_change
from general_manager.logging import get_logger

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager

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

logger = get_logger("cache.dependency_index")


class DependencyLockTimeoutError(TimeoutError):
    """Raised when the dependency index lock cannot be acquired within the timeout."""

    def __init__(self, operation: str) -> None:
        """
        Error raised when acquiring the dependency index lock times out.

        Parameters:
            operation (str): Name or description of the operation during which lock acquisition timed out.
        """
        super().__init__(
            f"Timed out acquiring dependency index lock during {operation}."
        )


# -----------------------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------------------
INDEX_KEY = "dependency_index"  # Cache key storing the complete dependency index
LOCK_KEY = "dependency_index_lock"  # Cache key used for the dependency lock
LOCK_TIMEOUT = 5  # Lock TTL in seconds
UNDEFINED = object()  # Sentinel for undefined values
ACTIONS: tuple[Literal["filter"], Literal["exclude"]] = ("filter", "exclude")


# -----------------------------------------------------------------------------
# LOCKING HELPERS
# -----------------------------------------------------------------------------
def acquire_lock(timeout: int = LOCK_TIMEOUT) -> bool:
    """
    Attempt to acquire the cache-backed lock guarding dependency writes.

    Parameters:
        timeout (int): Expiration time for the lock entry in seconds.

    Returns:
        bool: True if the lock was acquired; otherwise, False.
    """
    return cache.add(LOCK_KEY, "1", timeout)


def release_lock() -> None:
    """
    Release the cache-backed lock guarding dependency writes.

    Returns:
        None
    """
    cache.delete(LOCK_KEY)


# -----------------------------------------------------------------------------
# INDEX ACCESS
# -----------------------------------------------------------------------------
def get_full_index() -> dependency_index:
    """
    Fetch the dependency index from cache, initialising it on first access.

    Returns:
        dependency_index: Mapping of tracked filters and excludes keyed by manager name.
    """
    cached_index = cache.get(INDEX_KEY, None)
    if cached_index is None:
        idx: dependency_index = {"filter": {}, "exclude": {}}
        cache.set(INDEX_KEY, idx, None)
        return idx
    return cast(dependency_index, cached_index)


def set_full_index(idx: dependency_index) -> None:
    """
    Persist the dependency index to cache.

    Parameters:
        idx (dependency_index): Updated index that should replace the cached value.

    Returns:
        None
    """
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
    """
    Register a cache key as dependent on the given manager-level filters, exclusions, or identifications.

    Parameters:
        cache_key (str): The cache key to associate with the declared dependencies.
        dependencies (Iterable[tuple[str, Literal["filter", "exclude", "identification"], str]]):
            Iterable of tuples describing each dependency as (manager_name, action, identifier).
            - For `filter` and `exclude`, `identifier` is a string representation of a mapping of lookups to values.
            - For `identification`, `identifier` is the identifying value (treated as a lookup on `id`).

    Raises:
        DependencyLockTimeoutError: If a lock cannot be acquired within the configured timeout while updating the index.
    """
    start = time.time()
    while not acquire_lock():
        if time.time() - start > LOCK_TIMEOUT:
            raise DependencyLockTimeoutError("record_dependencies")
        time.sleep(0.05)

    try:
        idx = get_full_index()
        for model_name, action, identifier in dependencies:
            if action in ("filter", "exclude"):
                action_key = cast(Literal["filter", "exclude"], action)
                params = ast.literal_eval(identifier)
                section = idx[action_key].setdefault(model_name, {})
                if len(params) > 1:
                    cache_dependencies = section.setdefault(
                        "__cache_dependencies__", {}
                    )
                    cache_dependencies.setdefault(cache_key, set()).add(identifier)
                for lookup, val in params.items():
                    lookup_map = section.setdefault(lookup, {})
                    val_key = repr(val)
                    lookup_map.setdefault(val_key, set()).add(cache_key)

            else:
                # Treat identification lookups as a simple filter on `id`
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
    """
    Remove a cache key from all dependency mappings in the stored dependency index.

    Acquires the dependency lock to update and persist the index; if the lock cannot be obtained within LOCK_TIMEOUT the operation fails.

    Parameters:
        cache_key (str): The cache key to expunge from all recorded filter, exclude, and identification mappings.

    Raises:
        DependencyLockTimeoutError: If the dependency lock cannot be acquired within LOCK_TIMEOUT.
    """
    start = time.time()
    while not acquire_lock():
        if time.time() - start > LOCK_TIMEOUT:
            raise DependencyLockTimeoutError("remove_cache_key_from_index")
        time.sleep(0.05)

    try:
        idx = get_full_index()
        for action in ACTIONS:
            action_section = idx[action]
            for mname, model_section in list(action_section.items()):
                cache_dependencies = model_section.get("__cache_dependencies__", {})
                for lookup, lookup_map in list(model_section.items()):
                    if lookup.startswith("__"):
                        continue
                    for val_key, key_set in list(lookup_map.items()):
                        if cache_key in key_set:
                            key_set.remove(cache_key)
                            if not key_set:
                                del lookup_map[val_key]
                    if not lookup_map:
                        del model_section[lookup]
                if cache_dependencies:
                    cache_dependencies.pop(cache_key, None)
                    if not cache_dependencies:
                        model_section.pop("__cache_dependencies__", None)
                if not model_section:
                    del action_section[mname]
        set_full_index(idx)
    finally:
        release_lock()


# -----------------------------------------------------------------------------
# CACHE INVALIDATION
# -----------------------------------------------------------------------------
def invalidate_cache_key(cache_key: str) -> None:
    """
    Delete the cached result associated with the provided key.

    Parameters:
        cache_key (str): Key referencing the cached queryset.

    Returns:
        None
    """
    cache.delete(cache_key)


@receiver(pre_data_change)
def capture_old_values(
    sender: Type[GeneralManager],
    instance: GeneralManager | None,
    **kwargs: object,
) -> None:
    """
    Record the current values of fields referenced by tracked filters on the given manager instance before it changes.

    Parameters:
        instance (GeneralManager | None): Manager instance about to change; if provided, this function sets instance._old_values to a mapping of lookup keys to their current values for use by post-change invalidation logic.
    """
    if instance is None:
        return
    manager_name = sender.__name__
    idx = get_full_index()
    # get all lookups for this model
    lookups = set()
    for action in ACTIONS:
        model_section = idx[action].get(manager_name)
        if isinstance(model_section, dict):
            lookups |= {
                lookup
                for lookup in model_section.keys()
                if isinstance(lookup, str) and not lookup.startswith("__")
            }
        elif isinstance(model_section, list):
            lookups |= set(model_section)
    if lookups and instance.identification:
        # save old values for later comparison
        vals: dict[str, object] = {}
        for lookup in lookups:
            attr_path = lookup.split("__")
            current: object = instance
            for i, attr in enumerate(attr_path):
                if getattr(current, attr, UNDEFINED) is UNDEFINED:
                    lookup = "__".join(attr_path[:i])
                    break
                current = getattr(current, attr, None)
            vals[lookup] = current
        instance._old_values = vals


@receiver(post_data_change)
def generic_cache_invalidation(
    sender: type[GeneralManager],
    instance: GeneralManager,
    old_relevant_values: dict[str, Any],
    **kwargs: object,
) -> None:
    """
    Invalidate cache entries whose recorded dependencies are affected by changes to a GeneralManager instance.

    Uses the dependency index to compare previously captured values against the instance's current values for tracked lookups, evaluates both simple and composite dependency conditions for "filter" and "exclude" actions, and for any dependency that warrants invalidation it deletes the corresponding cache entry and removes its references from the index.

    Parameters:
        sender (type[GeneralManager]): Manager class that emitted the signal.
        instance (GeneralManager): The manager instance that was changed.
        old_relevant_values (dict[str, Any]): Mapping of lookup paths (joined by "__") to their values as captured before the change; used to compare old vs. new values for invalidation decisions.
    """
    manager_name = sender.__name__
    idx = get_full_index()

    def _safe_literal_eval(value: Any) -> Any:
        if isinstance(value, str):
            try:
                return ast.literal_eval(value)
            except (ValueError, SyntaxError):
                return value
        return value

    def _coerce_to_type(sample: Any, raw: Any) -> Any | None:
        """
        Coerces a raw value to match the type and semantics of a sample value.

        Attempts to convert `raw` into the same type as `sample`. Handles:
        - datetimes: parses ISO-like strings, preserves or aligns timezone info with `sample`,
        - dates: parses ISO date strings,
        - booleans: recognizes common textual and numeric boolean representations,
        - other types: attempts to call the sample's type on `raw`.

        Parameters:
            sample: A value whose type and semantics should be used as the target.
            raw: The input value to coerce.

        Returns:
            The coerced value of the same type as `sample`, or `None` if `raw` cannot be sensibly converted.
        """
        if sample is None:
            return None

        if isinstance(sample, datetime):
            if isinstance(raw, datetime):
                parsed = raw
            elif isinstance(raw, str):
                candidate = raw.replace("Z", "+00:00")
                candidate = candidate.replace(" ", "T", 1)
                try:
                    parsed = datetime.fromisoformat(candidate)
                except ValueError:
                    return None
            else:
                return None

            if sample.tzinfo and parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=sample.tzinfo)
            elif not sample.tzinfo and parsed.tzinfo is not None:
                parsed = parsed.replace(tzinfo=None)
            return parsed

        if isinstance(sample, date) and not isinstance(sample, datetime):
            if isinstance(raw, date) and not isinstance(raw, datetime):
                return raw
            if isinstance(raw, str):
                try:
                    return date.fromisoformat(raw)
                except ValueError:
                    return None
            return None

        # Booleans: avoid bool("False") == True
        if isinstance(sample, bool):
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, (int,)):
                return bool(raw)
            if isinstance(raw, str):
                s = raw.strip().lower()
                if s in {"true", "1", "yes", "y", "t"}:
                    return True
                if s in {"false", "0", "no", "n", "f"}:
                    return False
            return None
        try:
            return type(sample)(raw)  # type: ignore
        except (TypeError, ValueError):
            if isinstance(raw, type(sample)):
                return raw
            return None

    def matches(op: str, value: Any, val_key: Any) -> bool:
        """
        Evaluate whether a given value satisfies a lookup operation described by `op` and `val_key`.

        Supports operators:
        - "eq": equality; attempts to interpret `val_key` as a literal and coerce it to `value`'s type before comparing.
        - "in": membership; expects `val_key` to be a literal iterable and checks if any coerced element equals `value`.
        - "gt", "gte", "lt", "lte": numeric/date comparisons; `val_key` is interpreted as a literal and coerced to `value`'s type.
        - "contains", "startswith", "endswith": string containment/prefix/suffix checks; both sides are compared as strings.
        - "regex": treats `val_key` as a regular expression pattern and tests it against the string form of `value`.

        Behavior notes:
        - If `value` is None the function returns `False`.
        - Literal parsing of `val_key` is attempted via AST literal evaluation; if parsing or coercion fails, the function falls back to conservative comparisons or returns `False` where appropriate.
        - Regex patterns that fail to compile are treated as non-matching.

        Parameters:
            op (str): The lookup operator name (one of the supported operators above).
            value (Any): The runtime value to test.
            val_key (Any): The stored comparison key (often a string representation) to interpret for the comparison.

        Returns:
            bool: `True` if the comparison defined by `op` and `val_key` matches `value`, `False` otherwise.
        """
        if value is None:
            return False

        # eq
        if op == "eq":
            literal_val = _safe_literal_eval(val_key)
            comparable = _coerce_to_type(value, literal_val)
            if comparable is None:
                return repr(value) == val_key
            return value == comparable

        # in
        if op == "in":
            try:
                seq = ast.literal_eval(val_key)
            except (ValueError, SyntaxError):
                return False
            for item in seq:
                comparable = _coerce_to_type(value, item)
                if comparable is not None:
                    if value == comparable:
                        return True
                elif repr(value) == repr(item):
                    return True
            return False

        # range comparisons
        if op in ("gt", "gte", "lt", "lte"):
            literal_val = _safe_literal_eval(val_key)
            thr = _coerce_to_type(value, literal_val)
            if thr is None:
                return False
            if op == "gt":
                return value > thr
            if op == "gte":
                return value >= thr
            if op == "lt":
                return value < thr
            if op == "lte":
                return value <= thr

        # wildcard / regex comparisons
        if op in ("contains", "startswith", "endswith", "regex"):
            try:
                literal = ast.literal_eval(val_key)
            except (ValueError, SyntaxError):
                literal = val_key

            # ensure we always work with strings to avoid TypeErrors
            text = "" if value is None else str(value)
            if op == "contains":
                return literal in text
            if op == "startswith":
                return text.startswith(literal)
            if op == "endswith":
                return text.endswith(literal)
            # regex: treat the stored key as the regex pattern
            if op == "regex":
                try:
                    pattern_source = (
                        literal if isinstance(literal, str) else str(literal)
                    )
                    pattern = re.compile(pattern_source)
                except re.error:
                    return False
                return bool(pattern.search(text))

        return False

    def current_value_for_path(path: list[str]) -> Any:
        """
        Fetches the current value from the captured `instance` by following a sequence of attribute names.

        Parameters:
            path (list[str]): Ordered attribute names to traverse on the instance (e.g., ["user", "profile", "email"]).

        Returns:
            The value found at the end of the attribute path, or `None` if any attribute along the path is missing.
        """
        current: object = instance
        for attr in path:
            current = getattr(current, attr, UNDEFINED)
            if current is UNDEFINED:
                return None
        return current

    def evaluate_composite(
        cache_key: str,
        lookup_key: str,
        action: Literal["filter", "exclude"],
        model_section: dict[str, dict[str, set[str]]],
    ) -> bool | None:
        """
        Determine whether a composite dependency (multiple lookup params grouped under a single identifier)
        for a given cache key and lookup should cause cache invalidation.

        Parameters:
            cache_key (str): The cache key being evaluated.
            lookup_key (str): The specific lookup (operator and attribute path joined by `"__"`) that prompted evaluation.
            action (Literal["filter", "exclude"]): The dependency action context; "filter" treats a match as cause for invalidation,
                "exclude" treats a change in match membership as cause for invalidation.
            model_section (dict[str, dict[str, set[str]]]): The index section for the model containing lookup maps and an
                optional "__cache_dependencies__" mapping from cache keys to sets of identifier strings (each identifier
                encodes multiple lookup parameters).

        Returns:
            bool | None: `True` if the composite dependency indicates the cache entry should be invalidated,
            `False` if it indicates no invalidation is required, or `None` if there are no composite identifiers
            registered for `cache_key`.
        """
        cache_dependencies = model_section.get("__cache_dependencies__", {})
        identifiers = cache_dependencies.get(cache_key) if cache_dependencies else None
        if not identifiers:
            return None

        for identifier in identifiers:
            params = ast.literal_eval(identifier)
            if lookup_key not in params:
                continue
            old_all = True
            new_all = True
            for param_lookup, expected in params.items():
                parts_param = param_lookup.split("__")
                if parts_param[-1] in (
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
                    op_param = parts_param[-1]
                    attr_path_param = parts_param[:-1]
                else:
                    op_param = "eq"
                    attr_path_param = parts_param
                expected_key = repr(expected)
                old_val_param = old_relevant_values.get("__".join(attr_path_param))
                new_val_param = current_value_for_path(attr_path_param)
                if not matches(op_param, old_val_param, expected_key):
                    old_all = False
                if not matches(op_param, new_val_param, expected_key):
                    new_all = False
                if not old_all and not new_all and action == "filter":
                    break
            if action == "filter":
                if old_all or new_all:
                    return True
            else:  # exclude
                if old_all != new_all:
                    return True
        return False

    for action in ACTIONS:
        model_section = idx[action].get(manager_name)
        if not isinstance(model_section, dict):
            continue
        for lookup, lookup_map in model_section.items():
            if lookup.startswith("__"):
                continue
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

            current: object = instance
            for attr in attr_path:
                current = getattr(current, attr, None)
                if current is None:
                    break
            new_val = current

            # 3) check against all cache_keys
            for val_key, cache_keys in list(lookup_map.items()):
                old_match = matches(op, old_val, val_key)
                new_match = matches(op, new_val, val_key)

                if action == "filter":
                    # Filter: invalidate if new match or old match
                    for ck in list(cache_keys):
                        composite_decision = evaluate_composite(
                            ck, lookup, action, model_section
                        )
                        should_invalidate = (
                            composite_decision
                            if composite_decision is not None
                            else (new_match or old_match)
                        )
                        if should_invalidate:
                            logger.info(
                                "invalidating cache key",
                                context={
                                    "manager": manager_name,
                                    "key": ck,
                                    "lookup": lookup,
                                    "action": action,
                                    "value": val_key,
                                },
                            )
                            invalidate_cache_key(ck)
                            remove_cache_key_from_index(ck)

                else:  # action == 'exclude'
                    # Excludes: invalidate only if matches changed
                    for ck in list(cache_keys):
                        composite_decision = evaluate_composite(
                            ck, lookup, action, model_section
                        )
                        should_invalidate = (
                            composite_decision
                            if composite_decision is not None
                            else (old_match != new_match)
                        )
                        if should_invalidate:
                            logger.info(
                                "invalidating cache key",
                                context={
                                    "manager": manager_name,
                                    "key": ck,
                                    "lookup": lookup,
                                    "action": action,
                                    "value": val_key,
                                },
                            )
                            invalidate_cache_key(ck)
                            remove_cache_key_from_index(ck)
