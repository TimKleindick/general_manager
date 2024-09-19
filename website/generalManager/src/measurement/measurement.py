# units.py

import pint
from decimal import Decimal, getcontext

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
    def __init__(self, value, unit):
        if not isinstance(value, (Decimal, float, int)):
            try:
                value = Decimal(str(value))
            except Exception:
                raise TypeError("Value must be a Decimal, float, int or compatible.")
        if not isinstance(value, Decimal):
            value = Decimal(str(value))
        self.__quantity = self.formatDecimal(value) * ureg.Quantity(1, unit)

    @property
    def quantity(self):
        return self.__quantity

    @staticmethod
    def formatDecimal(value):
        value = value.normalize()
        if value == value.to_integral():
            return value.quantize(Decimal("1"))
        else:
            return value

    def to(self, target_unit, exchange_rate=None):
        if self.is_currency():
            if self.quantity.units == ureg(target_unit):
                return self  # Same currency, no conversion needed
            elif exchange_rate is not None:
                # Convert using the provided exchange rate
                value = self.quantity.magnitude * Decimal(str(exchange_rate))
                return Measurement(value, target_unit)
            else:
                raise ValueError(
                    "Conversion between currencies requires an exchange rate."
                )
        else:
            # Standard conversion for physical units
            converted_quantity = self.quantity.to(target_unit)
            value = Decimal(str(converted_quantity.magnitude))
            unit = str(converted_quantity.units)
            return Measurement(value, unit)

    def is_currency(self):
        # Check if the unit is a defined currency
        return str(self.quantity.units) in currency_units

    def __add__(self, other):
        if not isinstance(other, Measurement):
            raise TypeError("Addition is only allowed between Measurement instances.")
        if self.is_currency() and other.is_currency():
            # Both are currencies
            if self.quantity.units != other.quantity.units:
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

    def __sub__(self, other):
        if not isinstance(other, Measurement):
            raise TypeError(
                "Subtraction is only allowed between Measurement instances."
            )
        if self.is_currency() and other.is_currency():
            # Both are currencies
            if self.quantity.units != other.quantity.units:
                raise ValueError(
                    "Subtraction between different currencies is not allowed."
                )
            result_quantity = self.quantity - other.quantity
            return Measurement(
                Decimal(str(result_quantity.magnitude)), str(self.quantity.units)
            )
        elif not self.is_currency() and not other.is_currency():
            # Both are physical units
            if self.quantity.dimensionality != other.quantity.dimensionality:
                raise ValueError("Units are not compatible for subtraction.")
            result_quantity = self.quantity - other.quantity
            return Measurement(
                Decimal(str(result_quantity.magnitude)), str(self.quantity.units)
            )
        else:
            raise TypeError(
                "Subtraction between currency and physical unit is not allowed."
            )

    def __mul__(self, other):
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
            return Measurement(
                Decimal(str(result_quantity.magnitude)), str(self.quantity.units)
            )
        else:
            raise TypeError(
                "Multiplication is only allowed with Measurement or numeric values."
            )

    def __truediv__(self, other):
        if isinstance(other, Measurement):
            if self.is_currency() and other.is_currency():
                raise TypeError("Division between two currency amounts is not allowed.")
            result_quantity = self.quantity / other.quantity
            return Measurement(
                Decimal(str(result_quantity.magnitude)), str(result_quantity.units)
            )
        elif isinstance(other, (Decimal, float, int)):
            if not isinstance(other, Decimal):
                other = Decimal(str(other))
            result_quantity = self.quantity / other
            return Measurement(
                Decimal(str(result_quantity.magnitude)), str(self.quantity.units)
            )
        else:
            raise TypeError(
                "Division is only allowed with Measurement or numeric values."
            )

    def __str__(self):
        if not str(self.quantity.units) == "dimensionless":
            return f"{self.quantity.magnitude} {self.quantity.units}"
        return f"{self.quantity.magnitude}"

    def __repr__(self):
        return f"Measurement({self.quantity.magnitude}, '{self.quantity.units}')"
