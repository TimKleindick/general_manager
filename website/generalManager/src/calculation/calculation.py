from .meta import CalculationMeta
from .input import Input
from typing import Any, Iterable


class Calculation(metaclass=CalculationMeta):
    _input_fields: dict[str, Input]

    def __init__(self, **kwargs: dict[str, Any]):
        input_fields = self._input_fields

        # Prüfe auf fehlende oder unerwartete Argumente
        missing_args = set(input_fields.keys()) - set(kwargs.keys())
        if missing_args:
            raise TypeError(f"Missing required arguments: {', '.join(missing_args)}")

        extra_args = set(kwargs.keys()) - set(input_fields.keys())
        if extra_args:
            raise TypeError(f"Unexpected arguments: {', '.join(extra_args)}")

        # Verarbeite Felder unter Berücksichtigung von Abhängigkeiten
        processed = set()
        while len(processed) < len(input_fields):
            progress_made = False
            for name, input_field in input_fields.items():
                if name in processed:
                    continue
                depends_on = input_field.depends_on
                if all(dep in processed for dep in depends_on):
                    value = kwargs[name]
                    self._process_input(name, value)
                    processed.add(name)
                    progress_made = True
            if not progress_made:
                # Zirkuläre Abhängigkeit erkannt
                unresolved = set(input_fields.keys()) - processed
                raise ValueError(
                    f"Circular dependency detected among inputs: {', '.join(unresolved)}"
                )

    def _process_input(self, name: str, value: Any) -> None:
        input_field = self._input_fields[name]

        # Prüfe mögliche Werte
        possible_values = input_field.possible_values
        if possible_values is not None:
            if callable(possible_values):
                depends_on = input_field.depends_on
                dep_values = {
                    dep_name: getattr(self, dep_name) for dep_name in depends_on
                }
                allowed_values = possible_values(**dep_values)
            elif isinstance(possible_values, Iterable):
                allowed_values = possible_values
            else:
                raise TypeError(f"Invalid type for possible_values of input {name}")

            if value not in allowed_values:
                raise ValueError(
                    f"Invalid value for {name}: {value}, allowed: {allowed_values}"
                )
        setattr(self, f"__{name}", value)
