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
ureg = pint.UnitRegistry(auto_reduce_dimensions=True)

# Define currency units
currency_units = ["EUR", "USD", "GBP", "JPY", "CHF", "AUD", "CAD"]
for currency in currency_units:
    # Define each currency as its own dimension
    ureg.define(f"{currency} = [{currency}]")


class Measurement:
    def __init__(self, value: Decimal | float | int | str, unit: str) -> None:
        """
        Create a measurement from a numeric value and a unit string.

        Parameters:
            value (Decimal | float | int | str): Numeric value, which will be coerced to `Decimal` if needed.
            unit (str): Unit name registered in the unit registry, including currencies and physical units.

        Returns:
            None

        Raises:
            ValueError: If the numeric value cannot be converted to `Decimal`.
        """
        if not isinstance(value, (Decimal, float, int)):
            try:
                value = Decimal(str(value))
            except Exception:
                raise ValueError("Value must be a Decimal, float, int or compatible.")
        if not isinstance(value, Decimal):
            value = Decimal(str(value))
        self.__quantity = ureg.Quantity(self.formatDecimal(value), unit)

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
        self.__quantity = ureg.Quantity(self.formatDecimal(value), unit)

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
        Create a measurement from a textual representation of magnitude and unit.

        Parameters:
            value (str): String formatted as `"<number> <unit>"`; a single numeric value is treated as dimensionless.

        Returns:
            Measurement: Measurement parsed from the provided string.

        Raises:
            ValueError: If the string lacks a unit, has too many tokens, or contains a non-numeric magnitude.
        """
        splitted = value.split(" ")
        if len(splitted) == 1:
            # If only one part, assume it's a dimensionless value
            try:
                return cls(Decimal(splitted[0]), "dimensionless")
            except InvalidOperation:
                raise ValueError("Invalid value for dimensionless measurement.")
        if len(splitted) != 2:
            raise ValueError("String must be in the format 'value unit'.")
        value, unit = splitted
        return cls(value, unit)

    @staticmethod
    def formatDecimal(value: Decimal) -> Decimal:
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
        Convert this measurement to a specified target unit, supporting both currency and physical unit conversions.

        For currency conversions between different currencies, an explicit exchange rate must be provided; if converting to the same currency, the original measurement is returned. For physical units, standard unit conversion is performed using the unit registry.

        Parameters:
            target_unit (str): The unit to convert to.
            exchange_rate (float, optional): Required for currency conversion between different currencies.

        Returns:
            Measurement: The converted measurement in the target unit.

        Raises:
            ValueError: If converting between different currencies without an exchange rate.
        """
        if self.is_currency():
            if self.unit == ureg(target_unit):
                return self  # Same currency, no conversion needed
            elif exchange_rate is not None:
                # Convert using the provided exchange rate
                value = self.magnitude * Decimal(str(exchange_rate))
                return Measurement(value, target_unit)
            else:
                raise ValueError(
                    "Conversion between currencies requires an exchange rate."
                )
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
        Add another measurement while enforcing currency and dimensional rules.

        Parameters:
            other (Any): Measurement or compatible value used as the addend.

        Returns:
            Measurement: Measurement representing the sum.

        Raises:
            TypeError: If the operand is not a measurement or mixes currency with non-currency units.
            ValueError: If the operands use incompatible currency codes or physical dimensions.
        """
        if not isinstance(other, Measurement):
            raise TypeError("Addition is only allowed between Measurement instances.")
        if self.is_currency() and other.is_currency():
            # Both are currencies
            if self.unit != other.unit:
                raise ValueError(
                    "Addition between different currencies is not allowed."
                )
            result_quantity = self.quantity + other.quantity
            if not isinstance(result_quantity, pint.Quantity):
                raise ValueError("Units are not compatible for addition.")
            return Measurement(
                Decimal(str(result_quantity.magnitude)), str(result_quantity.units)
            )
        elif not self.is_currency() and not other.is_currency():
            # Both are physical units
            if self.quantity.dimensionality != other.quantity.dimensionality:
                raise ValueError("Units are not compatible for addition.")
            result_quantity = self.quantity + other.quantity
            if not isinstance(result_quantity, pint.Quantity):
                raise ValueError("Units are not compatible for addition.")
            return Measurement(
                Decimal(str(result_quantity.magnitude)), str(result_quantity.units)
            )
        else:
            raise TypeError(
                "Addition between currency and physical unit is not allowed."
            )

    def __sub__(self, other: Any) -> Measurement:
        """
        Subtract another measurement while enforcing unit compatibility.

        Parameters:
            other (Any): Measurement or compatible value that should be subtracted.

        Returns:
            Measurement: Measurement representing the difference.

        Raises:
            TypeError: If the operand is not a measurement or mixes currency with non-currency units.
            ValueError: If the operands use incompatible currency codes or physical dimensions.
        """
        if not isinstance(other, Measurement):
            raise TypeError(
                "Subtraction is only allowed between Measurement instances."
            )
        if self.is_currency() and other.is_currency():
            # Both are currencies
            if self.unit != other.unit:
                raise ValueError(
                    "Subtraction between different currencies is not allowed."
                )
            result_quantity = self.quantity - other.quantity
            return Measurement(Decimal(str(result_quantity.magnitude)), str(self.unit))
        elif not self.is_currency() and not other.is_currency():
            # Both are physical units
            if self.quantity.dimensionality != other.quantity.dimensionality:
                raise ValueError("Units are not compatible for subtraction.")
            result_quantity = self.quantity - other.quantity
            return Measurement(Decimal(str(result_quantity.magnitude)), str(self.unit))
        else:
            raise TypeError(
                "Subtraction between currency and physical unit is not allowed."
            )

    def __mul__(self, other: Any) -> Measurement:
        """
        Multiply the measurement by another measurement or scalar.

        Parameters:
            other (Any): Measurement or numeric value used as the multiplier.

        Returns:
            Measurement: Product expressed as a measurement.

        Raises:
            TypeError: If multiplying two currency amounts or using an unsupported type.
        """
        if isinstance(other, Measurement):
            if self.is_currency() and other.is_currency():
                raise TypeError(
                    "Multiplication between two currency amounts is not allowed."
                )
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
            raise TypeError(
                "Multiplication is only allowed with Measurement or numeric values."
            )

    def __truediv__(self, other: Any) -> Measurement:
        """
        Divide the measurement by another measurement or scalar value.

        Parameters:
            other (Any): Measurement or numeric divisor.

        Returns:
            Measurement: Quotient expressed as a measurement.

        Raises:
            TypeError: If dividing currency amounts with different units or using an unsupported type.
        """
        if isinstance(other, Measurement):
            if self.is_currency() and other.is_currency() and self.unit != other.unit:
                raise TypeError(
                    "Division between two different currency amounts is not allowed."
                )
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
            raise TypeError(
                "Division is only allowed with Measurement or numeric values."
            )

    def __str__(self) -> str:
        """
        Format the measurement as a string, including the unit when present.

        Returns:
            str: Text representation of the magnitude and unit.
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
        Normalise operands into comparable measurements before applying a comparison.

        Parameters:
            other (Any): Measurement instance or string representation used for the comparison.
            operation (Callable[..., bool]): Callable that consumes two magnitudes and returns a comparison result.

        Returns:
            bool: Outcome of the supplied comparison function.

        Raises:
            TypeError: If `other` cannot be interpreted as a measurement.
            ValueError: If the operands use incompatible dimensions.
        """
        if other is None or other in ("", [], (), {}):
            return False
        if isinstance(other, str):
            other = Measurement.from_string(other)

        # Überprüfen, ob `other` ein Measurement-Objekt ist
        if not isinstance(other, Measurement):
            raise TypeError("Comparison is only allowed between Measurement instances.")
        try:
            # Convert `other` to the same units as `self`
            other_converted: pint.Quantity = other.quantity.to(self.unit)  # type: ignore
            # Apply the comparison operation
            return operation(self.magnitude, other_converted.magnitude)
        except pint.DimensionalityError:
            raise ValueError("Cannot compare measurements with different dimensions.")

    def __radd__(self, other: Any) -> Measurement:
        """
        Support sum() by treating zero as a neutral element.

        Parameters:
            other (Any): Left operand supplied by Python's arithmetic machinery.

        Returns:
            Measurement: Either `self` or the result of addition.
        """
        if other == 0:
            return self
        return self.__add__(other)

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
