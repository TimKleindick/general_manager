"""Utilities for building deterministic cache keys from function calls."""

import inspect
import json
from general_manager.utils.jsonEncoder import CustomJSONEncoder
from hashlib import sha256


def make_cache_key(func, args, kwargs):
    """
    Build a deterministic cache key that uniquely identifies a function invocation.

    Parameters:
        func (Callable[..., Any]): The function whose invocation should be cached.
        args (tuple[Any, ...]): Positional arguments supplied to the function.
        kwargs (dict[str, Any]): Keyword arguments supplied to the function.

    Returns:
        str: Hexadecimal SHA-256 digest representing the call signature.
    """
    sig = inspect.signature(func)
    bound = sig.bind_partial(*args, **kwargs)
    bound.apply_defaults()
    payload = {
        "module": func.__module__,
        "qualname": func.__qualname__,
        "args": bound.arguments,
    }
    raw = json.dumps(
        payload, sort_keys=True, default=str, cls=CustomJSONEncoder
    ).encode()
    return sha256(raw, usedforsecurity=False).hexdigest()
