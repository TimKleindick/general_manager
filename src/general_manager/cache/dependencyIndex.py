from __future__ import annotations
import time
import ast
import re

from django.core.cache import cache
from general_manager.cache.signals import post_data_change, pre_data_change
from django.dispatch import receiver
from typing import Literal, Any, Iterable, TYPE_CHECKING, Type

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


# -----------------------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------------------
INDEX_KEY = "dependency_index"  # Key unter dem der gesamte Index liegt
LOCK_KEY = "dependency_index_lock"  # Key für das Sperr‑Mutex
LOCK_TIMEOUT = 5  # Sekunden TTL für den Lock


# -----------------------------------------------------------------------------
# LOCKING HELPERS
# -----------------------------------------------------------------------------
def acquire_lock(timeout: int = LOCK_TIMEOUT) -> bool:
    """Atomar: legt den LOCK_KEY an, wenn noch frei."""
    return cache.add(LOCK_KEY, "1", timeout)


def release_lock() -> None:
    """Gibt den Lock frei."""
    cache.delete(LOCK_KEY)


# -----------------------------------------------------------------------------
# INDEX ACCESS
# -----------------------------------------------------------------------------
def get_full_index() -> dependency_index:
    """Lädt oder initialisiert den kompletten Index."""
    idx = cache.get(INDEX_KEY, None)
    if idx is None:
        idx: dependency_index = {"filter": {}, "exclude": {}}
        cache.set(INDEX_KEY, idx, None)
    return idx


def set_full_index(idx: dependency_index) -> None:
    """Schreibt den kompletten Index zurück in den Cache."""
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
    Speichert die Abhängigkeiten eines Cache Eintrags.
    :param cache_key:   der Key unter dem das Ergebnis im cache steht
    :param dependencies: Iterable von Tuplen (model_name, action, identifier)
                         action ∈ {'filter','exclude'} oder sonstige → 'id'
                         identifier = für filter/exclude: Dict String,
                                      sonst: Primärschlüssel als String
    """
    # 1) Lock holen (Spin‑Lock mit Timeout)
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
                # Direkter ID‑Lookup als simpler filter auf 'id'
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
def remove_cache_key_from_index(cache_key: str) -> bool:
    """
    Entfernt einen cache_key aus allen Einträgen in filter/​exclude.
    Nützlich, sobald Du den Cache gelöscht hast.
    """
    start = time.time()
    while not acquire_lock():
        if time.time() - start > LOCK_TIMEOUT:
            return False
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
    return True


# -----------------------------------------------------------------------------
# CACHE INVALIDATION
# -----------------------------------------------------------------------------
def invalidate_cache_key(cache_key: str) -> None:
    """Löscht den CacheEintrag – hier nutzt du deinen CacheBackend."""
    cache.delete(cache_key)


@receiver(pre_data_change)
def capture_old_values(
    sender: Type[GeneralManager], instance: GeneralManager | None, **kwargs
) -> None:
    if instance is None:
        # Wenn es kein Modell ist, gibt es nichts zu tun
        return
    manager_name = sender.__name__
    idx = get_full_index()
    # Welche Lookups interessieren uns für diesen Model?
    lookups = set()
    for action in ("filter", "exclude"):
        lookups |= set(idx.get(action, {}).get(manager_name, {}))
    if lookups and instance.identification:
        # Speichere alle relevanten Attribute für später
        vals = {}
        for lookup in lookups:
            attr_path = lookup.split("__")
            obj = instance
            for attr in attr_path:
                obj = getattr(obj, attr, None)
                if obj is None:
                    break
            vals[lookup] = obj
        setattr(instance, "_old_values", vals)


# -----------------------------------------------------------------------------
# GENERIC CACHE INVALIDATION: vergleicht alt vs. neu und invalidiert nur bei Übergang
# -----------------------------------------------------------------------------


@receiver(post_data_change)
def generic_cache_invalidation(
    sender: type[GeneralManager],
    instance: GeneralManager,
    old_relevant_values: dict[str, Any],
    **kwargs,
):
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
                pattern = re.compile(val_key)
            except:
                return False
            text = value or ""
            return bool(pattern.search(text))

        return False

    for action in ("filter", "exclude"):
        model_section = idx.get(action, {}).get(manager_name, {})
        for lookup, lookup_map in model_section.items():
            # 1) Operator und Attributpfad ermitteln
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

            # 2) Alten und neuen Wert holen
            old_val = old_relevant_values.get(lookup)

            obj = instance
            for attr in attr_path:
                obj = getattr(obj, attr, None)
                if obj is None:
                    break
            new_val = obj

            # 3) Für jedes val_key prüfen
            for val_key, cache_keys in list(lookup_map.items()):
                old_match = matches(op, old_val, val_key)
                new_match = matches(op, new_val, val_key)

                if action == "filter":
                    # Direkte & alle Filter-Abhängigkeiten: immer invalidieren, wenn neu matcht
                    if new_match:
                        print(
                            f"Invalidate cache key {cache_keys} for filter {lookup} with value {val_key}"
                        )
                        for ck in list(cache_keys):
                            invalidate_cache_key(ck)
                            remove_cache_key_from_index(ck)

                else:  # action == 'exclude'
                    # Excludes: nur invalidieren, wenn sich der Match-Status ändert
                    if old_match != new_match:
                        print(
                            f"Invalidate cache key {cache_keys} for exclude {lookup} with value {val_key}"
                        )
                        for ck in list(cache_keys):
                            invalidate_cache_key(ck)
                            remove_cache_key_from_index(ck)
