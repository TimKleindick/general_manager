"""Bounded immutable signatures for known executable evidence contracts."""

import enum
from types import FunctionType, MethodType
from typing import cast

_ENUM_NOT_GIVEN: object = vars(enum)["_not_given"]
_NONE_LITERAL_SIGNATURE = (type(None), None)
_ONE_NONE_DEFAULT_SIGNATURE = (tuple, (_NONE_LITERAL_SIGNATURE,))


def literal_signature(
    value: object,
    *,
    _path: frozenset[int] = frozenset(),
    _depth: int = 0,
    _remaining: list[int] | None = None,
) -> object | None:
    """Return an immutable literal key, or no proof for opaque/cyclic defaults.

    The depth and 256-node limits bound authority inspection only; never ordinary
    callback data or execution. Backend objects are not traversed.
    """
    remaining = [256] if _remaining is None else _remaining
    if remaining[0] <= 0:
        return None
    remaining[0] -= 1
    # EnumType.__call__ checks this stdlib absence sentinel only by identity.
    if value is _ENUM_NOT_GIVEN:
        return ("enum_not_given", id(value))
    if value is None or type(value) in (str, int, float, bool):
        return (type(value), value)
    if _depth >= 32 or id(value) in _path:
        return None
    path = _path | {id(value)}
    if type(value) in (tuple, list):
        keys = []
        for item in cast(tuple[object, ...] | list[object], value):
            key = literal_signature(
                item, _path=path, _depth=_depth + 1, _remaining=remaining
            )
            if key is None:
                return None
            keys.append(key)
        return (type(value), tuple(keys))
    if type(value) is dict:
        pairs = []
        for key, item in value.items():
            key_signature = literal_signature(
                key, _path=path, _depth=_depth + 1, _remaining=remaining
            )
            item_signature = literal_signature(
                item, _path=path, _depth=_depth + 1, _remaining=remaining
            )
            if key_signature is None or item_signature is None:
                return None
            pairs.append((key_signature, item_signature))
        return (dict, tuple(pairs))
    return None


def executable_signature(value: object) -> object | None:
    """Pin function code/defaults and exact captured bindings without backend reads."""
    if isinstance(value, MethodType):
        signature = executable_signature(value.__func__)
        return None if signature is None else (id(value.__self__), signature)
    if not isinstance(value, FunctionType):
        # Native builtins have no mutable Python code/default/closure state.
        return None
    defaults = (
        _NONE_LITERAL_SIGNATURE
        if value.__defaults__ is None
        else literal_signature(value.__defaults__)
    )
    keywords = (
        _NONE_LITERAL_SIGNATURE
        if value.__kwdefaults__ is None
        else literal_signature(value.__kwdefaults__)
    )
    if defaults is None or keywords is None:
        return None
    try:
        closure = (
            ()
            if value.__closure__ is None
            else tuple(
                (type(cell.cell_contents), cell.cell_contents)
                if cell.cell_contents is None
                or type(cell.cell_contents) in (str, int, float, bool)
                else (type(cell.cell_contents), id(cell.cell_contents))
                for cell in value.__closure__ or ()
            )
        )
    except ValueError:
        return None
    return (value.__code__, defaults, keywords, closure)


def executable_matches(value: object, signature: object) -> bool:
    """A missing bounded signature can never authorize an opt-in proof."""
    if signature is None:
        return False
    # The common scalar helpers have no defaults or captured bindings. Check
    # their current state directly rather than rebuilding the same literal
    # tuples millions of times during a schema observation. Every invocation
    # still observes code, defaults, keyword defaults and closure afresh.
    if (
        isinstance(value, FunctionType)
        and type(signature) is tuple
        and len(signature) == 4
        and (
            (value.__defaults__ is None and signature[1] == _NONE_LITERAL_SIGNATURE)
            or (
                type(value.__defaults__) is tuple
                and len(value.__defaults__) == 1
                and value.__defaults__[0] is None
                and signature[1] == _ONE_NONE_DEFAULT_SIGNATURE
            )
        )
        and signature[2] == _NONE_LITERAL_SIGNATURE
        and signature[3] == ()
        and value.__kwdefaults__ is None
        and value.__closure__ is None
    ):
        return bool(value.__code__ == signature[0])
    return executable_signature(value) == signature
