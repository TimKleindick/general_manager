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
    def __init__(self, value: Decimal | float | int | str, unit: str):
        """
        Create a Measurement with the specified numeric value and unit.

        Parameters:
            value: The numeric value to associate with the unit. Accepts Decimal, float, int, or a string convertible to Decimal.
            unit: The unit of measurement as a string.

        Raises:
            TypeError: If the value cannot be converted to a Decimal.
        """
        if not isinstance(value, (Decimal, float, int)):
            try:
                value = Decimal(str(value))
            except Exception:
                raise TypeError("Value must be a Decimal, float, int or compatible.")
        if not isinstance(value, Decimal):
            value = Decimal(str(value))
        self.__quantity = ureg.Quantity(self.formatDecimal(value), unit)

    def __getstate__(self):
        """
        Return a dictionary representing the serializable state of the measurement, including its magnitude and unit as strings.

        Returns:
            dict: Contains 'magnitude' and 'unit' keys for serialization purposes.
        """
        state = {
            "magnitude": str(self.magnitude),
            "unit": str(self.unit),
        }
        return state

    def __setstate__(self, state):
        """
        Restore the Measurement object from a serialized state.

        Parameters:
            state (dict): Dictionary with 'magnitude' (as a string) and 'unit' (as a string) representing the measurement.
        """
        value = Decimal(state["magnitude"])
        unit = state["unit"]
        self.__quantity = ureg.Quantity(self.formatDecimal(value), unit)

    @property
    def quantity(self) -> PlainQuantity:
        """
        Return the internal quantity as a `PlainQuantity` object from the `pint` library.
        """
        return self.__quantity

    @property
    def magnitude(self) -> Decimal:
        return self.__quantity.magnitude

    @property
    def unit(self) -> str:
        return str(self.__quantity.units)

    @classmethod
    def from_string(cls, value: str) -> Measurement:
        """
        Creates a Measurement instance from a string containing a numeric value and a unit.

        If the string contains only a value, it is treated as dimensionless. If the string contains both a value and a unit separated by a space, both are used to construct the Measurement. Raises ValueError if the format is invalid or the value cannot be parsed.

        Returns:
            Measurement: The constructed Measurement object.

        Raises:
            ValueError: If the string format is invalid or the value cannot be parsed as a number.
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
        value = value.normalize()
        if value == value.to_integral_value():
            try:
                return value.quantize(Decimal("1"))
            except InvalidOperation:
                return value
        else:
            return value

    def to(self, target_unit: str, exchange_rate: float | None = None):
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

    def is_currency(self):
        # Check if the unit is a defined currency
        """
        Return True if the measurement's unit is one of the defined currency units.
        """
        return str(self.unit) in currency_units

    def __add__(self, other: Any) -> Measurement:
        """
        Add this measurement to another, supporting both currency and physical units.

        Addition is permitted only if both operands are currencies of the same unit or both are physical units with compatible dimensions. Raises a TypeError if operands are of different types (currency vs. physical unit) or not Measurement instances, and raises a ValueError if units are incompatible.

        Returns:
            Measurement: A new Measurement representing the sum.
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
        Subtracts another Measurement from this one, enforcing unit compatibility.

        Subtraction is permitted only between two currency measurements of the same unit or two physical measurements with compatible dimensions. Raises a TypeError if the operand is not a Measurement or if subtracting between a currency and a physical unit. Raises a ValueError if subtracting different currencies or incompatible physical units.

        Returns:
            Measurement: A new Measurement representing the result of the subtraction.
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
        Returns the product of this measurement and another measurement or numeric value.

        Multiplication between two currency measurements is not permitted. When multiplied by another measurement, the result combines their units. When multiplied by a numeric value, the magnitude is scaled and the unit remains unchanged.

        Returns:
            Measurement: A new Measurement representing the result.

        Raises:
            TypeError: If multiplying two currency measurements or if the operand is neither a Measurement nor a numeric value.
        """
        if isinstance(other, Measurement):
            if self.is_currency() or other.is_currency():
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
        Divide this measurement by another measurement or a numeric value.

        If dividing by another `Measurement`:
          - Division between two *different* currencies is disallowed (raises TypeError).
          - Division between the *same* currency is allowed and yields a dimensionless result.
        Returns a new `Measurement` with the resulting value and unit.

        Raises:
            TypeError: If dividing two currency measurements with different units, or if the operand is not a `Measurement` or numeric value.
        Returns:
            Measurement: The result of the division as a new `Measurement` instance.
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

    def __str__(self):
        """
        Return a string representation of the measurement, including the unit unless it is dimensionless.
        """
        if not str(self.unit) == "dimensionless":
            return f"{self.magnitude} {self.unit}"
        return f"{self.magnitude}"

    def __repr__(self):
        """
        Return a string representation of the Measurement instance for debugging, showing its magnitude and unit.
        """
        return f"Measurement({self.magnitude}, '{self.unit}')"

    def _compare(self, other: Any, operation: Callable[..., bool]) -> bool:
        """
        Compares this Measurement to another using a specified comparison operation.

        If `other` is a string, it is parsed as a Measurement. The comparison is performed after converting `other` to this instance's unit. Raises a TypeError if `other` is not a Measurement or a valid string, and a ValueError if the measurements have incompatible dimensions.

        Parameters:
            other: The object to compare, either a Measurement or a string in the format "value unit".
            operation: A callable that takes two magnitudes and returns a boolean.

        Returns:
            bool: The result of the comparison.
        """
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
        Return True if this measurement is greater than or equal to another measurement or compatible value.

        The comparison converts the other operand to this measurement's unit before evaluating. Raises TypeError if the operand is not a Measurement or convertible string, or ValueError if units are incompatible.
        """
        return self._compare(other, ge)

    def __hash__(self) -> int:
        """
        Return a hash based on the measurement's magnitude and unit.

        Enables Measurement instances to be used in hash-based collections.
        """
        return hash((self.magnitude, str(self.unit)))
