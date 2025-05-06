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
        if isinstance(value, self.type):
            return value
        if issubclass(self.type, GeneralManager):
            if isinstance(value, dict):
                return self.type(**value)  # type: ignore
            return self.type(id=value)  # type: ignore
        if self.type == date:
            return date.fromisoformat(value)
        if self.type == datetime:
            return datetime.fromisoformat(value)
        if self.type == Measurement and isinstance(value, str):
            return Measurement.from_string(value)
        return self.type(value)
