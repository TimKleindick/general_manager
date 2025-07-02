from __future__ import annotations
from typing import Iterable, Optional, Callable, List, TypeVar, Generic, Any
import inspect

from general_manager.manager.generalManager import GeneralManager
from datetime import date, datetime
from general_manager.measurement import Measurement


INPUT_TYPE = TypeVar("INPUT_TYPE", bound=type)


class Input(Generic[INPUT_TYPE]):
    def __init__(
        self,
        type: INPUT_TYPE,
        possible_values: Optional[Callable | Iterable] = None,
        depends_on: Optional[List[str]] = None,
    ):
        """
        Create an Input specification with type information, allowed values, and dependency metadata.
        
        Parameters:
            type: The expected Python type for the input value.
            possible_values: Optional; an iterable of allowed values or a callable returning allowed values.
            depends_on: Optional; a list of dependency names. If not provided and possible_values is callable, dependencies are inferred from the callable's parameter names.
        """
        self.type = type
        self.possible_values = possible_values
        self.is_manager = issubclass(type, GeneralManager)

        if depends_on is not None:
            # Verwende die angegebenen Abhängigkeiten
            self.depends_on = depends_on
        elif callable(possible_values):
            # Ermittele Abhängigkeiten automatisch aus den Parametern der Funktion
            sig = inspect.signature(possible_values)
            self.depends_on = list(sig.parameters.keys())
        else:
            # Keine Abhängigkeiten
            self.depends_on = []

    def cast(self, value: Any) -> Any:
        """
        Converts the input value to the type specified by this Input instance, handling special cases for dates, datetimes, GeneralManager subclasses, and Measurement types.
        
        If the value is already of the target type, it is returned unchanged. For date and datetime types, string and cross-type conversions are supported. For GeneralManager subclasses, instances are constructed from a dictionary or an ID. For Measurement, string values are parsed accordingly. Otherwise, the value is cast using the target type's constructor.
        
        Returns:
            The value converted to the target type, or an instance of the target type.
        """
        if self.type == date:
            if isinstance(value, datetime) and type(value) is not date:
                return value.date()
            elif isinstance(value, date):
                return value
            return date.fromisoformat(value)
        if self.type == datetime:
            if isinstance(value, date):
                return datetime.combine(value, datetime.min.time())
            return datetime.fromisoformat(value)
        if isinstance(value, self.type):
            return value
        if issubclass(self.type, GeneralManager):
            if isinstance(value, dict):
                return self.type(**value)  # type: ignore
            return self.type(id=value)  # type: ignore
        if self.type == Measurement and isinstance(value, str):
            return Measurement.from_string(value)
        return self.type(value)
