import inspect
import json
from general_manager.auxiliary.jsonEncoder import CustomJSONEncoder
from hashlib import sha256


def make_cache_key(func, args, kwargs):
    sig = inspect.signature(func)
    bound = sig.bind_partial(*args, **kwargs)
    bound.apply_defaults()
    payload = {
        "module": func.__module__,
        "name": func.__name__,
        "args": bound.arguments,
    }
    raw = json.dumps(
        payload, sort_keys=True, default=str, cls=CustomJSONEncoder
    ).encode()
    return sha256(raw, usedforsecurity=False).hexdigest()
