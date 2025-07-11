from __future__ import annotations
from datetime import date, datetime
from typing import Any, Type

from general_manager.manager.generalManager import GeneralManager
from general_manager.measurement import Measurement


class ExcelDataField:
    """Describe a single column inside an Excel import/export."""

    def __init__(
        self, type: Type[Any], default: Any = None, is_required: bool = True
    ) -> None:
        self.type = type
        self.default = default
        self.is_required = is_required
        self.is_manager = issubclass(type, GeneralManager)

    @property
    def python_type(self) -> Type[Any]:
        """Return the configured Python type."""
        return self.type

    def cast(self, value: Any) -> Any:
        """Convert an Excel cell value to the configured type."""

        if self.type == date:
            if isinstance(value, datetime) and type(value) is not date:
                return value.date()
            if isinstance(value, date):
                return value
            return date.fromisoformat(str(value))

        if self.type == datetime:
            if isinstance(value, date) and not isinstance(value, datetime):
                return datetime.combine(value, datetime.min.time())
            if isinstance(value, datetime):
                return value
            return datetime.fromisoformat(str(value))

        if isinstance(value, self.type):
            return value

        if issubclass(self.type, GeneralManager):
            if isinstance(value, dict):
                return self.type(**value)
            return self.type(id=value)

        if self.type == Measurement and isinstance(value, str):
            return Measurement.from_string(value)

        return self.type(value)
