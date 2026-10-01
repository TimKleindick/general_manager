"""Guard integer-zero construction against Decimal and Pint regressions."""

from decimal import (
    Context,
    Decimal,
    DecimalException,
    ROUND_DOWN,
    ROUND_UP,
    getcontext,
    localcontext,
    setcontext,
)
import pickle
from tokenize import TokenError
from unittest.mock import patch

import pint

import pytest

from general_manager.measurement import measurement as module


class IntZero(int):
    def __str__(self):
        return "7.50"


class DecimalZero(Decimal):
    def normalize(self, context=None):
        return Decimal("3")


class MeasurementSubclass(module.Measurement):
    pass


def _snapshot(factory, value, unit, configuration, flagged):
    with localcontext(Context(**configuration)) as context:
        for signal in context.flags:
            context.flags[signal] = flagged
        before = context.flags.copy()
        try:
            result = factory(value, unit)
            state = result.__getstate__()
            magnitude = result.magnitude.as_tuple()
            quantity = result.quantity
            outcome = (
                state,
                magnitude,
                quantity.magnitude.as_tuple(),
                str(quantity.units),
                pickle.dumps(result),
            )
        except (
            DecimalException,
            pint.errors.PintError,
            TokenError,
            TypeError,
            ValueError,
            AttributeError,
            module.InvalidMeasurementInitializationError,
        ) as error:
            outcome = type(error), str(error)
        return outcome, before, context.flags.copy()


def _legacy_constructor(value, unit):
    # Keep the original constructor conversion order as a differential oracle.
    if isinstance(value, bool):
        raise module.InvalidMeasurementInitializationError()
    if not isinstance(value, (Decimal, float, int)):
        try:
            value = Decimal(str(value))
        except (module.InvalidOperation, TypeError, ValueError) as error:
            raise module.InvalidMeasurementInitializationError() from error
    if not isinstance(value, Decimal):
        try:
            value = Decimal(str(value))
        except (module.InvalidOperation, TypeError, ValueError) as error:
            raise module.InvalidMeasurementInitializationError() from error
    result = module.Measurement.__new__(module.Measurement)
    result._Measurement__set_value(value, unit)
    return result


@pytest.mark.parametrize("flagged", [False, True])
@pytest.mark.parametrize(
    "configuration",
    [
        {},
        {"prec": 1, "rounding": ROUND_DOWN},
        {"prec": 80, "rounding": ROUND_UP},
        {"Emin": 0, "Emax": 0},
        {"Emin": -1, "Emax": 1, "clamp": 1, "traps": []},
        {"Emin": -1, "Emax": 1, "clamp": 1, "traps": list(getcontext().flags)},
        {"prec": 1, "Emin": 0, "Emax": 0, "clamp": 1, "traps": []},
        {"traps": list(getcontext().flags)},
        {"traps": []},
    ],
)
@pytest.mark.parametrize(
    "unit", ["kg", "EUR/count", "degC", "", "not_a_gm_unit", None, 3, [], {}]
)
@pytest.mark.parametrize(
    "value",
    [
        0,
        False,
        IntZero(0),
        module.ureg.Quantity(0, "kg"),
        DecimalZero(0),
        Decimal("-0"),
        Decimal("0E+100"),
        Decimal("-0E-100"),
        "-0.00",
        -0.0,
        "0",
        "bad",
        7,
    ],
)
def test_zero_constructor_matches_legacy(value, unit, configuration, flagged):
    assert _snapshot(
        module.Measurement, value, unit, configuration, flagged
    ) == _snapshot(_legacy_constructor, value, unit, configuration, flagged)


@pytest.mark.parametrize("formatter", ["", "~", "L", "~P"])
@pytest.mark.parametrize("warm", [False, True])
@pytest.mark.parametrize("unit", ["kg", "count", "EUR / kg", "cm ** 0.123456789"])
def test_zero_preserves_formatter_roundtrip(formatter, warm, unit):
    previous = module.ureg.formatter.default_format
    try:
        outcomes = []
        for factory in (module.Measurement, _legacy_constructor):
            module.ureg.formatter.default_format = ""
            module._canonical_unit_string.cache_clear()
            module._default_quantity_unit.cache_clear()
            module._parse_unit.cache_clear()
            if warm:
                factory(0, unit)
            module.ureg.formatter.default_format = formatter
            outcomes.append(_snapshot(factory, 0, unit, {}, False))
        assert outcomes[0] == outcomes[1]
    finally:
        module.ureg.formatter.default_format = previous
        module._canonical_unit_string.cache_clear()
        module._default_quantity_unit.cache_clear()


@pytest.mark.parametrize("cls", [module.Measurement, MeasurementSubclass])
def test_zero_objects_and_pickle_memo_are_independent(cls):
    first, second = cls(0, "kg"), cls(0, "kg")
    assert first is not second
    assert first.magnitude is not second.magnitude
    payload = pickle.dumps((first, second, first.magnitude, second.magnitude))
    restored = pickle.loads(payload)  # noqa: S301 - locally generated payload
    assert type(restored[0]) is cls
    assert restored[0] is not restored[1]
    assert restored[0].magnitude is not restored[1].magnitude
    assert restored[2] is not restored[3]
    first.quantity.ito("g")
    assert first.__getstate__() == second.__getstate__()


def test_arithmetic_results_still_normalize_with_canonical_units():
    result = module.Measurement("1.25", "EUR") * 4
    assert result.magnitude.as_tuple() == Decimal(5).as_tuple()
    assert (
        module.Measurement("1.20", "kg") + module.Measurement("2.80", "kg")
    ).magnitude.as_tuple() == Decimal(4).as_tuple()


def test_context_subclass_preserves_original_path():
    class ObservedContext(Context):
        pass

    previous = getcontext()
    try:
        setcontext(ObservedContext(prec=1, Emin=0, Emax=0))
        assert type(getcontext()) is ObservedContext
        with patch.object(
            module, "_decimal_from_magnitude", wraps=module._decimal_from_magnitude
        ) as normalize:
            actual = module.Measurement(0, "kg")
            normalize.assert_called_once()
        expected = _legacy_constructor(0, "kg")
        assert actual.__getstate__() == expected.__getstate__()
        assert actual.magnitude.as_tuple() == expected.magnitude.as_tuple()
        assert not any(getcontext().flags.values())
    finally:
        setcontext(previous)
