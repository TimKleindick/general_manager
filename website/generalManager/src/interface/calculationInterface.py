from __future__ import annotations
from datetime import datetime
from typing import Any, Type, TYPE_CHECKING, Callable, Iterable
from generalManager.src.interface.baseInterface import InterfaceBase
from generalManager.src.manager.bucket import CalculationBucket
from generalManager.src.calculation.input import Input

if TYPE_CHECKING:
    from generalManager.src.manager.generalManager import GeneralManager
    from generalManager.src.manager.meta import GeneralManagerMeta


def args_to_kwargs(args, keys, existing_kwargs=None):
    """
    Wandelt *args in **kwargs um und kombiniert sie mit bestehenden **kwargs.

    :param args: Tuple der positional arguments (z. B. *args).
    :param keys: Liste der Schlüssel, die den Argumenten zugeordnet werden.
    :param existing_kwargs: Optionales Dictionary mit bereits existierenden Schlüssel-Wert-Zuordnungen.
    :return: Dictionary mit kombinierten **kwargs.
    """
    if len(args) > len(keys):
        raise ValueError("Mehr args als keys vorhanden.")

    kwargs = {key: value for key, value in zip(keys, args)}
    if existing_kwargs:
        kwargs.update(existing_kwargs)

    return kwargs


class CalculationInterface(InterfaceBase):
    _interface_type = "calculation"
    input_fields: dict[str, Input]

    def parseInputFieldsToIdentification(
        self,
        *args: list[Any],
        **kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        identification = {}
        kwargs = args_to_kwargs(args, self.input_fields.keys(), kwargs)
        # Prüfe auf fehlende oder unerwartete Argumente
        missing_args = set(self.input_fields.keys()) - set(kwargs.keys())
        if missing_args:
            raise TypeError(f"Missing required arguments: {', '.join(missing_args)}")

        extra_args = set(kwargs.keys()) - set(self.input_fields.keys())
        if extra_args:
            raise TypeError(f"Unexpected arguments: {', '.join(extra_args)}")

        # Verarbeite Felder unter Berücksichtigung von Abhängigkeiten
        processed = set()
        while len(processed) < len(self.input_fields):
            progress_made = False
            for name, input_field in self.input_fields.items():
                if name in processed:
                    continue
                depends_on = input_field.depends_on
                if all(dep in processed for dep in depends_on):
                    value = kwargs[name]
                    self._process_input(name, value, identification)
                    identification[name] = value
                    processed.add(name)
                    progress_made = True
            if not progress_made:
                # Zirkuläre Abhängigkeit erkannt
                unresolved = set(self.input_fields.keys()) - processed
                raise ValueError(
                    f"Circular dependency detected among inputs: {', '.join(unresolved)}"
                )
        return identification

    def _process_input(
        self, name: str, value: Any, identification: dict[str, Any]
    ) -> None:
        input_field = self.input_fields[name]

        # Prüfe mögliche Werte
        possible_values = input_field.possible_values
        if possible_values is not None:
            if callable(possible_values):
                depends_on = input_field.depends_on
                dep_values = {
                    dep_name: identification.get(dep_name) for dep_name in depends_on
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

    @classmethod
    def create(cls, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("Calculations are generated, not created directly.")

    def update(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("Calculations cannot be updated.")

    def deactivate(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("Calculations cannot be deactivated.")

    def getData(self, search_date: datetime | None = None) -> Any:
        raise NotImplementedError("Calculations do not store data.")

    @classmethod
    def getAttributeTypes(cls) -> dict[str, type]:
        return {name: field.type for name, field in cls.input_fields.items()}

    def getAttributes(self) -> dict[str, Any]:
        return self.identification

    @classmethod
    def filter(cls, **kwargs: Any) -> CalculationBucket:
        return CalculationBucket(cls._parent_class).filter(**kwargs)

    @classmethod
    def exclude(cls, **kwargs: Any) -> CalculationBucket:
        return CalculationBucket(cls._parent_class).exclude(**kwargs)

    @classmethod
    def all(cls) -> CalculationBucket:
        return CalculationBucket(cls._parent_class).all()

    @staticmethod
    def _preCreate(
        name: str, attrs: dict[str, Any], interface: Type[CalculationInterface]
    ) -> tuple[dict[str, Any], Type[CalculationInterface], None]:
        # Felder aus der Interface-Klasse sammeln
        input_fields: dict[str, Input[Any]] = {}
        for key, value in vars(interface).items():
            if key.startswith("__"):
                continue
            if isinstance(value, Input):
                input_fields[key] = value

        # Interface-Typ bestimmen
        attrs["_interface_type"] = interface._interface_type
        interface_cls = type(
            interface.__name__, (interface,), {"input_fields": input_fields}
        )
        attrs["Interface"] = interface_cls

        return attrs, interface_cls, None

    @staticmethod
    def _postCreate(
        mcs: Type[GeneralManagerMeta],
        new_class: Type[GeneralManager],
        interface_class: Type[CalculationInterface],
        model: None,
    ) -> None:
        interface_class._parent_class = new_class

    @classmethod
    def handleInterface(cls) -> tuple[  # type: ignore
        Callable[
            [str, dict[str, Any], Type[CalculationInterface]],
            tuple[dict[str, Any], Type[CalculationInterface], None],
        ],
        Callable[
            [
                Type[GeneralManagerMeta],
                Type[GeneralManager],
                Type[CalculationInterface],
                None,
            ],
            None,
        ],
    ]:
        """
        This method returns a pre and a post GeneralManager creation method
        and is called inside the GeneralManagerMeta class to initialize the
        Interface.
        The pre creation method is called before the GeneralManager instance
        is created to modify the kwargs.
        The post creation method is called after the GeneralManager instance
        is created to modify the instance and add additional data.
        """
        return cls._preCreate, cls._postCreate
