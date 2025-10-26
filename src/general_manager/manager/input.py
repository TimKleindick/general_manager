"""Input field metadata used by GeneralManager interfaces."""

from __future__ import annotations
from typing import Iterable, Optional, Callable, List, TypeVar, Generic, Any, Type, cast
import inspect

from general_manager.manager.general_manager import GeneralManager
from datetime import date, datetime
from general_manager.measurement import Measurement


INPUT_TYPE = TypeVar("INPUT_TYPE", bound=type)


class Input(Generic[INPUT_TYPE]):
    """Descriptor describing the expected type and constraints for an interface input."""

    def __init__(
        self,
        type: INPUT_TYPE,
        possible_values: Optional[Callable | Iterable] = None,
        depends_on: Optional[List[str]] = None,
    ) -> None:
        """
        Create an Input specification with type information, allowed values, and dependency metadata.

        Parameters:
            type (INPUT_TYPE): Expected Python type for the input value.
            possible_values (Callable | Iterable | None): Allowed values as an iterable or callable returning allowed values.
            depends_on (list[str] | None): Names of other inputs required for computing possible values.
        """
        self.type: Type[Any] = cast(Type[Any], type)
        self.possible_values = possible_values
        self.is_manager = issubclass(type, GeneralManager)

        if depends_on is not None:
            # Use the provided dependency list when available
            self.depends_on = depends_on
        elif callable(possible_values):
            # Derive dependencies automatically from the callable signature
            sig = inspect.signature(possible_values)
            self.depends_on = list(sig.parameters.keys())
        else:
            # Default to no dependencies when none are provided
            self.depends_on = []

    def cast(self, value: Any) -> Any:
        """
        Convert a raw value to the configured input type.

        Parameters:
            value (Any): Raw value supplied by the caller.

        Returns:
            Any: Value converted to the target type.

        Raises:
            ValueError: If the value cannot be converted to the target type.
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
