from django.db import models
from generalManager.src.manager.interface import (
    DBBasedInterface,
    ReadOnlyInterface,
    GeneralManagerModel,
)
from typing import Type
from django.core.exceptions import ValidationError
from generalManager.src.factory.factories import AutoFactory


def getFullCleanMethode(model):
    def full_clean(self, *args, **kwargs):
        errors = {}

        try:
            super(model, self).full_clean(*args, **kwargs)
        except ValidationError as e:
            errors.update(e.message_dict)

        for rule in self._meta.rules:
            if not rule.evaluate(self):
                errors.update(rule.getErrorMessage())

        if errors:
            raise ValidationError(errors)

    return full_clean


class GeneralManagerMeta(type):
    read_only_classes: list[Type] = []

    def __new__(mcs, name, bases, attrs):
        if "Interface" in attrs:
            interface = attrs.pop("Interface")
            if issubclass(interface, DBBasedInterface):
                attrs, interface_cls, model = mcs.__handleDBBasedInterface(
                    name, attrs, interface
                )
                new_class = super().__new__(mcs, name, bases, attrs)
                interface_cls._parent_class = new_class
                model._general_manager_class = new_class
                if issubclass(interface, ReadOnlyInterface):
                    mcs.read_only_classes.append(interface_cls)
                return new_class
        else:
            return super().__new__(mcs, name, bases, attrs)

    @staticmethod
    def __handleDBBasedInterface(
        name: str, attrs: dict, interface: Type[DBBasedInterface]
    ) -> tuple[dict, Type, Type]:
        # Felder aus der Interface-Klasse sammeln
        model_fields = {}
        meta_class = None
        for attr_name, attr_value in interface.__dict__.items():
            if not attr_name.startswith("__"):
                if attr_name == "Meta" and isinstance(attr_value, type):
                    # Meta-Klasse speichern
                    meta_class = attr_value
                elif attr_name == "Factory":
                    # Factory nicht in model_fields speichern
                    pass
                else:
                    model_fields[attr_name] = attr_value
        model_fields["__module__"] = attrs.get("__module__")
        # Meta-Klasse hinzufügen oder erstellen
        if meta_class:
            rules = None
            model_fields["Meta"] = meta_class

            if hasattr(meta_class, "rules"):
                rules = meta_class.rules
                delattr(meta_class, "rules")

        # Modell erstellen
        model = type(name, (GeneralManagerModel,), model_fields)
        if meta_class and rules:
            model._meta.rules = rules  # type: ignore
            # full_clean Methode hinzufügen
            model.full_clean = getFullCleanMethode(model)
        # Interface-Typ bestimmen
        if issubclass(interface, DBBasedInterface):
            attrs["_interface_type"] = interface._interface_type
            interface_cls = type(interface.__name__, (interface,), {})
            interface_cls._model = model
            attrs["Interface"] = interface_cls
        else:
            raise TypeError("Interface must be a subclass of DBBasedInterface")
        # add factory class
        factory_definition = getattr(interface, "Factory", None)
        factory_attributes = {}
        if factory_definition:
            for attr_name, attr_value in factory_definition.__dict__.items():
                if not attr_name.startswith("__"):
                    factory_attributes[attr_name] = attr_value
        factory_attributes["interface"] = interface_cls
        factory_attributes["Meta"] = type("Meta", (), {"model": model})
        factory_class = type(f"{name}Factory", (AutoFactory,), factory_attributes)
        factory_class._meta.model = model
        attrs["Factory"] = factory_class

        return attrs, interface_cls, model
