"""Preserve construction semantics while reducing repeated Pint/Decimal work."""

from concurrent.futures import ThreadPoolExecutor
from decimal import (
    Decimal,
    DecimalException,
    InvalidOperation,
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    ROUND_UP,
    localcontext,
)
import pickle
from tokenize import TokenError
from unittest.mock import patch

import numpy as np
import pint
import pytest

from general_manager.measurement import measurement as module

Measurement = module.Measurement


@pytest.mark.parametrize("unit", ["kg", "kg / m ** 3", "EUR", "count", ""])
@pytest.mark.parametrize("warm", [False, True])
def test_custom_formatter_preserves_eager_constructor_errors(unit, warm):
    original = module.ureg.formatter.default_format
    try:
        module.ureg.formatter.default_format = ""
        module._canonical_unit_string.cache_clear()
        if warm:
            _ = Measurement(1, unit).quantity
        module.ureg.formatter.default_format = "L"
        with pytest.raises(TokenError):
            Measurement(1, unit)
    finally:
        module.ureg.formatter.default_format = original
        module._canonical_unit_string.cache_clear()


@pytest.mark.parametrize("unit", ["kg", "kg / m ** 3", "EUR", "count", ""])
def test_formatter_reset_preserves_internal_units_despite_cached_public_label(unit):
    original = module.ureg.formatter.default_format
    try:
        module.ureg.formatter.default_format = "L"
        module._canonical_unit_string.cache_clear()
        module._default_quantity_unit.cache_clear()
        expected_label = module._canonical_unit_string(unit)
        module.ureg.formatter.default_format = ""
        value = Measurement(1, unit)
        assert value.unit == expected_label
        assert value.quantity == module.ureg.Quantity(Decimal(1), unit)
    finally:
        module.ureg.formatter.default_format = original
        module._canonical_unit_string.cache_clear()


@pytest.mark.parametrize("exponent", ["0.123456789", "1.23456789", "-0.123456789"])
def test_fractional_unit_exponents_keep_original_canonical_round_trip(exponent):
    source = f"cm ** {exponent}"
    canonical = str(module._parse_unit(source))
    value = Measurement(1, source)
    rounded = Measurement(1, canonical)
    assert value == rounded
    assert hash(value) == hash(rounded)


def test_normalization_reads_decimal_coefficient_once():
    class ObservedDecimal(Decimal):
        reads = 0

        def as_tuple(self):
            self.reads += 1
            return super().as_tuple()

    value = ObservedDecimal("123456789.0123456789012345678900")
    assert (
        Measurement.format_decimal(value).as_tuple()
        == Decimal("123456789.01234567890123456789").as_tuple()
    )
    assert value.reads == 1


def _reference_normalize(value):
    """The pre-optimization Decimal rule, including traps and exponent limits."""
    with localcontext() as context:
        context.prec = (
            max(28, len(value.as_tuple().digits)) if value.is_finite() else 28
        )
        normalized = value.normalize(context=context)
        if normalized == normalized.to_integral_value(context=context):
            try:
                return normalized.quantize(Decimal("1"), context=context)
            except InvalidOperation:
                return normalized
        return normalized


def _reference_construction(value, unit):
    """Evaluate the original constructor and defensive quantity-copy pipeline."""
    value = value if isinstance(value, Decimal) else Decimal(str(value))
    first = module.ureg.Quantity(_reference_normalize(value), module._parse_unit(unit))
    public_unit = module._canonical_unit_string(unit)
    magnitude = _reference_normalize(first.magnitude)
    stored = module.ureg.Quantity(
        _reference_normalize(magnitude), module._parse_unit(str(first.units))
    )
    exposed = module.ureg.Quantity(
        _reference_normalize(_reference_normalize(stored.magnitude)),
        module._parse_unit(str(stored.units)),
    )
    return (
        magnitude.as_tuple(),
        public_unit,
        exposed.magnitude.as_tuple(),
        str(exposed.units),
    )


def _actual_construction(value, unit):
    result = Measurement(value, unit)
    exposed = result.quantity
    return (
        result.magnitude.as_tuple(),
        result.unit,
        exposed.magnitude.as_tuple(),
        str(exposed.units),
    )


def _observe(callback, value, unit, configuration):
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
            outcome = callback(value, unit)
        except (DecimalException, pint.errors.PintError) as error:
            outcome = type(error), str(error)
        return outcome, context.flags.copy()


@pytest.mark.parametrize("unit", ["kg", "EUR/count", "degC", "percent", "m / km", ""])
@pytest.mark.parametrize(
    "value",
    [
        0,
        -7,
        1.25,
        "12.3400",
        np.int64(17),
        np.float64(2.125),
        Decimal("-0.00"),
        Decimal("0E+100"),
        Decimal("1e-50"),
        Decimal("123456789.0123456789012345678900"),
        Decimal("1E+50"),
        Decimal("1." + "0" * 99 + "e-50"),
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
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
    ],
)
def test_constructor_matches_original_decimal_and_pint_pipeline(
    value, unit, configuration
):
    assert _observe(_actual_construction, value, unit, configuration) == _observe(
        _reference_construction, value, unit, configuration
    )


def test_ordinary_construction_defers_pint_quantity_allocation():
    with patch.object(module.ureg, "Quantity", wraps=module.ureg.Quantity) as quantity:
        values = [Measurement(0, unit) for unit in ("EUR", "kg", "count", "degC")]
        assert [value.magnitude for value in values] == [Decimal(0)] * 4
        assert [value.unit for value in values] == [
            "EUR",
            "kilogram",
            "count",
            "degree_Celsius",
        ]
        quantity.assert_not_called()


def test_unpickling_defers_pint_quantity_allocation():
    payload = pickle.dumps(Measurement("12.3400", "kg"))
    with patch.object(module.ureg, "Quantity", wraps=module.ureg.Quantity) as quantity:
        restored = pickle.loads(payload)  # noqa: S301 - locally generated test payload
        assert restored.__getstate__() == {"magnitude": "12.34", "unit": "kilogram"}
        quantity.assert_not_called()


def test_repeated_zeros_and_exposed_quantities_are_independent():
    first = Measurement(0, "kg")
    second = Measurement(0, "kg")
    assert first is not second
    exposed = first.quantity
    exposed.ito("g")
    exposed += module.ureg.Quantity(500, "g")
    assert (
        first.__getstate__()
        == second.__getstate__()
        == {"magnitude": "0", "unit": "kilogram"}
    )
    assert first.quantity is not first.quantity
    assert first == second and hash(first) == hash(second)


def test_invalid_units_are_rejected_before_quantity_access():
    with pytest.raises(pint.errors.UndefinedUnitError):
        Measurement(0, "not_a_gm_unit")
    with pytest.raises(module.InvalidMeasurementInitializationError):
        Measurement("not a number", "not_a_gm_unit")
    with pytest.raises(module.InvalidMeasurementInitializationError):
        Measurement(True, "kg")
    with pytest.raises(module.InvalidMeasurementInitializationError):
        Measurement(None, "kg")


def test_quantity_access_keeps_current_context_error_behavior():
    value = Measurement(Decimal("1e50"), "kg")
    with localcontext() as context:
        context.Emax = 3
        with pytest.raises(DecimalException):
            _ = value.quantity
    assert value.magnitude == Decimal("1e50")


def test_shared_measurement_remains_isolated_across_thread_contexts():
    shared = Measurement("12.34", "kg")

    def calculate(index):
        with localcontext() as context:
            context.prec = 6 + index
            context.rounding = ROUND_DOWN if index % 2 else ROUND_UP
            created = Measurement("12.3400", "kg")
            exposed = shared.quantity
            exposed.ito("g")
            exposed += module.ureg.Quantity(1, "g")
            return created.__getstate__(), created.to("g").magnitude, hash(created)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(calculate, range(24)))
    assert all(result == results[0] for result in results)
    assert shared.__getstate__() == {"magnitude": "12.34", "unit": "kilogram"}
    assert shared.quantity.magnitude == Decimal("12.34")
