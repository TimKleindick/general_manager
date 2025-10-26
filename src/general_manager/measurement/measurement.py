"""Utility types and helpers for unit-aware measurements."""

# units.py
from __future__ import annotations
from typing import Any, Callable
import pint
from decimal import Decimal, getcontext, InvalidOperation
from operator import eq, ne, lt, le, gt, ge
from pint.facets.plain import PlainQuantity

# Set precision for Decimal
getcontext().prec = 28

# Create a new UnitRegistry
ureg = pint.UnitRegistry(auto_reduce_dimensions=True)  # type: ignore

# Define currency units
currency_units = ["EUR", "USD", "GBP", "JPY", "CHF", "AUD", "CAD"]
for currency in currency_units:
    # Define each currency as its own dimension
    ureg.define(f"{currency} = [{currency}]")


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
    def __init__(self, value: Decimal | float | int | str, unit: str) -> None:
        """
        Create a Measurement from a numeric value and a unit label.

        Converts the provided numeric-like value to a Decimal and constructs the internal quantity using the given unit.

        Parameters:
            value (Decimal | float | int | str): Numeric value to use as the measurement magnitude; strings and numeric types are coerced to Decimal.
            unit (str): Unit label registered in the module's unit registry (currency codes or physical unit names).

        Raises:
            InvalidMeasurementInitializationError: If `value` cannot be converted to a Decimal.
        """
        if not isinstance(value, (Decimal, float, int)):
            try:
                value = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError) as error:
                raise InvalidMeasurementInitializationError() from error
        if not isinstance(value, Decimal):
            value = Decimal(str(value))
        self.__quantity = ureg.Quantity(self.format_decimal(value), unit)

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
        self.__quantity = ureg.Quantity(self.format_decimal(value), unit)

    @property
    def quantity(self) -> PlainQuantity:
        """
        Access the underlying pint quantity for advanced operations.

        Returns:
            PlainQuantity: Pint quantity representing the measurement value and unit.
        """
        return self.__quantity

    @property
    def magnitude(self) -> Decimal:
        """
        Fetch the numeric component of the measurement.

        Returns:
            Decimal: Magnitude of the measurement in its current unit.
        """
        return self.__quantity.magnitude

    @property
    def unit(self) -> str:
        """
        Retrieve the unit label associated with the measurement.

        Returns:
            str: Canonical unit string as provided by the unit registry.
        """
        return str(self.__quantity.units)

    @classmethod
    def from_string(cls, value: str) -> Measurement:
        """
        Parse a textual representation into a Measurement.

        Parameters:
            value (str): A string in the form "<number> <unit>" or a single numeric token for a dimensionless value.

        Returns:
            Measurement: Measurement constructed from the parsed magnitude and unit.

        Raises:
            InvalidDimensionlessValueError: If a single-token input cannot be parsed as a number.
            InvalidMeasurementStringError: If the string does not contain exactly one or two space-separated tokens.
            InvalidMeasurementInitializationError: If constructing the Measurement from the parsed parts fails.
        """
        splitted = value.split(" ")
        if len(splitted) == 1:
            # If only one part, assume it's a dimensionless value
            try:
                return cls(Decimal(splitted[0]), "dimensionless")
            except InvalidOperation as error:
                raise InvalidDimensionlessValueError() from error
        if len(splitted) != 2:
            raise InvalidMeasurementStringError()
        value, unit = splitted
        return cls(value, unit)

    @staticmethod
    def format_decimal(value: Decimal) -> Decimal:
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
        else:
            return value

    def to(
        self,
        target_unit: str,
        exchange_rate: float | None = None,
    ) -> Measurement:
        """
        Convert this measurement to the specified target unit, handling currency conversions when applicable.

        Parameters:
            target_unit (str): Unit label or currency code to convert the measurement into.
            exchange_rate (float | None): Exchange rate to use when converting between different currencies; ignored for same-currency conversions and physical-unit conversions.

        Returns:
            Measurement: The measurement expressed in the target unit.

        Raises:
            MissingExchangeRateError: If converting between two different currencies without providing an exchange rate.
        """
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
            converted_quantity: pint.Quantity = self.quantity.to(target_unit)  # type: ignore
            value = Decimal(str(converted_quantity.magnitude))
            unit = str(converted_quantity.units)
            return Measurement(value, unit)

    def is_currency(self) -> bool:
        """
        Determine whether the measurement's unit represents a configured currency.

        Returns:
            bool: True if the unit matches one of the registered currency codes.
        """
        return str(self.unit) in currency_units

    def __add__(self, other: Any) -> Measurement:
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
        if self.is_currency() and other.is_currency():
            # Both are currencies
            if self.unit != other.unit:
                raise CurrencyMismatchError("Addition")
            result_quantity = self.quantity + other.quantity
            if not isinstance(result_quantity, pint.Quantity):
                raise IncompatibleUnitsError("addition")
            return Measurement(
                Decimal(str(result_quantity.magnitude)), str(result_quantity.units)
            )
        elif not self.is_currency() and not other.is_currency():
            # Both are physical units
            if self.quantity.dimensionality != other.quantity.dimensionality:
                raise IncompatibleUnitsError("addition")
            result_quantity = self.quantity + other.quantity
            if not isinstance(result_quantity, pint.Quantity):
                raise IncompatibleUnitsError("addition")
            return Measurement(
                Decimal(str(result_quantity.magnitude)), str(result_quantity.units)
            )
        else:
            raise MixedUnitOperationError("Addition")

    def __sub__(self, other: Any) -> Measurement:
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
        if self.is_currency() and other.is_currency():
            # Both are currencies
            if self.unit != other.unit:
                raise CurrencyMismatchError("Subtraction")
            result_quantity = self.quantity - other.quantity
            return Measurement(Decimal(str(result_quantity.magnitude)), str(self.unit))
        elif not self.is_currency() and not other.is_currency():
            # Both are physical units
            if self.quantity.dimensionality != other.quantity.dimensionality:
                raise IncompatibleUnitsError("subtraction")
            result_quantity = self.quantity - other.quantity
            return Measurement(Decimal(str(result_quantity.magnitude)), str(self.unit))
        else:
            raise MixedUnitOperationError("Subtraction")

    def __mul__(self, other: Any) -> Measurement:
        """
        Multiply this measurement by another measurement or by a numeric scalar.

        Parameters:
            other (Measurement | Decimal | float | int): The multiplier. When a Measurement is provided, units are combined according to unit algebra; when a numeric scalar is provided, the magnitude is scaled and the unit is preserved.

        Returns:
            Measurement: The product as a Measurement with the resulting magnitude and unit.

        Raises:
            CurrencyScalarOperationError: If both operands are currency measurements (multiplying two currencies is not allowed).
            MeasurementScalarTypeError: If `other` is not a Measurement or a supported numeric type.
        """
        if isinstance(other, Measurement):
            if self.is_currency() and other.is_currency():
                raise CurrencyScalarOperationError("Multiplication")
            result_quantity = self.quantity * other.quantity
            return Measurement(
                Decimal(str(result_quantity.magnitude)), str(result_quantity.units)
            )
        elif isinstance(other, (Decimal, float, int)):
            if not isinstance(other, Decimal):
                other = Decimal(str(other))
            result_quantity = self.quantity * other
            return Measurement(Decimal(str(result_quantity.magnitude)), str(self.unit))
        else:
            raise MeasurementScalarTypeError("Multiplication")

    def __truediv__(self, other: Any) -> Measurement:
        """
        Divide this measurement by another measurement or by a numeric scalar.

        Parameters:
            other (Measurement | Decimal | float | int): The divisor; when a Measurement, must be compatible (currencies require same unit).

        Returns:
            Measurement: The quotient as a new Measurement. If `other` is a Measurement the result carries the derived units; if `other` is a scalar the result retains this measurement's unit.

        Raises:
            CurrencyMismatchError: If both operands are currencies with different units.
            MeasurementScalarTypeError: If `other` is not a Measurement or a numeric type.
        """
        if isinstance(other, Measurement):
            if self.is_currency() and other.is_currency() and self.unit != other.unit:
                raise CurrencyMismatchError("Division")
            result_quantity = self.quantity / other.quantity
            return Measurement(
                Decimal(str(result_quantity.magnitude)), str(result_quantity.units)
            )
        elif isinstance(other, (Decimal, float, int)):
            if not isinstance(other, Decimal):
                other = Decimal(str(other))
            result_quantity = self.quantity / other
            return Measurement(Decimal(str(result_quantity.magnitude)), str(self.unit))
        else:
            raise MeasurementScalarTypeError("Division")

    def __str__(self) -> str:
        """
        Return a human-readable string of the measurement, including its unit when not dimensionless.

        Returns:
            A string formatted as "<magnitude> <unit>" for measurements with a unit, or as "<magnitude>" for dimensionless measurements.
        """
        if not str(self.unit) == "dimensionless":
            return f"{self.magnitude} {self.unit}"
        return f"{self.magnitude}"

    def __repr__(self) -> str:
        """
        Return a detailed representation suitable for debugging.

        Returns:
            str: Debug-friendly notation including magnitude and unit.
        """
        return f"Measurement({self.magnitude}, '{self.unit}')"

    def _compare(self, other: Any, operation: Callable[..., bool]) -> bool:
        """
        Compare this measurement to another value by normalizing both to the same unit and applying a comparison operation.

        Parameters:
            other (Any): A Measurement instance or a string parseable by Measurement.from_string; empty or null-like values return False.
            operation (Callable[..., bool]): A callable that accepts two magnitudes (self and other, after unit normalization) and returns a boolean result.

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
            other_converted: pint.Quantity = other.quantity.to(self.unit)  # type: ignore
            return operation(self.magnitude, other_converted.magnitude)
        except pint.DimensionalityError as error:
            raise IncomparableMeasurementError() from error

    def __radd__(self, other: Any) -> Measurement:
        """
        Allow right-side addition so sum() treats 0 as the neutral element.

        Parameters:
            other (Any): Left operand supplied by Python's arithmetic machinery; typically 0 when used with sum().

        Returns:
            Measurement: `self` if `other` is 0, otherwise the result of adding `other` to `self`.
        """
        if other == 0:
            return self
        return self.__add__(other)

    def __rsub__(self, other: Any) -> Measurement:
        """
        Support right-side subtraction.

        Parameters:
            other (Any): Left operand supplied by Python's arithmetic machinery; typically 0 when used with sum().

        Returns:
            Measurement: Result of subtracting `self` from `other`.

        Raises:
            TypeError: If `other` is not a Measurement instance.
        """
        if other == 0:
            return self * -1
        if not isinstance(other, Measurement):
            raise MeasurementOperandTypeError("Subtraction")
        return other.__sub__(self)

    def __rmul__(self, other: Any) -> Measurement:
        """
        Support right-side multiplication.

        Parameters:
            other (Any): Left operand supplied by Python's arithmetic machinery.

        Returns:
            Measurement: Result of multiplying `other` by `self`.
        """
        return self.__mul__(other)

    def __rtruediv__(self, other: Any) -> Measurement:
        """
        Support right-side division.

        Parameters:
            other (Any): Left operand supplied by Python's arithmetic machinery.

        Returns:
            Measurement: Result of dividing `other` by `self`.

        Raises:
            TypeError: If `other` is not a Measurement instance.
        """
        if isinstance(other, (Decimal, float, int)):
            if not isinstance(other, Decimal):
                other = Decimal(str(other))
            result_quantity = other / self.quantity
            return Measurement(
                Decimal(str(result_quantity.magnitude)), str(result_quantity.units)
            )

        if not isinstance(other, Measurement):
            raise MeasurementOperandTypeError("Division")
        return other.__truediv__(self)

    # Comparison Operators
    def __eq__(self, other: Any) -> bool:
        return self._compare(other, eq)

    def __ne__(self, other: Any) -> bool:
        return self._compare(other, ne)

    def __lt__(self, other: Any) -> bool:
        return self._compare(other, lt)

    def __le__(self, other: Any) -> bool:
        return self._compare(other, le)

    def __gt__(self, other: Any) -> bool:
        return self._compare(other, gt)

    def __ge__(self, other: Any) -> bool:
        """
        Check whether the measurement is greater than or equal to another value.

        Parameters:
            other (Any): Measurement or compatible representation used in the comparison.

        Returns:
            bool: True when the measurement is greater than or equal to `other`.

        Raises:
            TypeError: If `other` cannot be interpreted as a measurement.
            ValueError: If units are incompatible.
        """
        return self._compare(other, ge)

    def __hash__(self) -> int:
        """
        Compute a hash using the measurement's magnitude and unit.

        Returns:
            int: Stable hash suitable for use in dictionaries and sets.
        """
        return hash((self.magnitude, str(self.unit)))
