"""Operation-scoped historical date context."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime, time

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

    def __init__(self) -> None:
        super().__init__("Mutations are not allowed in historical context.")


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
