"""Utility types and helpers for unit-aware measurements."""

# units.py
from __future__ import annotations
from collections.abc import Callable
from functools import lru_cache
from typing import TypeAlias, TypeGuard, cast
import pint
from decimal import Decimal, getcontext, InvalidOperation
from operator import eq, ne, lt, le, gt, ge
from pint.facets.plain import PlainQuantity

NumericMagnitude: TypeAlias = Decimal | float | int | str
NumericScalar: TypeAlias = Decimal | float | int
QuantityMagnitude: TypeAlias = Decimal | float
MeasurementQuantity: TypeAlias = PlainQuantity[QuantityMagnitude]
ComparisonOperation: TypeAlias = Callable[[QuantityMagnitude, QuantityMagnitude], bool]

# Set precision for Decimal
getcontext().prec = 28
_PERCENT_SCALE = Decimal("100")

# Create a new UnitRegistry
ureg: pint.UnitRegistry[QuantityMagnitude] = pint.UnitRegistry(
    auto_reduce_dimensions=True
)

# Define currency units
currency_units = ["EUR", "USD", "GBP", "JPY", "CHF", "AUD", "CAD"]
for currency in currency_units:
    # Define each currency as its own dimension
    ureg.define(f"{currency} = [{currency}]")


@lru_cache(maxsize=512)
def _parse_unit(unit: str) -> pint.Unit:
    """Parse one Pint unit expression, reusing the immutable parsed unit."""

    return ureg.parse_units(unit)


@lru_cache(maxsize=512)
def _canonical_unit_string(unit: str) -> str:
    """Return Pint's canonical unit string for one unit expression."""

    return str(_parse_unit(unit))


def _format_decimal(value: Decimal) -> Decimal:
    """
    Normalise decimals so integers have no fractional component.

    Parameters:
        value (Decimal): Decimal value that should be normalised.

    Returns:
        Decimal: Normalised decimal with insignificant trailing zeros removed.
    """
    value = value.normalize()
    if value == value.to_integral_value():
        try:
            return value.quantize(Decimal("1"))
        except InvalidOperation:
            return value
    return value


def _decimal_from_magnitude(value: NumericMagnitude) -> Decimal:
    """Convert a numeric magnitude into the canonical Decimal representation."""

    if isinstance(value, Decimal):
        return _format_decimal(value)
    return _format_decimal(Decimal(str(value)))


def _is_numeric_scalar(value: object) -> TypeGuard[NumericScalar]:
    """Return whether a value is a supported numeric scalar, excluding bool."""

    return not isinstance(value, bool) and isinstance(value, (Decimal, float, int))


@lru_cache(maxsize=256)
def _unit_uses_offset_for_unit_string(unit: str) -> bool:
    """Return whether a unit string has offset conversion semantics."""

    for unit_name, power in _pint_unit_components(unit):
        if power != 1:
            continue
        if _pint_unit_component_uses_offset(unit_name):
            return True
    return False


def _pint_unit_components(unit: str) -> tuple[tuple[str, object], ...]:
    """Return Pint unit component names and powers for one unit expression."""

    parsed_unit = _parse_unit(unit)
    return tuple(parsed_unit._units.items())


def _pint_unit_component_uses_offset(unit_name: str) -> bool:
    """Return whether a Pint unit component has offset conversion semantics."""

    converter = ureg._units[unit_name].converter
    return getattr(converter, "offset", None) is not None


def _unit_uses_offset(unit: str | pint.Unit | MeasurementQuantity) -> bool:
    """Return whether a Pint unit has offset conversion semantics."""

    unit_string = str(unit.units if isinstance(unit, PlainQuantity) else unit)
    return _unit_uses_offset_for_unit_string(unit_string)


_unit_uses_offset.cache_clear = _unit_uses_offset_for_unit_string.cache_clear  # type: ignore[attr-defined]
_unit_uses_offset.cache_info = _unit_uses_offset_for_unit_string.cache_info  # type: ignore[attr-defined]


@lru_cache(maxsize=512)
def _scalar_arithmetic_preserves_unit(unit: str) -> bool:
    """Return whether multiplying/dividing by a scalar keeps the canonical unit."""

    if _unit_uses_offset_for_unit_string(unit):
        return False
    if unit == "dimensionless":
        return True
    return bool(_parse_unit(unit).dimensionality)


def _exact_currency_per_unit_product(
    left_unit: str,
    right_unit: str,
) -> str | None:
    """Return the currency unit for exact ``currency / unit * unit`` products."""

    right_parsed = _parse_unit(right_unit)
    if len(right_parsed._units) != 1:
        return None
    for currency in currency_units:
        prefix = f"{currency} / "
        if left_unit.startswith(prefix):
            left_denominator = left_unit[len(prefix) :]
            if _parse_unit(left_denominator) == right_parsed:
                return currency
    return None


def _quantity_as_float(quantity: MeasurementQuantity) -> PlainQuantity[float]:
    """Rebuild a quantity with a float magnitude so offset-unit math stays in Pint."""

    return ureg.Quantity(float(quantity.magnitude), quantity.units)


def _prepare_quantities_for_binary_operation(
    *quantities: MeasurementQuantity,
) -> tuple[MeasurementQuantity, ...]:
    """
    Coerce quantities to float-backed Pint instances when any operand uses offset units.
    """

    if any(_unit_uses_offset(quantity) for quantity in quantities):
        return tuple(
            cast(MeasurementQuantity, _quantity_as_float(quantity))
            for quantity in quantities
        )
    return quantities


def _build_quantity(value: NumericMagnitude, unit: str) -> MeasurementQuantity:
    """Build a Pint quantity while routing offset units through float magnitudes."""

    decimal_value = _decimal_from_magnitude(value)
    quantity_value: QuantityMagnitude = decimal_value
    if _unit_uses_offset(unit):
        quantity_value = float(decimal_value)
    return cast(MeasurementQuantity, ureg.Quantity(quantity_value, _parse_unit(unit)))


def _convert_quantity(
    quantity: MeasurementQuantity, target_unit: str
) -> MeasurementQuantity:
    """Convert a quantity, coercing offset-unit paths to float-backed quantities."""

    source_quantity = quantity
    if _unit_uses_offset(quantity) or _unit_uses_offset(target_unit):
        source_quantity = cast(MeasurementQuantity, _quantity_as_float(quantity))
    return source_quantity.to(target_unit)


def _currency_component(unit: str) -> tuple[str, int] | None:
    """Return the single configured currency component in a unit expression."""

    parsed_unit = ureg.parse_units(str(unit))
    currency_components: list[tuple[str, int]] = []
    for unit_name, power in parsed_unit._units.items():
        currency_power = _integer_unit_power(power)
        if currency_power is not None and unit_name in currency_units:
            currency_components.append((unit_name, currency_power))
    if len(currency_components) != 1:
        return None
    return currency_components[0]


def _integer_unit_power(power: object) -> int | None:
    """Return an integer power only when the parsed unit power is integral."""

    if isinstance(power, bool) or isinstance(power, complex):
        return None
    if isinstance(power, int):
        return power
    if isinstance(power, float) and power.is_integer():
        return int(power)
    return None


def _unit_without_currency(unit: str, currency: str, power: int) -> str:
    """Return a unit expression with one currency component removed."""

    stripped_unit = ureg.parse_units(str(unit)) / (ureg.parse_units(currency) ** power)
    return str(stripped_unit)


def convert_magnitude(value: Decimal, source_unit: str, target_unit: str) -> Decimal:
    """
    Convert a magnitude between units while keeping offset-unit math away from Decimal.

    Pint's non-multiplicative conversions use float offsets internally, so absolute
    temperatures like ``degC`` and ``degF`` cannot be converted when the magnitude
    stays as ``Decimal``. For those conversions, convert through ``float`` and then
    round-trip back to ``Decimal`` via ``str``.
    """

    converted_quantity = _convert_quantity(
        _build_quantity(value, source_unit), target_unit
    )
    return _decimal_from_magnitude(converted_quantity.magnitude)


HASH_DECIMAL_QUANTUM = Decimal("1e-9")


def _compare_magnitudes(
    left: Decimal,
    right: Decimal,
    operation: ComparisonOperation,
    *,
    tolerant: bool,
) -> bool:
    """Compare magnitudes, quantizing offset-unit paths to match hashing."""

    if not tolerant:
        return operation(left, right)

    return operation(_hash_decimal(left), _hash_decimal(right))


def _hash_decimal(value: NumericMagnitude) -> Decimal:
    """Return the Decimal representation used for equality-compatible hashing."""

    decimal_value = _decimal_from_magnitude(value)
    try:
        return decimal_value.quantize(HASH_DECIMAL_QUANTUM)
    except InvalidOperation:
        return decimal_value


class InvalidMeasurementInitializationError(ValueError):
    """Raised when a measurement cannot be constructed from the provided value."""

    def __init__(self) -> None:
        """
        Exception raised when a Measurement cannot be constructed from the provided value.

        This error indicates the initializer received a value that is not a Decimal, float, int, or otherwise compatible numeric type suitable for constructing a Measurement.
        """
        super().__init__("Value must be a Decimal, float, int or compatible.")


class InvalidDimensionlessValueError(ValueError):
    """Raised when parsing a dimensionless measurement with an invalid value."""

    def __init__(self) -> None:
        """
        Initialize the exception indicating an invalid or malformed dimensionless measurement value.

        The exception carries a default message: "Invalid value for dimensionless measurement."
        """
        super().__init__("Invalid value for dimensionless measurement.")


class InvalidMeasurementStringError(ValueError):
    """Raised when a measurement string is not in the expected format."""

    def __init__(self) -> None:
        """
        Exception raised when a measurement string is not in the expected "<value> <unit>" format.

        Initializes the exception with the default message: "String must be in the format 'value unit'."
        """
        super().__init__("String must be in the format 'value unit'.")


class MissingExchangeRateError(ValueError):
    """Raised when a currency conversion lacks a required exchange rate."""

    def __init__(self) -> None:
        """
        Exception raised when a currency-to-currency conversion is attempted without an exchange rate.

        This exception indicates that an explicit exchange rate is required to convert between two different currency units.
        """
        super().__init__("Conversion between currencies requires an exchange rate.")


class MeasurementOperandTypeError(TypeError):
    """Raised when arithmetic operations receive non-measurement operands."""

    def __init__(self, operation: str) -> None:
        """
        Create an exception indicating an arithmetic operation was attempted with a non-Measurement operand.

        Parameters:
            operation (str): The name of the operation (e.g., '+', '-', '*', '/') used to format the exception message.
        """
        super().__init__(f"{operation} is only allowed between Measurement instances.")


class CurrencyMismatchError(ValueError):
    """Raised when performing arithmetic between mismatched currencies."""

    def __init__(self, operation: str) -> None:
        """
        Initialize the exception with a message describing the attempted currency operation that is disallowed.

        Parameters:
            operation (str): Name of the attempted operation (e.g., "add", "divide") used to construct the error message.
        """
        super().__init__(f"{operation} between different currencies is not allowed.")


class IncompatibleUnitsError(ValueError):
    """Raised when operations involve incompatible physical units."""

    def __init__(self, operation: str) -> None:
        """
        Initialize the exception indicating that two units are incompatible for a given operation.

        Parameters:
            operation (str): Name or description of the operation that failed due to incompatible units (e.g., 'addition', 'comparison').
        """
        super().__init__(f"Units are not compatible for {operation}.")


class MixedUnitOperationError(TypeError):
    """Raised when mixing currency and physical units in arithmetic."""

    def __init__(self, operation: str) -> None:
        """
        Create a MixedUnitOperationError indicating an attempted operation mixing currency and physical units.

        Parameters:
            operation (str): The name of the attempted operation (e.g., "addition", "multiplication"); used to build the exception message.
        """
        super().__init__(
            f"{operation} between currency and physical unit is not allowed."
        )


class CurrencyScalarOperationError(TypeError):
    """Raised when multiplication/division uses unsupported currency operands."""

    def __init__(self, operation: str) -> None:
        """
        Exception raised when attempting an arithmetic operation between two currency amounts that is not allowed.

        Parameters:
            operation (str): The name of the attempted operation (e.g., "multiplication", "division"); used to compose the exception message.
        """
        super().__init__(f"{operation} between two currency amounts is not allowed.")


class MeasurementScalarTypeError(TypeError):
    """Raised when operations expect a measurement or numeric operand."""

    def __init__(self, operation: str) -> None:
        """
        Initialize the exception indicating an invalid operand type for the specified operation.

        Parameters:
            operation (str): Name of the operation that only accepts Measurement or numeric operands; used to construct the exception message.
        """
        super().__init__(
            f"{operation} is only allowed with Measurement or numeric values."
        )


class UnsupportedComparisonError(TypeError):
    """Raised when comparing measurements with non-measurement types."""

    def __init__(self) -> None:
        """
        Initialize the exception with a fixed message indicating comparisons require Measurement instances.

        This constructor sets the exception's message to "Comparison is only allowed between Measurement instances."
        """
        super().__init__("Comparison is only allowed between Measurement instances.")


class IncomparableMeasurementError(ValueError):
    """Raised when measurements of different dimensions are compared."""

    def __init__(self) -> None:
        """
        Raised when attempting to compare two measurements whose units belong to different physical dimensions (for example, length vs mass), indicating they are not comparable.
        """
        super().__init__("Cannot compare measurements with different dimensions.")


class Measurement:
    """
    Decimal-backed measurement value with Pint unit conversion and arithmetic.

    A ``Measurement`` stores a magnitude and unit, exposes the underlying Pint
    quantity for advanced integrations, and supports compatible arithmetic,
    comparison, string parsing, pickling, and explicit currency conversion.
    Currency conversions never use implicit rates; callers must pass an
    ``exchange_rate`` when converting between different configured currencies.
    Pint canonicalizes unit names, so ``unit`` and string/repr output may use
    canonical names such as ``"kilogram"`` rather than the caller's spelling.
    Exact Pint exception subclasses/messages, canonical compound-unit ordering,
    aliases, and offset-unit internals are delegated to the installed Pint
    version and are not a stable GeneralManager API contract.
    """

    __quantity: MeasurementQuantity | None
    __magnitude: Decimal
    __unit: str
    __quantity_exposed: bool

    def __init__(self, value: NumericMagnitude, unit: str) -> None:
        """
        Create a Measurement from a numeric value and a unit label.

        Converts the provided numeric-like value to a Decimal through
        ``Decimal(str(value))`` and constructs the internal quantity using the
        given Pint unit expression. ``unit`` may be ``"dimensionless"`` or an
        empty string for dimensionless measurements. Invalid units are reported
        by Pint and are not wrapped by this constructor. String values are
        numeric magnitudes only; use ``from_string()`` for combined
        ``"<value> <unit>"`` text. ``bool`` is rejected even though it is an
        ``int`` subclass in Python.
        Invalid numeric strings, including combined measurement text such as
        ``"1 kg"`` passed directly as ``value``, raise
        ``InvalidMeasurementInitializationError``.
        Decimal special values such as ``NaN`` and infinities are accepted or
        rejected according to Decimal and Pint quantity construction; later
        operations inherit that delegated behavior.

        Parameters:
            value (Decimal | float | int | str): Numeric value to use as the measurement magnitude; strings and numeric types are coerced to Decimal.
            unit (str): Unit label registered in the module's unit registry,
                including configured currency codes, physical unit names,
                compound Pint expressions such as ``"kg / m^2"``, or
                dimensionless units.

        Raises:
            InvalidMeasurementInitializationError: If `value` cannot be converted to a Decimal or is a bool.
            pint.errors.PintError: If `unit` is not parseable by Pint.
        """
        if isinstance(value, bool):
            raise InvalidMeasurementInitializationError()
        if not isinstance(value, (Decimal, float, int)):
            try:
                value = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError) as error:
                raise InvalidMeasurementInitializationError() from error
        if not isinstance(value, Decimal):
            try:
                value = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError) as error:
                raise InvalidMeasurementInitializationError() from error
        self.__set_quantity(_build_quantity(value, unit), _canonical_unit_string(unit))

    @classmethod
    def _from_canonical_parts(
        cls,
        value: NumericMagnitude,
        unit: str,
    ) -> Measurement:
        """Build a measurement from already-canonical non-offset unit metadata."""

        measurement = cls.__new__(cls)
        decimal_value = _decimal_from_magnitude(value)
        measurement.__quantity = None
        measurement.__magnitude = decimal_value
        measurement.__unit = unit
        measurement.__quantity_exposed = False
        return measurement

    @classmethod
    def _from_quantity(cls, quantity: MeasurementQuantity) -> Measurement:
        """Build a measurement from a Pint operation result without reparsing it."""

        measurement = cls.__new__(cls)
        measurement.__set_quantity(quantity)
        return measurement

    def __set_quantity(
        self,
        quantity: MeasurementQuantity,
        unit: str | None = None,
    ) -> None:
        """Store quantity and cached public scalar values for internal reads."""

        self.__quantity = quantity
        self.__magnitude = _decimal_from_magnitude(quantity.magnitude)
        self.__unit = unit if unit is not None else str(quantity.units)
        self.__quantity_exposed = False

    def __current_quantity(self) -> MeasurementQuantity:
        quantity = self.__quantity
        if quantity is None:
            quantity = cast(
                MeasurementQuantity,
                ureg.Quantity(self.__magnitude, _parse_unit(self.__unit)),
            )
            self.__quantity = quantity
        return quantity

    def __current_magnitude(self) -> Decimal:
        if self.__quantity_exposed:
            return _decimal_from_magnitude(self.__current_quantity().magnitude)
        return self.__magnitude

    def __current_unit(self) -> str:
        if self.__quantity_exposed:
            return str(self.__current_quantity().units)
        return self.__unit

    def __getstate__(self) -> dict[str, str]:
        """
        Produce a serialisable representation of the measurement.

        Returns:
            dict[str, str]: Mapping with `magnitude` and `unit` entries for pickling.
        """
        state = {
            "magnitude": str(self.magnitude),
            "unit": str(self.unit),
        }
        return state

    def __setstate__(self, state: dict[str, str]) -> None:
        """
        Recreate the internal quantity from a serialized representation.

        Parameters:
            state (dict[str, str]): Serialized state containing `magnitude` and `unit` values.

        Returns:
            None
        """
        value = Decimal(state["magnitude"])
        unit = state["unit"]
        self.__set_quantity(_build_quantity(value, unit), _canonical_unit_string(unit))

    @property
    def quantity(self) -> MeasurementQuantity:
        """
        Access the underlying pint quantity for advanced operations.

        Magnitudes are usually Decimal-backed. Offset units such as absolute
        temperatures may be float-backed because Pint performs those conversions
        with non-multiplicative offsets.

        Returns:
            PlainQuantity: Pint quantity representing the measurement value and unit.
        """
        quantity = self.__current_quantity()
        self.__quantity_exposed = True
        return quantity

    @property
    def magnitude(self) -> Decimal:
        """
        Fetch the numeric component of the measurement.

        This property always returns a ``Decimal`` by converting the underlying
        Pint magnitude with ``Decimal(str(quantity.magnitude))``. For
        float-backed offset quantities, that preserves Pint's string
        representation rather than the original binary float.

        Returns:
            Decimal: Magnitude of the measurement in its current unit.
        """
        return self.__current_magnitude()

    @property
    def unit(self) -> str:
        """
        Retrieve the unit label associated with the measurement.

        The returned value is Pint's canonical unit string, not necessarily the
        spelling passed to the constructor. Empty-string and ``"dimensionless"``
        inputs both expose ``"dimensionless"``.

        Returns:
            str: Canonical unit string as provided by the unit registry.
        """
        return self.__current_unit()

    @classmethod
    def from_string(cls, value: str) -> Measurement:
        """
        Parse a textual representation into a Measurement.

        Leading and trailing whitespace is ignored. A single token is parsed as
        a dimensionless Decimal value. Two-token input is split once on
        whitespace; the first token must be a Decimal-compatible magnitude and
        the remainder is passed to Pint as the unit expression, so spaced
        compound units such as ``"1 g / cm^3"`` are supported. Decimal parsing
        accepts the syntax accepted by ``Decimal(...)``; currency symbols such
        as ``$`` are not accepted unless Pint knows them as unit text.

        Parameters:
            value (str): A string in the form "<number> <unit>" or a single numeric token for a dimensionless value.

        Returns:
            Measurement: Measurement constructed from the parsed magnitude and unit.

        Raises:
            InvalidDimensionlessValueError: If a single-token input cannot be parsed as a number.
            InvalidMeasurementStringError: If the string does not contain a valid numeric magnitude followed by a parseable unit expression.

        Notes:
            Empty strings raise ``InvalidMeasurementStringError``. Single-token
            inputs with invalid Decimal syntax raise
            ``InvalidDimensionlessValueError``. Inputs containing whitespace are
            treated as magnitude-plus-unit text; invalid magnitudes or invalid
            Pint unit expressions are reported as
            ``InvalidMeasurementStringError``.
        """
        stripped_value = value.strip()
        if not stripped_value:
            raise InvalidMeasurementStringError()

        splitted = stripped_value.split(maxsplit=1)
        if len(splitted) == 1:
            # If only one part, assume it's a dimensionless value
            magnitude = splitted[0]
            try:
                return cls(Decimal(magnitude), "dimensionless")
            except InvalidOperation as error:
                raise InvalidDimensionlessValueError() from error
        magnitude, unit = splitted
        try:
            return cls(magnitude, unit.strip())
        except (pint.errors.PintError, ValueError) as error:
            raise InvalidMeasurementStringError() from error

    @staticmethod
    def format_decimal(value: Decimal) -> Decimal:
        """
        Normalise decimals for measurement storage and display.

        This public helper exposes the same Decimal normalization used by
        measurement magnitude access and string formatting.

        The value is passed through ``Decimal.normalize()``. If the normalized
        value is integral, it is quantized to ``Decimal("1")`` so values such as
        ``Decimal("2.0")`` display and compare as ``Decimal("2")``. Non-integral
        values keep their significant fractional digits; no rounding is applied.
        Special Decimal values such as ``NaN`` and infinities are delegated to
        Decimal's own ``normalize()`` and ``to_integral_value()`` behavior; if
        integral quantization raises ``InvalidOperation``, the normalized value
        is returned unchanged. Signed zero follows Decimal normalization. The
        exact string form of Decimal special values is the standard-library
        Decimal result, not a separate GeneralManager guarantee.

        Parameters:
            value (Decimal): Decimal value that should be normalised.

        Returns:
            Decimal: Normalised decimal with insignificant trailing zeros removed.
        """
        return _format_decimal(value)

    def to(
        self,
        target_unit: str,
        exchange_rate: float | None = None,
    ) -> Measurement:
        """
        Convert this measurement to the specified target unit, handling currency conversions when applicable.

        Physical and same-currency conversions are delegated to Pint. For
        different currencies, ``exchange_rate`` means target units per one
        source unit: ``Measurement(100, "EUR").to("USD", exchange_rate=1.1)``
        returns ``110 USD``. Compound currency expressions keep the same rule
        for the currency component and still convert compatible physical
        components. Currency-to-currency conversion is only special-cased when
        each unit expression contains exactly one configured currency component;
        expressions with zero, multiple, inverse, or mismatched-power currency
        components fall back to Pint conversion and may raise Pint errors.
        Components with explicit power one, such as ``EUR ** 1``, are treated as
        the same currency component; expressions such as ``EUR / EUR`` simplify
        before this check and therefore are not currency conversions.
        Fractional currency powers, such as ``EUR ** 0.5``, are not handled by
        the exchange-rate shortcut and fall back to Pint conversion.
        Equivalent simplification beyond these rules follows Pint's parsed unit
        container; GeneralManager classifies the expression after Pint
        simplification and only inspects the resulting configured currency
        components. For example, if Pint simplifies ``EUR * meter / meter`` to a
        pure ``EUR`` unit, GeneralManager treats it as one currency component.
        GeneralManager does not guarantee exact Pint exception subclasses or
        messages for invalid units, incompatible conversions, or malformed
        target expressions.

        Parameters:
            target_unit (str): Unit label or currency code to convert the measurement into.
            exchange_rate (float | None): Exchange rate to use when converting between different currencies; ignored for same-currency conversions and physical-unit conversions.

        Returns:
            Measurement: The measurement expressed in the target unit.

        Raises:
            MissingExchangeRateError: If converting between two different currencies without providing an exchange rate.
            pint.errors.PintError: If `target_unit` is invalid or incompatible
                with the source unit.
        """
        source_currency = _currency_component(self.unit)
        target_currency = _currency_component(target_unit)
        if (
            source_currency is not None
            and target_currency is not None
            and source_currency[0] != target_currency[0]
        ):
            if exchange_rate is None:
                raise MissingExchangeRateError()
            source_currency_name, source_currency_power = source_currency
            target_currency_name, target_currency_power = target_currency
            if source_currency_power == target_currency_power:
                value = convert_magnitude(
                    self.magnitude,
                    _unit_without_currency(
                        self.unit, source_currency_name, source_currency_power
                    ),
                    _unit_without_currency(
                        target_unit, target_currency_name, target_currency_power
                    ),
                )
                value *= Decimal(str(exchange_rate)) ** source_currency_power
                return Measurement(value, target_unit)

        if self.is_currency():
            if str(self.unit) == str(target_unit):
                return self  # Same currency, no conversion needed
            elif exchange_rate is not None:
                # Convert using the provided exchange rate
                value = self.magnitude * Decimal(str(exchange_rate))
                return Measurement(value, target_unit)
            else:
                raise MissingExchangeRateError()
        else:
            # Standard conversion for physical units
            value = convert_magnitude(self.magnitude, self.unit, target_unit)
            return Measurement(value, target_unit)

    def is_currency(self) -> bool:
        """
        Determine whether the measurement's unit represents a configured currency.

        Currency matching is case-sensitive and checks the canonical unit string
        against the module-level ``currency_units`` list. Compound units such as
        ``"EUR / kilogram"`` are not considered pure currencies by this helper.
        The configured codes are ``EUR``, ``USD``, ``GBP``, ``JPY``, ``CHF``,
        ``AUD``, and ``CAD``; aliases are not registered. Percent and
        dimensionless units are treated as ordinary Pint units.
        The registry defines each currency as its own dimension named after the
        currency code, for example ``EUR = [EUR]``.

        Returns:
            bool: True if the unit matches one of the registered currency codes.
        """
        return self.unit in currency_units

    def __add__(self, other: object) -> Measurement:
        """
        Return the sum of this Measurement and another Measurement while enforcing currency and dimensional rules.

        If both operands are currency units their currency codes must match. If both are physical units their dimensionalities must match. Mixing currency and physical units is not permitted.

        Parameters:
            other (Measurement): The addend measurement.

        Returns:
            Measurement: A new Measurement representing the sum.

        Raises:
            MeasurementOperandTypeError: If `other` is not a Measurement.
            CurrencyMismatchError: If both operands are currencies with different currency codes.
            IncompatibleUnitsError: If both operands are physical units but have different dimensionalities or the result cannot be represented as a pint.Quantity.
            MixedUnitOperationError: If one operand is a currency and the other is a physical unit.
        """
        if not isinstance(other, Measurement):
            raise MeasurementOperandTypeError("Addition")
        left_unit = self.__current_unit()
        right_unit = other.__current_unit()
        if left_unit == right_unit and not _unit_uses_offset_for_unit_string(left_unit):
            return self._from_canonical_parts(
                self.__current_magnitude() + other.__current_magnitude(),
                left_unit,
            )
        if self.is_currency() and other.is_currency():
            # Both are currencies
            if self.unit != other.unit:
                raise CurrencyMismatchError("Addition")
            result_quantity = self.__current_quantity() + other.__current_quantity()
            if not isinstance(result_quantity, pint.Quantity):
                raise IncompatibleUnitsError("addition")
            return Measurement(
                Decimal(str(result_quantity.magnitude)), str(result_quantity.units)
            )
        elif not self.is_currency() and not other.is_currency():
            # Both are physical units
            left_quantity = self.__current_quantity()
            right_quantity = other.__current_quantity()
            if left_quantity.dimensionality != right_quantity.dimensionality:
                raise IncompatibleUnitsError("addition")
            left_quantity, right_quantity = _prepare_quantities_for_binary_operation(
                left_quantity,
                right_quantity,
            )
            result_quantity = left_quantity + right_quantity
            if not isinstance(result_quantity, pint.Quantity):
                raise IncompatibleUnitsError("addition")
            return Measurement(
                _decimal_from_magnitude(result_quantity.magnitude),
                str(result_quantity.units),
            )
        else:
            raise MixedUnitOperationError("Addition")

    def __sub__(self, other: object) -> Measurement:
        """
        Subtract another Measurement from this one, enforcing currency and unit compatibility.

        Performs subtraction for two currency Measurements only when they share the same currency code, or for two physical Measurements only when they have the same dimensionality; mixing currency and physical units is disallowed.

        Parameters:
            other (Measurement): The measurement to subtract from this measurement.

        Returns:
            Measurement: A new Measurement representing the difference.

        Raises:
            MeasurementOperandTypeError: If `other` is not a Measurement.
            CurrencyMismatchError: If both operands are currencies but use different currency codes.
            IncompatibleUnitsError: If both operands are physical units but have incompatible dimensionality.
            MixedUnitOperationError: If one operand is a currency and the other is a physical unit.
        """
        if not isinstance(other, Measurement):
            raise MeasurementOperandTypeError("Subtraction")
        left_unit = self.__current_unit()
        right_unit = other.__current_unit()
        if left_unit == right_unit and not _unit_uses_offset_for_unit_string(left_unit):
            return self._from_canonical_parts(
                self.__current_magnitude() - other.__current_magnitude(),
                left_unit,
            )
        if self.is_currency() and other.is_currency():
            # Both are currencies
            if self.unit != other.unit:
                raise CurrencyMismatchError("Subtraction")
            result_quantity = self.__current_quantity() - other.__current_quantity()
            return Measurement(Decimal(str(result_quantity.magnitude)), str(self.unit))
        elif not self.is_currency() and not other.is_currency():
            # Both are physical units
            left_quantity = self.__current_quantity()
            right_quantity = other.__current_quantity()
            if left_quantity.dimensionality != right_quantity.dimensionality:
                raise IncompatibleUnitsError("subtraction")
            left_quantity, right_quantity = _prepare_quantities_for_binary_operation(
                left_quantity,
                right_quantity,
            )
            result_quantity = left_quantity - right_quantity
            return Measurement(
                _decimal_from_magnitude(result_quantity.magnitude),
                str(result_quantity.units),
            )
        else:
            raise MixedUnitOperationError("Subtraction")

    def __mul__(self, other: object) -> Measurement:
        """
        Multiply this measurement by another measurement or by a numeric scalar.

        Multiplication combines units through Pint. Two pure currency
        measurements are rejected because the result would be a currency-squared
        amount. Currency multiplied by a non-currency measurement, including
        percent or dimensionless measurements, is allowed and produces the
        corresponding compound or simplified unit. Pint treats percent as
        ``0.01 dimensionless`` during conversion, but multiplication preserves
        the compound unit until callers convert it, e.g. ``100 EUR * 20 percent``
        renders as ``2000 EUR * percent`` and converts to ``20 EUR``.

        Parameters:
            other (Measurement | Decimal | float | int): The multiplier. When a Measurement is provided, units are combined according to unit algebra; when a numeric scalar is provided, the magnitude is scaled and the unit is preserved. Bool is not accepted as a scalar.

        Returns:
            Measurement: The product as a Measurement with the resulting magnitude and unit.

        Raises:
            CurrencyScalarOperationError: If both operands are currency measurements (multiplying two currencies is not allowed).
            MeasurementScalarTypeError: If `other` is not a Measurement or a supported numeric type, including bool.
        """
        if isinstance(other, Measurement):
            if self.is_currency() and other.is_currency():
                raise CurrencyScalarOperationError("Multiplication")
            left_unit = self.__current_unit()
            right_unit = other.__current_unit()
            result_currency = _exact_currency_per_unit_product(left_unit, right_unit)
            if result_currency is not None:
                return self._from_canonical_parts(
                    self.__current_magnitude() * other.__current_magnitude(),
                    result_currency,
                )
            result_currency = _exact_currency_per_unit_product(right_unit, left_unit)
            if result_currency is not None:
                return self._from_canonical_parts(
                    self.__current_magnitude() * other.__current_magnitude(),
                    result_currency,
                )
            left_quantity, right_quantity = _prepare_quantities_for_binary_operation(
                self.__current_quantity(),
                other.__current_quantity(),
            )
            result_quantity = left_quantity * right_quantity
            return self._from_quantity(cast(MeasurementQuantity, result_quantity))
        elif _is_numeric_scalar(other):
            if not isinstance(other, Decimal):
                other = Decimal(str(other))
            unit = self.__current_unit()
            if unit == "percent":
                return self._from_canonical_parts(
                    self.__current_magnitude() * other / _PERCENT_SCALE,
                    "dimensionless",
                )
            if _scalar_arithmetic_preserves_unit(unit):
                return self._from_canonical_parts(
                    self.__current_magnitude() * other,
                    unit,
                )
            quantity = self.__current_quantity()
            scalar: QuantityMagnitude = other
            if _unit_uses_offset(quantity):
                quantity = cast(MeasurementQuantity, _quantity_as_float(quantity))
                scalar = float(other)
            result_quantity = quantity * scalar
            return Measurement(
                _decimal_from_magnitude(result_quantity.magnitude),
                str(result_quantity.units),
            )
        else:
            raise MeasurementScalarTypeError("Multiplication")

    def __truediv__(self, other: object) -> Measurement:
        """
        Divide this measurement by another measurement or by a numeric scalar.

        Division combines units through Pint. Dividing two measurements with the
        same currency produces a ratio with Pint's derived units, normally
        dimensionless for pure currency values. Dividing different currencies is
        rejected unless callers explicitly convert first with ``to()``.

        Parameters:
            other (Measurement | Decimal | float | int): The divisor; when a Measurement, must be compatible (currencies require same unit). Bool is not accepted as a scalar.

        Returns:
            Measurement: The quotient as a new Measurement. If `other` is a Measurement the result carries the derived units; if `other` is a scalar the result retains this measurement's unit.

        Raises:
            CurrencyMismatchError: If both operands are currencies with different units.
            MeasurementScalarTypeError: If `other` is not a Measurement or a numeric type, including bool.
            ZeroDivisionError: If dividing by a zero scalar or zero-magnitude measurement.
        """
        if isinstance(other, Measurement):
            if self.is_currency() and other.is_currency() and self.unit != other.unit:
                raise CurrencyMismatchError("Division")
            left_quantity, right_quantity = _prepare_quantities_for_binary_operation(
                self.__current_quantity(),
                other.__current_quantity(),
            )
            result_quantity = left_quantity / right_quantity
            return self._from_quantity(cast(MeasurementQuantity, result_quantity))
        elif _is_numeric_scalar(other):
            if not isinstance(other, Decimal):
                other = Decimal(str(other))
            unit = self.__current_unit()
            if unit == "percent":
                return self._from_canonical_parts(
                    self.__current_magnitude() / other / _PERCENT_SCALE,
                    "dimensionless",
                )
            if _scalar_arithmetic_preserves_unit(unit):
                return self._from_canonical_parts(
                    self.__current_magnitude() / other,
                    unit,
                )
            quantity = self.__current_quantity()
            scalar: QuantityMagnitude = other
            if _unit_uses_offset(quantity):
                quantity = cast(MeasurementQuantity, _quantity_as_float(quantity))
                scalar = float(other)
            result_quantity = quantity / scalar
            return Measurement(
                _decimal_from_magnitude(result_quantity.magnitude),
                str(result_quantity.units),
            )
        else:
            raise MeasurementScalarTypeError("Division")

    def __str__(self) -> str:
        """
        Return a human-readable string of the measurement, including its unit when not dimensionless.

        The magnitude is read through the ``magnitude`` property, so output uses
        the same Decimal normalization as other public magnitude access.

        Returns:
            A string formatted as "<magnitude> <unit>" for measurements with a unit, or as "<magnitude>" for dimensionless measurements.
        """
        if not str(self.unit) == "dimensionless":
            return f"{self.magnitude} {self.unit}"
        return f"{self.magnitude}"

    def __repr__(self) -> str:
        """
        Return a detailed representation suitable for debugging.

        The magnitude is read through the ``magnitude`` property, and the unit
        is Pint's canonical unit string. The GeneralManager-owned shape is
        ``Measurement(<magnitude>, '<canonical unit>')``; exact magnitude and
        compound-unit spelling follow the documented Decimal/Pint boundaries.

        Returns:
            str: Debug-friendly notation including magnitude and unit.
        """
        return f"Measurement({self.magnitude}, '{self.unit}')"

    def _compare(self, other: object, operation: ComparisonOperation) -> bool:
        """
        Compare this measurement to another value by normalizing both to the same unit and applying a comparison operation.

        ``None`` and values equal to ``""``, ``[]``, ``()``, or ``{}`` return
        ``False`` for every comparison operation, including equality and
        inequality, before string parsing occurs. Therefore ``measurement == ""``
        returns ``False`` rather than raising. The check uses Python equality, so
        non-string custom objects that compare equal to those literal empty
        values also take this path. Other non-empty strings are parsed with
        ``from_string()``, so invalid strings propagate
        ``InvalidMeasurementStringError`` or ``InvalidDimensionlessValueError``.
        Other falsey values such as ``0`` and ``False`` are unsupported operands
        and raise ``UnsupportedComparisonError``.
        Offset-unit comparisons quantize normalized magnitudes to ``1e-9`` so
        equality matches hashing. Non-offset comparisons use exact Decimal
        comparison after conversion. Which units Pint treats as offset units
        and which operations require float-backed quantities are delegated to
        Pint.

        Parameters:
            other (object): A Measurement instance or a string parseable by Measurement.from_string; empty or null-like values return False.
            operation (ComparisonOperation): Callable receiving the normalized left and right magnitudes.

        Returns:
            bool: Result of applying `operation` to the two magnitudes; `False` for empty/null-like `other`.

        Raises:
            UnsupportedComparisonError: If `other` cannot be interpreted as a Measurement.
            IncomparableMeasurementError: If the two measurements have incompatible dimensions and cannot be converted to the same unit.
        """
        if other is None or other in ("", [], (), {}):
            return False
        if isinstance(other, str):
            other = Measurement.from_string(other)

        if not isinstance(other, Measurement):
            raise UnsupportedComparisonError()
        try:
            self_quantity, other_quantity = _prepare_quantities_for_binary_operation(
                self.__current_quantity(),
                other.__current_quantity(),
            )
            other_converted = _convert_quantity(
                other_quantity, str(self_quantity.units)
            )
            left = _decimal_from_magnitude(self_quantity.magnitude)
            right = _decimal_from_magnitude(other_converted.magnitude)
            return _compare_magnitudes(
                left,
                right,
                operation,
                tolerant=_unit_uses_offset(self_quantity)
                or _unit_uses_offset(other_converted),
            )
        except pint.DimensionalityError as error:
            raise IncomparableMeasurementError() from error

    def __radd__(self, other: object) -> Measurement:
        """
        Allow right-side addition so sum() treats 0 as the neutral element.

        Nonzero left operands delegate to ``__add__`` and therefore raise
        ``MeasurementOperandTypeError`` unless they are ``Measurement`` instances.

        Parameters:
            other (object): Left operand supplied by Python's arithmetic machinery; typically numeric zero when used with sum(). Bool is not treated as zero.

        Returns:
            Measurement: `self` if `other` is 0, otherwise the result of adding `other` to `self`.
        """
        if _is_numeric_scalar(other) and other == 0:
            return self
        return self.__add__(other)

    def __rsub__(self, other: object) -> Measurement:
        """
        Support right-side subtraction.

        Numeric zero returns the negated measurement. Nonzero non-Measurement
        left operands raise ``MeasurementOperandTypeError``.

        Parameters:
            other (object): Left operand supplied by Python's arithmetic machinery; numeric zero produces a negated measurement. Bool is not treated as zero.

        Returns:
            Measurement: Result of subtracting `self` from `other`.

        Raises:
            MeasurementOperandTypeError: If `other` is neither 0 nor a Measurement instance.
        """
        if _is_numeric_scalar(other) and other == 0:
            return self * -1
        if not isinstance(other, Measurement):
            raise MeasurementOperandTypeError("Subtraction")
        return other.__sub__(self)

    def __rmul__(self, other: object) -> Measurement:
        """
        Support right-side multiplication.

        Parameters:
            other (object): Left operand supplied by Python's arithmetic machinery.

        Returns:
            Measurement: Result of multiplying `other` by `self`.
        """
        return self.__mul__(other)

    def __rtruediv__(self, other: object) -> Measurement:
        """
        Support right-side division.

        Reverse division follows Python's reflected arithmetic boundary:
        unsupported left operands raise ``MeasurementOperandTypeError``. Forward
        division by unsupported non-measurement scalar-like operands raises
        ``MeasurementScalarTypeError`` instead. Bool is not accepted as a scalar.
        When the left operand is a ``Measurement``, the method returns
        ``other.__truediv__(self)`` so all forward division currency, unit, and
        zero-divisor rules apply with the operands reversed.

        Parameters:
            other (object): Left operand supplied by Python's arithmetic machinery.

        Returns:
            Measurement: Result of dividing `other` by `self`.

        Raises:
            MeasurementOperandTypeError: If `other` is not a Measurement instance or numeric scalar.
            ZeroDivisionError: If this measurement has zero magnitude.
        """
        if _is_numeric_scalar(other):
            if not isinstance(other, Decimal):
                other = Decimal(str(other))
            quantity = self.__current_quantity()
            scalar: QuantityMagnitude = other
            if _unit_uses_offset(quantity):
                quantity = cast(MeasurementQuantity, _quantity_as_float(quantity))
                scalar = float(other)
            result_quantity = scalar / quantity
            return Measurement(
                _decimal_from_magnitude(result_quantity.magnitude),
                str(result_quantity.units),
            )

        if not isinstance(other, Measurement):
            raise MeasurementOperandTypeError("Division")
        return other.__truediv__(self)

    # Comparison Operators
    def __eq__(self, other: object) -> bool:
        """
        Return whether this measurement equals another compatible measurement.

        Args:
            other: ``Measurement`` or string accepted by ``from_string``.

        Returns:
            ``True`` when magnitudes are equal after unit normalization. The
            null-like values handled by ``_compare()`` return ``False``.

        Raises:
            UnsupportedComparisonError: If ``other`` cannot be interpreted as a measurement.
            IncomparableMeasurementError: If units are incompatible.
        """
        return self._compare(other, eq)

    def __ne__(self, other: object) -> bool:
        """
        Return whether this measurement differs from another compatible measurement.

        This uses the same comparison path as ``__eq__`` with the ``!=``
        operation; it is not implemented as ``not __eq__(other)``. The documented
        null-like values therefore return ``False`` rather than the inverse of
        equality.

        Args:
            other: ``Measurement`` or string accepted by ``from_string``.

        Returns:
            ``True`` when magnitudes differ after unit normalization. The
            null-like values handled by ``_compare()`` return ``False`` rather
            than ``True``.

        Raises:
            UnsupportedComparisonError: If ``other`` cannot be interpreted as a measurement.
            IncomparableMeasurementError: If units are incompatible.
        """
        return self._compare(other, ne)

    def __lt__(self, other: object) -> bool:
        """
        Return whether this measurement is less than another compatible measurement.

        Args:
            other: ``Measurement`` or string accepted by ``from_string``.

        Returns:
            ``True`` when this magnitude is smaller after unit normalization.

        Raises:
            UnsupportedComparisonError: If ``other`` cannot be interpreted as a measurement.
            IncomparableMeasurementError: If units are incompatible.
        """
        return self._compare(other, lt)

    def __le__(self, other: object) -> bool:
        """
        Return whether this measurement is less than or equal to another measurement.

        Args:
            other: ``Measurement`` or string accepted by ``from_string``.

        Returns:
            ``True`` when this magnitude is smaller or equal after unit normalization.

        Raises:
            UnsupportedComparisonError: If ``other`` cannot be interpreted as a measurement.
            IncomparableMeasurementError: If units are incompatible.
        """
        return self._compare(other, le)

    def __gt__(self, other: object) -> bool:
        """
        Return whether this measurement is greater than another compatible measurement.

        Args:
            other: ``Measurement`` or string accepted by ``from_string``.

        Returns:
            ``True`` when this magnitude is larger after unit normalization.

        Raises:
            UnsupportedComparisonError: If ``other`` cannot be interpreted as a measurement.
            IncomparableMeasurementError: If units are incompatible.
        """
        return self._compare(other, gt)

    def __ge__(self, other: object) -> bool:
        """
        Check whether the measurement is greater than or equal to another value.

        Parameters:
            other (object): Measurement or compatible representation used in the comparison.

        Returns:
            bool: True when the measurement is greater than or equal to `other`.

        Raises:
            TypeError: If `other` cannot be interpreted as a measurement.
            ValueError: If units are incompatible.
        """
        return self._compare(other, ge)

    def __hash__(self) -> int:
        """
        Compute a hash using the measurement's canonical base magnitude and unit.

        Hashing mirrors equality by converting to base units first. The base
        magnitude is normalized to a Decimal and quantized to ``1e-9``,
        matching offset-unit equality. Hash collisions remain possible, as with
        any Python hash. Non-offset equality uses exact Decimal comparison after
        conversion; quantization may introduce extra hash collisions but does
        not make unequal measurements compare equal.
        "Equivalent" means measurements for which the comparison methods report
        equality; additional Pint simplifications only matter when they affect
        that equality result.
        For Decimal special values and Pint base-unit conversion failures,
        hashing follows the same Decimal/Pint behavior described above.

        Returns:
            int: Stable hash suitable for use in dictionaries and sets. Measurements
            that compare equal after unit conversion produce the same hash.
        """
        quantity = self.__current_quantity()
        if _unit_uses_offset(quantity):
            quantity = cast(MeasurementQuantity, _quantity_as_float(quantity))
        base_quantity = quantity.to_base_units()
        return hash((_hash_decimal(base_quantity.magnitude), str(base_quantity.units)))
