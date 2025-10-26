"""Utilities for building deterministic cache keys from function calls."""

import inspect
import json
from hashlib import sha256
from typing import Callable, Mapping

from general_manager.utils.json_encoder import CustomJSONEncoder


def make_cache_key(
    func: Callable[..., object],
    args: tuple[object, ...],
    kwargs: Mapping[str, object] | None,
) -> str:
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
    kwargs_dict = dict(kwargs or {})
    bound = sig.bind_partial(*args, **kwargs_dict)
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
