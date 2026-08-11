"""Process-local memory accounting for calculation run caches."""

from __future__ import annotations

import sys
from types import CodeType, FunctionType, MethodType, ModuleType
from typing import Iterable, cast

from django.core.exceptions import ImproperlyConfigured

from general_manager.conf import get_setting

RUN_CONTEXT_CACHE_MAX_BYTES_SETTING = "RUN_CONTEXT_CACHE_MAX_BYTES"
MIN_TRACKED_ENTRY_BYTES = 256
_INVALID_MAX_BYTES_MESSAGE = (
    'GENERAL_MANAGER["RUN_CONTEXT_CACHE_MAX_BYTES"] must be None or a '
    "non-negative integer number of bytes."
)

_SHALLOW_LEAF_TYPES = (ModuleType, type, FunctionType, MethodType, CodeType)
_SIZED_BUILTIN_TYPES = (
    type(None),
    bool,
    int,
    float,
    complex,
    str,
    bytes,
    bytearray,
    range,
    dict,
    tuple,
    list,
    set,
    frozenset,
)


def resolve_run_context_cache_max_bytes() -> int | None:
    configured = get_setting(RUN_CONTEXT_CACHE_MAX_BYTES_SETTING)
    if configured is None:
        return None
    if isinstance(configured, bool) or not isinstance(configured, int):
        raise ImproperlyConfigured(_INVALID_MAX_BYTES_MESSAGE)
    if configured < 0:
        raise ImproperlyConfigured(_INVALID_MAX_BYTES_MESSAGE)
    return configured


def estimate_cache_entry_size(
    key: object,
    value: object,
    *,
    stop_after: int | None,
) -> int:
    """Estimate owned bytes for one cache entry without unbounded traversal."""
    measured_bytes = 0
    seen: set[int] = set()
    candidates = [key, value]

    while candidates:
        candidate = candidates.pop()
        candidate_id = id(candidate)
        if candidate_id in seen:
            continue
        seen.add(candidate_id)

        candidate_type = type(candidate)
        try:
            if candidate_type in _SIZED_BUILTIN_TYPES:
                measured_bytes += sys.getsizeof(candidate)
            else:
                measured_bytes += object.__sizeof__(candidate)
        except Exception:  # noqa: BLE001 - conservative accounting must survive sizing errors.
            measured_bytes += MIN_TRACKED_ENTRY_BYTES

        if stop_after is not None and measured_bytes > stop_after:
            return stop_after + 1

        if isinstance(candidate, _SHALLOW_LEAF_TYPES):
            continue
        if candidate_type is dict:
            mapping = cast(dict[object, object], candidate)
            for item_key, item_value in mapping.items():
                candidates.extend((item_key, item_value))
        elif candidate_type in (tuple, list, set, frozenset):
            candidates.extend(cast(Iterable[object], candidate))
        else:
            try:
                candidates.append(object.__getattribute__(candidate, "__dict__"))
            except (AttributeError, TypeError):
                pass

            for cls in candidate_type.__mro__:
                slots = vars(cls).get("__slots__")
                if isinstance(slots, str):
                    slots = (slots,)
                if not isinstance(slots, (tuple, list, set, frozenset)):
                    continue
                for slot in slots:
                    if slot in {"__dict__", "__weakref__"}:
                        continue
                    if slot.startswith("__") and not slot.endswith("__"):
                        class_name = type.__getattribute__(cls, "__name__")
                        slot = f"_{class_name.lstrip('_')}{slot}"
                    try:
                        candidates.append(object.__getattribute__(candidate, slot))
                    except (AttributeError, TypeError):
                        pass

    return max(MIN_TRACKED_ENTRY_BYTES, measured_bytes)
