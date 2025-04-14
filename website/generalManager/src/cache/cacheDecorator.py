from typing import Callable, Optional
from functools import wraps
from django.core.cache import cache as django_cache
from hashlib import sha256


def cached(timeout: Optional[int] = None) -> Callable:
    """
    Decorator to cache the result of a function for a specified timeout.
    If no timeout is provided, the cache will not expire.
    """

    def decorator(func: Callable) -> Callable:

        @wraps(func)
        def wrapper(*args, **kwargs):
            django_cache_key = sha256(
                f"{func.__module__}.{func.__name__}:{args}:{kwargs}".encode(),
                usedforsecurity=False,
            ).hexdigest()
            cached_result = django_cache.get(django_cache_key)
            if cached_result is not None:
                return cached_result
            result = func(*args, **kwargs)
            if timeout is not None:
                django_cache.set(django_cache_key, result, timeout)
            else:
                django_cache.set(django_cache_key, result)
            return result

        return wrapper

    return decorator
