from __future__ import annotations
from typing import Iterable, Optional, Callable, List, TypeVar, Generic
import inspect

T = TypeVar("T")


class Input(Generic[T]):
    def __init__(
        self,
        type: T,
        possible_values: Optional[Callable | Iterable] = None,
        depends_on: Optional[List[str]] = None,
    ):
        self.type = type
        self.possible_values = possible_values

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
