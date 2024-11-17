from typing import Any
from .input import Input


class CalculationMeta(type):
    def __new__(mcs, name, bases, attrs):
        is_base_calculation = name == "Calculation" and all(
            base.__name__ == "object" for base in bases
        )

        if "Input" not in attrs and not is_base_calculation:
            raise TypeError("Calculation must have an Input attribute")

        InputClass = attrs.get("Input", None)
        input_fields: dict[str, Input] = {}

        if InputClass:
            # Sammle die Input-Felder
            for key, value in vars(InputClass).items():
                if key.startswith("__"):
                    continue
                if isinstance(value, Input):
                    input_fields[key] = value

            # Verarbeite Abhängigkeiten
            for input_name, input_field in input_fields.items():
                depends_on = input_field.depends_on or []
                resolved_depends_on = []
                for dep in depends_on:
                    if isinstance(dep, Input):
                        # Finde den Namen des Input-Feldes
                        found = False
                        for name, field in input_fields.items():
                            if field is dep:
                                resolved_depends_on.append(name)
                                found = True
                                break
                        if not found:
                            raise ValueError(f"Dependency {dep} not found among inputs")
                    elif isinstance(dep, str):
                        resolved_depends_on.append(dep)
                    else:
                        raise TypeError(f"Invalid dependency type for {input_name}")
                input_field.depends_on = resolved_depends_on

            # Speichere die Input-Felder in der Klasse
            attrs["_input_fields"] = input_fields

            # Erstelle @property-Methoden für jedes Feld
            for field_name in input_fields.keys():

                def getter(self, name=field_name):
                    return getattr(self, f"__{name}")

                attrs[field_name] = property(getter)

        return super().__new__(mcs, name, bases, attrs)
