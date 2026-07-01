"""Utilities for building deterministic cache keys from function calls."""

from collections.abc import Callable, Mapping
from functools import lru_cache
import inspect
import json
from hashlib import sha256

from general_manager.utils.json_encoder import CustomJSONEncoder

type CacheKeyArgs = tuple[object, ...]
type CacheKeyKwargs = Mapping[str, object]


@lru_cache(maxsize=None)
def _signature_for(func: Callable[..., object]) -> inspect.Signature:
    return inspect.signature(func)


def make_cache_key(
    func: Callable[..., object],
    args: CacheKeyArgs,
    kwargs: CacheKeyKwargs | None,
) -> str:
    """Build a deterministic cache key for one function invocation.

    `kwargs=None` is treated as an empty mapping; supplied mappings are copied
    with `dict(...)` even when they are falsey. The function module, qualified
    name, and normalized bound arguments are encoded as JSON with sorted keys and
    `CustomJSONEncoder`, then hashed with SHA-256 using `usedforsecurity=False`.
    Positional and keyword forms of the same call produce the same key after
    `inspect.Signature.bind_partial()` and default application.

    Raises:
        TypeError: If `args` and `kwargs` cannot bind to `func`'s signature, or
            if payload serialization fails before `CustomJSONEncoder` can fall
            back to strings.
    """
    sig = _signature_for(func)
    kwargs_dict = {} if kwargs is None else dict(kwargs)
    bound = sig.bind_partial(*args, **kwargs_dict)
    bound.apply_defaults()
    payload = {
        "module": func.__module__,
        "qualname": func.__qualname__,
        "args": bound.arguments,
    }
    raw = json.dumps(payload, sort_keys=True, cls=CustomJSONEncoder).encode()
    return sha256(raw, usedforsecurity=False).hexdigest()
