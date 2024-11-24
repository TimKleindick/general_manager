from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Type, ClassVar, Callable, TYPE_CHECKING, Iterable
from datetime import datetime

from generalManager.src.manager.bucket import Bucket
from django.conf import settings


if TYPE_CHECKING:
    from generalManager.src.manager.input import Input
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


class InterfaceBase(ABC):
    _parent_class: ClassVar[Type[Any]]
    _interface_type: ClassVar[str]
    input_fields: dict[str, Input]

    def __init__(self, *args: Any, **kwargs: Any):
        self.identification = self.parseInputFieldsToIdentification(*args, **kwargs)

    def parseInputFieldsToIdentification(
        self,
        *args: list[Any],
        **kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        identification = {}
        kwargs = args_to_kwargs(args, self.input_fields.keys(), kwargs)
        # Prüfe auf fehlende oder unerwartete Argumente
        extra_args = set(kwargs.keys()) - set(self.input_fields.keys())
        if extra_args:
            for extra_arg in extra_args:
                if extra_arg.replace("_id", "") in self.input_fields.keys():
                    kwargs[extra_arg.replace("_id", "")] = kwargs.pop(extra_arg)
                else:
                    raise TypeError(f"Unexpected arguments: {', '.join(extra_args)}")

        missing_args = set(self.input_fields.keys()) - set(kwargs.keys())
        if missing_args:
            raise TypeError(f"Missing required arguments: {', '.join(missing_args)}")

        # Verarbeite Felder unter Berücksichtigung von Abhängigkeiten
        processed = set()
        while len(processed) < len(self.input_fields):
            progress_made = False
            for name, input_field in self.input_fields.items():
                if name in processed:
                    continue
                depends_on = input_field.depends_on
                if all(dep in processed for dep in depends_on):
                    value = self.input_fields[name].cast(kwargs[name])
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
        if not isinstance(value, input_field.type):
            raise TypeError(
                f"Invalid type for {name}: {type(value)}, expected: {input_field.type}"
            )
        if settings.DEBUG:
            # Prüfe mögliche Werte
            possible_values = input_field.possible_values
            if possible_values is not None:
                if callable(possible_values):
                    depends_on = input_field.depends_on
                    dep_values = {
                        dep_name: identification.get(dep_name)
                        for dep_name in depends_on
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
    @abstractmethod
    def create(cls, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    @abstractmethod
    def update(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    @abstractmethod
    def deactivate(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    @abstractmethod
    def getData(self, search_date: datetime | None = None) -> Any:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def getAttributeTypes(cls) -> dict[str, type]:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def getAttributes(cls) -> dict[str, Any]:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def filter(cls, **kwargs: Any) -> Bucket[Any]:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def exclude(cls, **kwargs: Any) -> Bucket[Any]:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def handleInterface(
        cls,
    ) -> tuple[
        Callable[
            [str, dict[str, Any], Type[InterfaceBase]],
            tuple[dict[str, Any], Type[InterfaceBase], Type[Any]],
        ],
        Callable[
            [
                Type[GeneralManagerMeta],
                Type[GeneralManager],
                Type[InterfaceBase],
                Type[Any],
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
        raise NotImplementedError
