from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import cast
from unittest.mock import patch

import pytest

from general_manager.manager.input import _invoke_callable
from tests.perf.support import PerfBudgets

pytestmark = pytest.mark.perf

CALLBACK_FORMS = (
    "FUNCTION",
    "INSTANCE",
    "PARTIAL",
    "BOUND_METHOD",
    "DECORATED",
    "LAMBDA",
    "NONWEAK",
)


def _measure_callback_form(size: int, form: str) -> dict[str, int]:
    calls = 0
    callback: Callable[..., object]
    invocation_kwargs: dict[str, object]

    def add_call(value: int, offset: int = 1) -> int:
        nonlocal calls
        calls += 1
        return value + offset

    if form == "FUNCTION":
        callback = add_call
        invocation_kwargs = {"offset": 1}
    elif form == "INSTANCE":

        class InstanceCallback:
            def __call__(self, value: int, *, offset: int = 1) -> int:
                return add_call(value, offset)

        callback = InstanceCallback()
        invocation_kwargs = {"offset": 1}
    elif form == "PARTIAL":

        def add_partial(left: int, right: int, *, offset: int = 1) -> int:
            return add_call(left + right, offset)

        callback = functools.partial(add_partial, 0)
        invocation_kwargs = {"offset": 1}
    elif form == "BOUND_METHOD":

        class BoundCallback:
            def add(self, value: int, *, offset: int = 1) -> int:
                return add_call(value, offset)

        callback = BoundCallback().add
        invocation_kwargs = {"offset": 1}
    elif form == "DECORATED":
        callback_target: Callable[..., object] = add_call

        @functools.wraps(callback_target)
        def decorated_callback(*args: object, **kwargs: object) -> object:
            return callback_target(*args, **kwargs)

        callback = decorated_callback
        invocation_kwargs = {"offset": 1}
    elif form == "LAMBDA":
        callback = cast(
            Callable[..., object],
            {"callback": lambda value, *, offset=1: add_call(value, offset)}[
                "callback"
            ],
        )

        invocation_kwargs = {"offset": 1}
    elif form == "NONWEAK":

        class NonWeakCallback:
            __slots__ = ()

            def __call__(self, value: int) -> int:
                return add_call(value)

        callback = NonWeakCallback()
        invocation_kwargs = {}
    else:
        raise AssertionError

    original_signature = inspect.signature
    with patch(
        "general_manager.manager.input.inspect.signature",
        wraps=original_signature,
    ) as signature:
        result = [
            cast(
                int,
                _invoke_callable(callback, value, **invocation_kwargs),
            )
            for value in range(size)
        ]

    assert sum(result) == size * (size - 1) // 2 + size
    return {"INSPECTIONS": signature.call_count, "INVOCATIONS": calls}


@pytest.mark.parametrize("size", [100, 1_000, 10_000])
def test_callback_invocation_plan_observations_are_stable(
    size: int,
    perf_budgets: PerfBudgets,
) -> None:
    observations = {
        form: [_measure_callback_form(size, form) for _ in range(3)]
        for form in CALLBACK_FORMS
    }
    for form, runs in observations.items():
        assert runs[0] == runs[1] == runs[2]
        for name, value in runs[0].items():
            perf_budgets.assert_observation(f"INPUT_PLAN_{size}_{form}_{name}", value)
