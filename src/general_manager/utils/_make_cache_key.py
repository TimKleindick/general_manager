"""Build versioned cache keys from normalized function calls."""

from collections.abc import Callable, Mapping
from functools import lru_cache
from hashlib import sha256
import inspect

from general_manager.as_of import as_of_cache_fingerprint
from general_manager.utils._cache_key_encoder import (
    canonical_cache_key_json,
    encode_cache_key_value,
    freeze_encoded_cache_key_value,
    is_general_manager,
)

type CacheKeyArgs = tuple[object, ...]
type CacheKeyKwargs = Mapping[str, object]

CALL_KEY_NAMESPACE = "gm:call:v2"


@lru_cache(maxsize=None)
def _cached_signature_for(func: Callable[..., object]) -> inspect.Signature:
    return inspect.signature(func)


def _signature_for(func: Callable[..., object]) -> inspect.Signature:
    try:
        hash(func)
    except TypeError:
        return inspect.signature(func)
    return _cached_signature_for(func)


def _simple_positional_parameter_names_from_signature(
    signature: inspect.Signature,
) -> tuple[str, ...] | None:
    """Return parameter names when positional binding is a direct zip."""
    parameters = tuple(signature.parameters.values())
    if any(
        parameter.kind
        not in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
        or parameter.default is not inspect.Parameter.empty
        for parameter in parameters
    ):
        return None
    return tuple(parameter.name for parameter in parameters)


@lru_cache(maxsize=None)
def _cached_simple_positional_parameter_names(
    func: Callable[..., object],
) -> tuple[str, ...] | None:
    return _simple_positional_parameter_names_from_signature(
        _cached_signature_for(func)
    )


def _simple_positional_parameter_names(
    func: Callable[..., object],
) -> tuple[str, ...] | None:
    try:
        hash(func)
    except TypeError:
        return _simple_positional_parameter_names_from_signature(
            inspect.signature(func)
        )
    return _cached_simple_positional_parameter_names(func)


def _call_key_from_encoded_arguments(
    module: str,
    qualname: str,
    encoded_arguments: object,
    active_fingerprint: str | None,
) -> str:
    payload = [
        "call",
        ["module", module],
        ["qualname", qualname],
        ["args", encoded_arguments],
        ["as_of", active_fingerprint],
    ]
    digest = sha256(
        canonical_cache_key_json(payload).encode(), usedforsecurity=False
    ).hexdigest()
    return f"{CALL_KEY_NAMESPACE}:{digest}"


@lru_cache(maxsize=65_536)
def _single_manager_arg_cache_key(
    parameter_name: str,
    module: str,
    qualname: str,
    encoded_manager: object,
    active_fingerprint: str | None,
) -> str:
    """Return a cached canonical key for one immutable tagged manager argument."""
    encoded_arguments = [
        "mapping",
        [[["str", parameter_name], encoded_manager]],
    ]
    return _call_key_from_encoded_arguments(
        module,
        qualname,
        encoded_arguments,
        active_fingerprint,
    )


def _single_manager_arg_fast_key(
    func: Callable[..., object],
    parameter_name: str,
    value: object,
) -> str | None:
    if not is_general_manager(value):
        return None
    encoded_manager = freeze_encoded_cache_key_value(encode_cache_key_value(value))
    return _single_manager_arg_cache_key(
        parameter_name,
        func.__module__,
        func.__qualname__,
        encoded_manager,
        as_of_cache_fingerprint(),
    )


def make_cache_key(
    func: Callable[..., object],
    args: CacheKeyArgs,
    kwargs: CacheKeyKwargs | None,
) -> str:
    """Build a tagged, versioned cache key for one function invocation.

    The single-manager positional path avoids signature binding and whole-call
    rehashing after the fully tagged identity has been seen. It uses exactly the
    same payload as generic binding and never memoizes mutable manager instances.
    """
    if kwargs is None or len(kwargs) == 0:
        positional_names = _simple_positional_parameter_names(func)
        if positional_names is not None and len(positional_names) == len(args):
            if len(args) == 1:
                fast_key = _single_manager_arg_fast_key(
                    func,
                    positional_names[0],
                    args[0],
                )
                if fast_key is not None:
                    return fast_key
            encoded_arguments = encode_cache_key_value(
                dict(zip(positional_names, args, strict=True))
            )
            return _call_key_from_encoded_arguments(
                func.__module__,
                func.__qualname__,
                encoded_arguments,
                as_of_cache_fingerprint(),
            )

    signature = _signature_for(func)
    bound = signature.bind_partial(*args, **({} if kwargs is None else dict(kwargs)))
    bound.apply_defaults()
    return _call_key_from_encoded_arguments(
        func.__module__,
        func.__qualname__,
        encode_cache_key_value(bound.arguments),
        as_of_cache_fingerprint(),
    )
