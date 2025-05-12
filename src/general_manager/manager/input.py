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
        Initializes an Input instance with type information, possible values, and dependencies.
        
        Args:
            type: The expected type for the input value.
            possible_values: An optional iterable or callable that defines allowed values.
            depends_on: An optional list of dependency names. If not provided and possible_values is callable, dependencies are inferred from its parameters.
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
        Casts the input value to the type specified by this Input instance.
        
        Handles special cases for date, datetime, GeneralManager subclasses, and Measurement types.
        If the value is already of the target type, it is returned unchanged. Otherwise, attempts to
        convert or construct the value as appropriate for the target type.
        
        Args:
            value: The value to be cast or converted.
        
        Returns:
            The value converted to the target type, or an instance of the target type.
        """
        if self.type == date:
            if isinstance(value, datetime) and type(value) is not date:
                return value.date()
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
