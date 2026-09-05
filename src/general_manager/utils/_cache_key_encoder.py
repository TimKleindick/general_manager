"""Private tagged value encoding for cache-key identity."""

from collections.abc import Mapping, Set
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import base64
from functools import lru_cache
import json
from typing import TYPE_CHECKING, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from general_manager.as_of import search_date_cache_fingerprint
from general_manager.measurement import Measurement

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager


class UnsupportedCacheKeyValueError(TypeError):
    """Raised when a value cannot participate in deterministic cache identity."""

    @classmethod
    def cycle(cls) -> "UnsupportedCacheKeyValueError":
        return cls("Unsupported cache-key cycle")

    @classmethod
    def opaque(cls, value: object) -> "UnsupportedCacheKeyValueError":
        value_type = type(value)
        return cls(
            f"Unsupported cache-key value: "
            f"{value_type.__module__}.{value_type.__qualname__}"
        )


def canonical_cache_key_json(value: object) -> str:
    """Serialize already-tagged cache-key values deterministically."""
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _encode_timedelta(value: timedelta) -> list[object]:
    return [value.days, value.seconds, value.microseconds]


def _encode_timezone(value: datetime | time) -> object:
    timezone_value = value.tzinfo
    if timezone_value is None:
        return ["none"]
    # A `time` without a date cannot determine a ZoneInfo offset, but its zone
    # remains part of the caller-provided value and therefore cache identity.
    if isinstance(timezone_value, ZoneInfo):
        return ["zoneinfo", timezone_value.key]
    if isinstance(timezone_value, timezone):
        offset = value.utcoffset()
        assert offset is not None
        return ["fixed", _encode_timedelta(offset), value.tzname()]
    raise UnsupportedCacheKeyValueError.opaque(timezone_value)


def _encode_datetime(value: datetime) -> list[object]:
    if type(value) is not datetime:
        raise UnsupportedCacheKeyValueError.opaque(value)
    return [
        "datetime",
        value.year,
        value.month,
        value.day,
        value.hour,
        value.minute,
        value.second,
        value.microsecond,
        value.fold,
        _encode_timezone(value),
    ]


def _encode_time(value: time) -> list[object]:
    if type(value) is not time:
        raise UnsupportedCacheKeyValueError.opaque(value)
    return [
        "time",
        value.hour,
        value.minute,
        value.second,
        value.microsecond,
        value.fold,
        _encode_timezone(value),
    ]


def _encode_manager(value: object, active_ids: set[int]) -> list[object]:
    manager = cast("GeneralManager", value)
    manager_type = type(manager)
    missing = object()
    search_date = manager.__dict__.get("_effective_search_date", missing)
    if search_date is missing:
        from general_manager.manager.general_manager import (
            _legacy_effective_search_date,
        )

        search_date = _legacy_effective_search_date(manager)
    snapshot: object = (
        ["none"]
        if not isinstance(search_date, datetime)
        else ["as_of", search_date_cache_fingerprint(search_date)]
    )
    return [
        "manager",
        type.__getattribute__(manager_type, "__module__"),
        type.__getattribute__(manager_type, "__qualname__"),
        encode_cache_key_value(manager.identification, active_ids),
        snapshot,
    ]


@lru_cache(maxsize=1)
def _general_manager_class() -> type[object]:
    from general_manager.manager.general_manager import GeneralManager

    return GeneralManager


def is_general_manager(value: object) -> bool:
    """Return whether a value has the framework manager identity contract."""
    return isinstance(value, _general_manager_class())


def _encode_mapping(
    value: Mapping[object, object], active_ids: set[int]
) -> list[object]:
    entries = [
        [
            encode_cache_key_value(key, active_ids),
            encode_cache_key_value(item, active_ids),
        ]
        for key, item in value.items()
    ]
    if len(entries) > 1:
        entries.sort(key=canonical_cache_key_json)
    return ["mapping", entries]


def _encode_unordered_container(
    tag: str,
    value: Set[object] | frozenset[object],
    active_ids: set[int],
) -> list[object]:
    items = [encode_cache_key_value(item, active_ids) for item in value]
    if len(items) > 1:
        items.sort(key=canonical_cache_key_json)
    return [tag, items]


def encode_cache_key_value(value: object, active_ids: set[int] | None = None) -> object:
    """Encode one supported cache-key value with explicit non-overlapping tags."""
    if active_ids is None:
        active_ids = set()
    if value is None:
        return ["none"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        return ["float", value.hex()]
    if isinstance(value, Decimal):
        decimal = value.as_tuple()
        return ["decimal", decimal.sign, list(decimal.digits), decimal.exponent]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, bytes):
        return ["bytes", base64.b64encode(value).decode("ascii")]
    if isinstance(value, UUID):
        return ["uuid", str(value)]
    if isinstance(value, datetime):
        return _encode_datetime(value)
    if isinstance(value, date):
        if type(value) is not date:
            raise UnsupportedCacheKeyValueError.opaque(value)
        return ["date", value.isoformat()]
    if isinstance(value, time):
        return _encode_time(value)
    if isinstance(value, timedelta):
        return ["timedelta", *_encode_timedelta(value)]
    if isinstance(value, Measurement):
        return ["measurement", value.unit, encode_cache_key_value(value.magnitude)]

    is_manager = is_general_manager(value)
    tracks_identity = (
        isinstance(value, (Mapping, list, tuple, set, frozenset)) or is_manager
    )
    if tracks_identity:
        value_id = id(value)
        if value_id in active_ids:
            raise UnsupportedCacheKeyValueError.cycle()
        active_ids.add(value_id)
        try:
            if is_manager:
                return _encode_manager(value, active_ids)
            if isinstance(value, Mapping):
                return _encode_mapping(value, active_ids)
            if isinstance(value, list):
                return [
                    "list",
                    [encode_cache_key_value(item, active_ids) for item in value],
                ]
            if isinstance(value, tuple):
                return [
                    "tuple",
                    [encode_cache_key_value(item, active_ids) for item in value],
                ]
            if isinstance(value, set):
                return _encode_unordered_container("set", value, active_ids)
            if isinstance(value, frozenset):
                return _encode_unordered_container("frozenset", value, active_ids)
        finally:
            active_ids.remove(value_id)
    raise UnsupportedCacheKeyValueError.opaque(value)


def freeze_encoded_cache_key_value(value: object) -> object:
    """Return an immutable equivalent of an already-tagged cache-key value."""
    if isinstance(value, list):
        return tuple(freeze_encoded_cache_key_value(item) for item in value)
    return value
