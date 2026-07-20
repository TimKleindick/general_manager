from __future__ import annotations

import asyncio
import threading
from datetime import UTC, date, datetime, timedelta, tzinfo
from importlib import import_module

import pytest
from django.utils import timezone

as_of_module = import_module("general_manager.as_of")
normalize_search_date = as_of_module.normalize_search_date


class _BodyFailure(RuntimeError):
    def __init__(self) -> None:
        super().__init__("body failed")


class _BrokenTimezone(tzinfo):
    def utcoffset(self, value: datetime | None) -> timedelta | None:
        raise ValueError


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2022-01-01", datetime(2022, 1, 1, tzinfo=timezone.get_fixed_timezone(60))),
        (
            "2022-01-01T12:30:45",
            datetime(2022, 1, 1, 12, 30, 45, tzinfo=timezone.get_fixed_timezone(60)),
        ),
        ("2022-01-01T12:30:45Z", datetime(2022, 1, 1, 12, 30, 45, tzinfo=UTC)),
        (
            date(2022, 1, 1),
            datetime(2022, 1, 1, tzinfo=timezone.get_fixed_timezone(60)),
        ),
        (
            datetime(2022, 1, 1, 12, 30, 45),
            datetime(2022, 1, 1, 12, 30, 45, tzinfo=timezone.get_fixed_timezone(60)),
        ),
        (
            datetime(2022, 1, 1, 12, 30, 45, tzinfo=UTC),
            datetime(2022, 1, 1, 12, 30, 45, tzinfo=UTC),
        ),
    ],
)
def test_normalize_search_date_accepts_supported_values(
    value: str | date | datetime,
    expected: datetime,
) -> None:
    with timezone.override("Europe/Berlin"):
        result = normalize_search_date(value)

    assert result == expected
    assert timezone.is_aware(result)


@pytest.mark.parametrize("value", ["not-a-date", 42, object()])
def test_normalize_search_date_rejects_invalid_values(value: object) -> None:
    with pytest.raises(
        as_of_module.InvalidSearchDateError, match="Invalid search date"
    ) as error:
        normalize_search_date(value)  # type: ignore[arg-type]

    assert error.value.__cause__ is not None


def test_normalize_search_date_wraps_timezone_normalization_errors() -> None:
    value = datetime(2022, 1, 1, tzinfo=_BrokenTimezone())

    with pytest.raises(as_of_module.InvalidSearchDateError) as error:
        normalize_search_date(value)

    assert error.value.__cause__ is not None


def test_as_of_sets_and_restores_context() -> None:
    assert as_of_module.current_as_of_date() is None

    with as_of_module.as_of("2022-01-01") as normalized:
        assert as_of_module.current_as_of_date() == normalized

    assert as_of_module.current_as_of_date() is None


def test_as_of_accepts_search_date_keyword() -> None:
    with as_of_module.as_of(search_date="2022-01-01"):
        assert as_of_module.current_as_of_date() == normalize_search_date("2022-01-01")


def test_same_normalized_date_can_be_nested() -> None:
    with as_of_module.as_of("2022-01-01") as outer:
        with as_of_module.as_of(date(2022, 1, 1)) as inner:
            assert inner == outer
            assert as_of_module.current_as_of_date() == outer
        assert as_of_module.current_as_of_date() == outer


def test_conflicting_nested_date_preserves_outer_context() -> None:
    with as_of_module.as_of("2022-01-01") as outer:
        with pytest.raises(
            as_of_module.HistoricalContextConflictError, match="Conflicting"
        ):
            with as_of_module.as_of("2023-01-01"):
                pass
        assert as_of_module.current_as_of_date() == outer

    assert as_of_module.current_as_of_date() is None


def test_invalid_nested_date_preserves_outer_context() -> None:
    with as_of_module.as_of("2022-01-01") as outer:
        with pytest.raises(as_of_module.InvalidSearchDateError):
            with as_of_module.as_of("not-a-date"):
                pass
        assert as_of_module.current_as_of_date() == outer

    assert as_of_module.current_as_of_date() is None


def test_resolve_search_date_uses_explicit_or_context_date() -> None:
    assert as_of_module.resolve_search_date(None) is None
    explicit = as_of_module.resolve_search_date("2022-01-01")
    assert explicit == normalize_search_date("2022-01-01")
    assert as_of_module.current_as_of_date() is None

    with as_of_module.as_of("2022-01-01") as active:
        assert as_of_module.resolve_search_date(None) == active
        assert as_of_module.resolve_search_date(date(2022, 1, 1)) == active
        with pytest.raises(
            as_of_module.HistoricalContextConflictError, match="Conflicting"
        ):
            as_of_module.resolve_search_date("2023-01-01")
        assert as_of_module.current_as_of_date() == active


def test_as_of_restores_context_after_body_exception() -> None:
    with pytest.raises(_BodyFailure, match="body failed"):
        with as_of_module.as_of("2022-01-01"):
            raise _BodyFailure

    assert as_of_module.current_as_of_date() is None


def test_as_of_context_propagates_to_asyncio_tasks() -> None:
    async def read_context() -> datetime | None:
        return as_of_module.current_as_of_date()

    async def run() -> tuple[datetime, datetime | None]:
        with as_of_module.as_of("2022-01-01") as active:
            task_value = await asyncio.create_task(read_context())
        return active, task_value

    active, task_value = asyncio.run(run())
    assert task_value == active
    assert as_of_module.current_as_of_date() is None


def test_as_of_context_is_isolated_from_fresh_thread() -> None:
    thread_value: list[datetime | None] = []

    with as_of_module.as_of("2022-01-01") as active:
        thread = threading.Thread(
            target=lambda: thread_value.append(as_of_module.current_as_of_date())
        )
        thread.start()
        thread.join()

        assert as_of_module.current_as_of_date() == active

    assert thread_value == [None]


def test_historical_policy_errors_have_concise_messages() -> None:
    assert issubclass(as_of_module.HistoricalMutationError, RuntimeError)
    assert str(as_of_module.HistoricalMutationError()) == (
        "Mutations are not allowed in historical context."
    )
    assert issubclass(as_of_module.HistoricalReadNotSupportedError, RuntimeError)
    assert str(as_of_module.HistoricalReadNotSupportedError()) == (
        "This read does not support historical context."
    )


def test_as_of_api_is_available_from_stable_public_module() -> None:
    public_api = import_module("general_manager.api")
    expected_exports = {
        "as_of": as_of_module.as_of,
        "current_as_of_date": as_of_module.current_as_of_date,
        "InvalidSearchDateError": as_of_module.InvalidSearchDateError,
        "HistoricalContextConflictError": as_of_module.HistoricalContextConflictError,
        "HistoricalMutationError": as_of_module.HistoricalMutationError,
        "HistoricalReadNotSupportedError": as_of_module.HistoricalReadNotSupportedError,
    }

    for name, expected in expected_exports.items():
        assert getattr(public_api, name) is expected
