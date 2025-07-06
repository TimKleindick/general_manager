import inspect
import json
from general_manager.utils.jsonEncoder import CustomJSONEncoder
from hashlib import sha256


def make_cache_key(func, args, kwargs):
    """
    Generates a unique, deterministic cache key for a specific function call.

    The key is derived from the function's module, qualified name, and bound arguments,
    serialized to JSON and hashed with SHA-256 to ensure uniqueness for each call signature.

    Args:
        func: The target function to be identified.
        args: Positional arguments for the function call.
        kwargs: Keyword arguments for the function call.

    Returns:
        A hexadecimal SHA-256 hash string uniquely representing the function call.
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
