"""Decimal normalization must retain context and representation semantics."""

import cProfile
import pickle
from decimal import (
    Context,
    Decimal,
    DecimalException,
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    ROUND_UP,
    getcontext,
    localcontext,
    setcontext,
)

import pytest

from general_manager.measurement import measurement as module
from tests.unit.test_measurement_construction import _reference_normalize


def _observe(normalize, value, configuration):
    precision, rounding, emin, emax, clamp, trapped = configuration
    with localcontext() as context:
        context.prec = precision
        context.rounding = rounding
        context.Emin = emin
        context.Emax = emax
        context.clamp = clamp
        for signal in context.traps:
            context.traps[signal] = trapped
        context.clear_flags()
        try:
            result = normalize(value)
            outcome = result.as_tuple(), str(result), repr(result)
        except DecimalException as error:
            outcome = type(error), str(error)
        return outcome, context.flags.copy()


@pytest.mark.parametrize(
    "value",
    [
        "0",
        "-0",
        "0.00",
        "-0.00",
        "0E+999999",
        "-0E-999999",
        "1",
        "-7",
        "100",
        "12.34",
        "12.3400",
        "1E+30",
        "1E-30",
        "123456789012345678901234567890",
        "12345678901234567890123456789.1",
        "0.0001",
        "1E-50",
        "1E+999999",
        "1E-999999",
        "1E-1000026",
        "NaN",
        "-NaN123",
        "sNaN456",
        "NaN" + "1" * 40,
        "sNaN" + "1" * 40,
        "Infinity",
        "-Infinity",
    ],
)
@pytest.mark.parametrize(
    "configuration",
    [
        (28, ROUND_HALF_EVEN, -999999, 999999, 0, True),
        (6, ROUND_DOWN, -999999, 999999, 0, False),
        (6, ROUND_UP, -3, 3, 0, True),
        (6, ROUND_DOWN, -3, 3, 0, False),
        (28, ROUND_HALF_EVEN, -3, 3, 1, True),
        (28, ROUND_UP, -3, 3, 1, False),
        (1, ROUND_DOWN, 0, 0, 0, True),
        (1, ROUND_UP, 0, 0, 1, False),
    ],
)
def test_normalization_matches_reference(value, configuration):
    value = Decimal(value)
    assert _observe(
        module.Measurement.format_decimal, value, configuration
    ) == _observe(_reference_normalize, value, configuration)


def test_canonical_measurements_avoid_local_decimal_context_allocations():
    values = [Decimal(value) for value in ("0", "-0", "100", "12.34")]
    profiler = cProfile.Profile()
    with profiler:
        results = [module.Measurement(value, "kg") for value in values]
    assert [result.magnitude.as_tuple() for result in results] == [
        value.as_tuple() for value in values
    ]
    context_calls = sum(
        entry.callcount
        for entry in profiler.getstats()
        if isinstance(entry.code, str) and "_decimal.localcontext" in entry.code
    )
    assert context_calls == 0


def test_decimal_subclass_hooks_run_inside_the_private_context():
    class ContextChangingDecimal(Decimal):
        def as_tuple(self):
            getcontext().prec = 2
            return super().as_tuple()

    with localcontext() as context:
        context.prec = 17
        value = ContextChangingDecimal("12.3400")
        assert (
            module.Measurement.format_decimal(value).as_tuple()
            == Decimal("12.34").as_tuple()
        )
        assert context.prec == 17


@pytest.mark.parametrize("text", ["0", "-0", "100", "12.34"])
def test_normalization_preserves_distinct_decimal_objects_in_pickles(text):
    value = Decimal(text)
    normalized = module.Measurement.format_decimal(value)
    expected = _reference_normalize(value)
    assert normalized.as_tuple() == expected.as_tuple()
    assert normalized is not value
    assert pickle.dumps((value, normalized)) == pickle.dumps((value, expected))


def test_context_subclass_attributes_are_not_read_before_copying():
    class CustomContext(Context):
        @property
        def clamp(self):
            message = "normalization must use the copied base Context"
            raise AssertionError(message)

    with localcontext():
        setcontext(CustomContext())
        value = Decimal("12.34")
        assert module.Measurement.format_decimal(value).as_tuple() == (
            _reference_normalize(value).as_tuple()
        )
