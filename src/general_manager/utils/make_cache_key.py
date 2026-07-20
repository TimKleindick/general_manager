"""Utilities for building deterministic cache keys from function calls."""

from collections.abc import Callable, Mapping
from functools import lru_cache
import inspect
import json
from json.encoder import encode_basestring_ascii
from hashlib import sha256
from datetime import datetime
from typing import TYPE_CHECKING, cast

from general_manager.as_of import (
    as_of_cache_fingerprint,
    search_date_cache_fingerprint,
)
from general_manager.utils.json_encoder import CustomJSONEncoder

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager

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


@lru_cache(maxsize=1)
def _general_manager_class() -> type[object]:
    from general_manager.manager.general_manager import GeneralManager

    return GeneralManager


@lru_cache(maxsize=None)
def _single_manager_arg_cache_key_parts(
    parameter_name: str,
    module: str,
    qualname: str,
) -> tuple[bytes, bytes]:
    prefix = (f'{{"args": {{{encode_basestring_ascii(parameter_name)}: ').encode()
    invariant_suffix = (
        f', "module": {encode_basestring_ascii(module)}, '
        f'"qualname": {encode_basestring_ascii(qualname)}}}'
    ).encode()
    return prefix, invariant_suffix


@lru_cache(maxsize=65536)
def _single_manager_arg_cache_key_from_repr(
    parameter_name: str,
    module: str,
    qualname: str,
    manager_class_name: str,
    identification_repr: str,
    manager_fingerprint: str | None,
    active_fingerprint: str | None,
) -> str:
    manager_value = f"{manager_class_name}(**{identification_repr})"
    if manager_fingerprint is not None:
        manager_value += f"@as_of({manager_fingerprint})"
    prefix, invariant_suffix = _single_manager_arg_cache_key_parts(
        parameter_name,
        module,
        qualname,
    )
    hash_builder = sha256(usedforsecurity=False)
    hash_builder.update(prefix)
    hash_builder.update(encode_basestring_ascii(manager_value).encode())
    if active_fingerprint is None:
        hash_builder.update(b"}")
    else:
        hash_builder.update(b'}, "as_of": ')
        hash_builder.update(encode_basestring_ascii(active_fingerprint).encode())
    hash_builder.update(invariant_suffix)
    return hash_builder.hexdigest()


def _single_manager_arg_cache_key(
    func: Callable[..., object],
    parameter_name: str,
    value: object,
) -> str | None:
    """Return the generic JSON-equivalent key for a single manager argument."""
    if not isinstance(value, _general_manager_class()):
        return None
    manager = cast("GeneralManager", value)
    manager_class_name = type.__getattribute__(manager.__class__, "__name__")
    identification_repr = f"{manager.identification}"
    search_date = manager.__dict__.get("_effective_search_date")
    manager_fingerprint = (
        search_date_cache_fingerprint(search_date)
        if isinstance(search_date, datetime)
        else None
    )
    return _single_manager_arg_cache_key_from_repr(
        parameter_name,
        func.__module__,
        func.__qualname__,
        manager_class_name,
        identification_repr,
        manager_fingerprint,
        as_of_cache_fingerprint(),
    )


def _add_as_of_fingerprint(payload: dict[str, object]) -> None:
    """Add the active historical namespace without changing current payloads."""
    fingerprint = as_of_cache_fingerprint()
    if fingerprint is not None:
        payload["as_of"] = fingerprint


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
    An active historical snapshot adds its ISO datetime as an `as_of` payload
    field; current payload bytes remain unchanged. Positional and keyword forms
    of the same call produce the same key after `inspect.Signature.bind_partial()`
    and default application.

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
            payload: dict[str, object] = {
                "module": func.__module__,
                "qualname": func.__qualname__,
                "args": dict(zip(positional_names, args, strict=True)),
            }
            _add_as_of_fingerprint(payload)
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
    _add_as_of_fingerprint(payload)
    raw = json.dumps(payload, sort_keys=True, cls=CustomJSONEncoder).encode()
    return sha256(raw, usedforsecurity=False).hexdigest()
