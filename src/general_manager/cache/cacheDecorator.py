from typing import Callable, Optional
from functools import wraps
from django.core.cache import cache as django_cache
from hashlib import sha256
from general_manager.cache.cacheTracker import DependencyTracker
from general_manager.cache.dependencyIndex import record_dependencies, get_full_index


def cached(timeout: Optional[int] = None) -> Callable:
    """
    Decorator to cache the result of a function for a specified timeout.
    If no timeout is provided, the cache will expire when a dependency is invalidated.
    """

    def decorator(func: Callable) -> Callable:

        @wraps(func)
        def wrapper(*args, **kwargs):
            from general_manager.manager.generalManager import GeneralManager, Bucket

            django_cache_key = sha256(
                f"{func.__module__}.{func.__name__}:{args}:{kwargs}".encode(),
                usedforsecurity=False,
            ).hexdigest()
            cached_result = django_cache.get(django_cache_key)
            if cached_result is not None:
                return cached_result
            # Dependency Tracking aktivieren
            with DependencyTracker() as dependencies:
                result = func(*args, **kwargs)

            def collect_model_dependencies(obj):
                """Rekursiv Django-Model-Instanzen im Objekt finden."""
                if isinstance(obj, GeneralManager):
                    yield (
                        obj.__class__.__name__,
                        "identification",
                        f"{obj.identification}",
                    )
                elif isinstance(obj, Bucket):
                    yield (obj._manager_class.__name__, "filter", f"{obj.filters}")
                    yield (obj._manager_class.__name__, "exclude", f"{obj.excludes}")
                elif isinstance(obj, dict):
                    for v in obj.values():
                        yield from collect_model_dependencies(v)
                elif isinstance(obj, (list, tuple, set)):
                    for item in obj:
                        yield from collect_model_dependencies(item)

            if args and isinstance(args[0], GeneralManager):
                self = args[0]
                for attr_val in self.__dict__.values():
                    for dependency_tuple in collect_model_dependencies(attr_val):
                        dependencies.add(dependency_tuple)

            for dependency_tuple in collect_model_dependencies(args):
                dependencies.add(dependency_tuple)
            for dependency_tuple in collect_model_dependencies(kwargs):
                dependencies.add(dependency_tuple)

            django_cache.set(django_cache_key, result, timeout)

            if dependencies and not timeout:
                record_dependencies(
                    django_cache_key,
                    dependencies,
                )
            return result

        return wrapper

    return decorator
