"""Utilities for building deterministic cache keys from function calls."""

from collections.abc import Callable, Mapping
from functools import lru_cache
import inspect
import json
from json.encoder import encode_basestring_ascii
from hashlib import sha256

from general_manager.utils.json_encoder import CustomJSONEncoder

type CacheKeyArgs = tuple[object, ...]
type CacheKeyKwargs = Mapping[str, object]


@lru_cache(maxsize=None)
def _cached_signature_for(func: Callable[..., object]) -> inspect.Signature:
    return inspect.signature(func)


def _signature_for(func: Callable[..., object]) -> inspect.Signature:
    try:
        hash(func)
    except TypeError:
        return inspect.signature(func)
    return _cached_signature_for(func)


def _simple_positional_parameter_names(
    func: Callable[..., object],
) -> tuple[str, ...] | None:
    """Return parameter names for signatures where binding is pure positional zip."""
    parameters = tuple(_signature_for(func).parameters.values())
    for parameter in parameters:
        if parameter.kind not in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }:
            return None
        if parameter.default is not inspect.Parameter.empty:
            return None
    return tuple(parameter.name for parameter in parameters)


def _single_manager_arg_cache_key(
    func: Callable[..., object],
    parameter_name: str,
    value: object,
) -> str | None:
    """Return the generic JSON-equivalent key for a single manager argument."""
    from general_manager.manager.general_manager import GeneralManager

    if not isinstance(value, GeneralManager):
        return None
    manager_value = f"{value.__class__.__name__}(**{value.identification})"
    raw = (
        '{"args": {'
        f"{encode_basestring_ascii(parameter_name)}: "
        f"{encode_basestring_ascii(manager_value)}"
        f'}}, "module": {encode_basestring_ascii(func.__module__)}, '
        f'"qualname": {encode_basestring_ascii(func.__qualname__)}}}'
    ).encode()
    return sha256(raw, usedforsecurity=False).hexdigest()


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
    if kwargs is None or len(kwargs) == 0:
        positional_names = _simple_positional_parameter_names(func)
        if positional_names is not None and len(positional_names) == len(args):
            if len(args) == 1:
                single_manager_key = _single_manager_arg_cache_key(
                    func,
                    positional_names[0],
                    args[0],
                )
                if single_manager_key is not None:
                    return single_manager_key
            payload = {
                "module": func.__module__,
                "qualname": func.__qualname__,
                "args": dict(zip(positional_names, args, strict=True)),
            }
            raw = json.dumps(payload, sort_keys=True, cls=CustomJSONEncoder).encode()
            return sha256(raw, usedforsecurity=False).hexdigest()

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
