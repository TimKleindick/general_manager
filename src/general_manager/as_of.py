"""Operation-scoped historical date context."""

from __future__ import annotations

from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterator,
    Mapping,
)
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime, time
from inspect import isasyncgenfunction, isawaitable, isgeneratorfunction, unwrap
from typing import cast

from django.utils import timezone

type SearchDateInput = str | date | datetime


class InvalidSearchDateError(ValueError):
    """Raised when a search date cannot be normalized."""

    def __init__(self, value: object) -> None:
        super().__init__(f"Invalid search date: {value!r}")


class HistoricalContextConflictError(RuntimeError):
    """Raised when historical dates conflict within one operation."""

    def __init__(self) -> None:
        super().__init__("Conflicting historical search dates are not allowed.")


class HistoricalMutationError(RuntimeError):
    """Raised when a mutation is attempted in historical context."""

    def __init__(self, active: datetime | None = None) -> None:
        message = (
            f"Mutations are not allowed in an as_of context ({active.isoformat()})."
            if active is not None
            else "Mutations are not allowed in historical context."
        )
        super().__init__(message)


class HistoricalReadNotSupportedError(RuntimeError):
    """Raised when a read does not support historical context."""

    def __init__(self, interface_name: str | None = None) -> None:
        message = (
            f"{interface_name} does not support historical reads."
            if interface_name is not None
            else "This read does not support historical context."
        )
        super().__init__(message)


_AS_OF_DATE: ContextVar[datetime | None] = ContextVar("as_of_date", default=None)


def _parse_search_date(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError


def _normalize_search_date(value: object) -> datetime:
    normalized = _parse_search_date(value)
    if timezone.is_naive(normalized):
        return timezone.make_aware(normalized, timezone.get_current_timezone())
    return normalized


def normalize_search_date(value: SearchDateInput) -> datetime:
    """Normalize a search date to an aware datetime."""
    try:
        return _normalize_search_date(value)
    except (TypeError, ValueError) as error:
        raise InvalidSearchDateError(value) from error


def current_as_of_date() -> datetime | None:
    """Return the historical date active for the current operation."""
    return _AS_OF_DATE.get()


def search_date_cache_fingerprint(search_date: datetime) -> str:
    """Return one canonical cache identity for a historical instant."""
    return search_date.astimezone(UTC).isoformat()


def as_of_cache_fingerprint() -> str | None:
    """Return the active historical datetime's cache namespace value."""
    active = current_as_of_date()
    return None if active is None else search_date_cache_fingerprint(active)


def reject_historical_mutation(bound: object | None = None) -> None:
    """Reject mutations in a historical operation or through a dated object."""
    active = current_as_of_date()
    if active is not None:
        raise HistoricalMutationError(active)
    state = getattr(bound, "__dict__", None)
    if isinstance(state, Mapping):
        snapshot = state.get("_effective_search_date", state.get("_search_date"))
        if isinstance(snapshot, datetime):
            raise HistoricalMutationError(snapshot)


def ensure_as_of_read_supported(interface_cls: type[object]) -> None:
    """Reject historical reads for interfaces without supported behavior."""
    if current_as_of_date() is None:
        return
    if getattr(interface_cls, "_as_of_behavior", "unsupported") not in {
        "historical",
        "transparent",
    }:
        raise HistoricalReadNotSupportedError(interface_cls.__name__)


def _represents_same_instant(left: datetime, right: datetime) -> bool:
    return left.astimezone(UTC) == right.astimezone(UTC)


def resolve_search_date(explicit: SearchDateInput | None) -> datetime | None:
    """Resolve an explicit search date against the active historical context."""
    active = current_as_of_date()
    if explicit is None:
        return active

    normalized = normalize_search_date(explicit)
    if active is not None and not _represents_same_instant(active, normalized):
        raise HistoricalContextConflictError
    return normalized


@contextmanager
def _property_as_of(instance: object) -> Iterator[datetime | None]:
    """Evaluate a manager property and its cache under the bound snapshot."""
    ensure = getattr(instance, "_ensure_as_of_compatible", None)
    if callable(ensure):
        ensure()
    state = getattr(instance, "__dict__", {})
    snapshot = state.get("_effective_search_date")
    if isinstance(snapshot, datetime):
        with as_of(snapshot):
            yield snapshot
    else:
        yield current_as_of_date()


def _bind_property_result(value: object, snapshot: datetime | None) -> object:
    """Bind awaitables and reject iterator bodies that can escape the scope."""
    if snapshot is None:
        return value
    if isinstance(value, (Iterator, AsyncIterator)):
        close = getattr(value, "close", None)
        if callable(close):
            close()
        raise HistoricalReadNotSupportedError()
    if isawaitable(value):
        return _await_as_of(cast(Awaitable[object], value), snapshot)
    return value


def _ensure_property_supported(
    getter: Callable[..., object], snapshot: datetime | None
) -> None:
    """Reject generator properties before constructing deferred bodies."""
    if snapshot is not None and (
        isgeneratorfunction(unwrap(getter)) or isasyncgenfunction(unwrap(getter))
    ):
        raise HistoricalReadNotSupportedError()


async def _await_as_of(value: Awaitable[object], snapshot: datetime) -> object:
    with as_of(snapshot):
        return _bind_property_result(await value, snapshot)


@contextmanager
def as_of(search_date: SearchDateInput) -> Iterator[datetime]:
    """Run an operation with a normalized historical date."""
    active = current_as_of_date()
    normalized = normalize_search_date(search_date)
    if active is not None and not _represents_same_instant(active, normalized):
        raise HistoricalContextConflictError
    if active is not None:
        yield normalized
        return

    token = _AS_OF_DATE.set(normalized)
    try:
        yield normalized
    finally:
        _AS_OF_DATE.reset(token)
