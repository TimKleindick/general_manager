from __future__ import annotations
from generalManager.src.manager.interface import (
    DBBasedInterface,
    ReadOnlyInterface,
    GeneralManagerModel,
    InterfaceBase,
)
from typing import Type, Callable
from django.core.exceptions import ValidationError
from generalManager.src.factory.factories import AutoFactory
from website.settings import AUTOCREATE_GRAPHQL
from django.db import models
import graphene
import json
from generalManager.src.manager.bucket import Bucket


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
    graphql_type_registry: dict = {}
    _query_class: Type
    pending_graphql_interfaces: list[Type] = []

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
        else:
            new_class = super().__new__(mcs, name, bases, attrs)

        if AUTOCREATE_GRAPHQL:
            mcs.pending_graphql_interfaces.append(new_class)

        return new_class

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

    @staticmethod
    def _createGraphQlInterface(generalManagerClass: Type[GeneralManagerMeta]):
        interface_cls: InterfaceBase | None = getattr(
            generalManagerClass, "Interface", None
        )
        if not interface_cls:
            return

        graphene_type_name = f"{generalManagerClass.__name__}Type"

        # Felder zum Graphene-Objekt hinzufügen
        fields = {}

        for field_name, field_type in interface_cls.getAttributeTypes().items():
            fields[field_name] = GeneralManagerMeta.__map_field_to_graphene(
                field_type, field_name
            )
            resolver_name = f"resolve_{field_name}"
            resolver = GeneralManagerMeta.__create_resolver(field_name, field_type)
            fields[resolver_name] = resolver

        graphene_type = type(graphene_type_name, (graphene.ObjectType,), fields)

        # Hinzufügen des Typs zum Registry
        GeneralManagerMeta.graphql_type_registry[generalManagerClass.__name__] = (
            graphene_type
        )

        # Hinzufügen der Abfragen zum Schema
        GeneralManagerMeta.__add_queries_to_schema(graphene_type, generalManagerClass)

    @staticmethod
    def __map_field_to_graphene(
        field_type: Type,
        field_name: str,
    ) -> (
        graphene.Field
        | graphene.String
        | graphene.Int
        | graphene.Float
        | graphene.Boolean
        | graphene.Date
        | graphene.List
    ):
        from generalManager.src.manager.generalManager import GeneralManager

        if field_type == models.CharField or field_type == models.TextField:
            return graphene.String()
        elif field_type == models.IntegerField:
            return graphene.Int()
        elif field_type == models.FloatField or field_type == models.DecimalField:
            return graphene.Float()
        elif field_type == models.BooleanField:
            return graphene.Boolean()
        elif field_type == models.DateField or field_type == models.DateTimeField:
            return graphene.Date()
        elif issubclass(field_type, GeneralManager):
            if field_name.endswith("_list"):
                return graphene.List(
                    lambda field_type=field_type: GeneralManagerMeta.graphql_type_registry[
                        field_type.__name__
                    ],
                    filter=graphene.JSONString(),
                    exclude=graphene.JSONString(),
                )
            return graphene.Field(
                lambda field_type=field_type: GeneralManagerMeta.graphql_type_registry[
                    field_type.__name__
                ]
            )
        else:
            # Fallback für andere Feldtypen
            return graphene.String()

    @staticmethod
    def __create_resolver(field_name: str, field_type: Type) -> Callable:
        from generalManager.src.manager.generalManager import GeneralManager

        if field_name.endswith("_list") and issubclass(field_type, GeneralManager):

            def list_resolver(self, info, filter=None, exclude=None):
                # Get related objects
                queryset = getattr(self, field_name).all()
                if filter:
                    filter_dict = (
                        json.loads(filter) if isinstance(filter, str) else filter
                    )
                    queryset = queryset.filter(**filter_dict)
                if exclude:
                    exclude_dict = (
                        json.loads(exclude) if isinstance(exclude, str) else exclude
                    )
                    queryset = queryset.exclude(**exclude_dict)
                return queryset

            return list_resolver
        else:

            def normal_resolver(self, info):
                return getattr(self, field_name)

            return normal_resolver

    @staticmethod
    def __add_queries_to_schema(graphene_type, generalManagerClass):
        # Sammeln der Felder
        if not hasattr(GeneralManagerMeta, "_query_fields"):
            GeneralManagerMeta._query_fields = {}

        # Abfrage für die Liste
        list_field_name = f"{generalManagerClass.__name__.lower()}_list"
        list_field = graphene.List(
            graphene_type, filter=graphene.JSONString(), exclude=graphene.JSONString()
        )

        def resolve_list(self, info, filter=None, exclude=None):
            queryset = generalManagerClass.all()
            if filter:
                filter_dict = json.loads(filter) if isinstance(filter, str) else filter
                queryset = queryset.filter(**filter_dict)
            if exclude:
                exclude_dict = (
                    json.loads(exclude) if isinstance(exclude, str) else exclude
                )
                queryset = queryset.exclude(**exclude_dict)
            return queryset

        GeneralManagerMeta._query_fields[list_field_name] = list_field
        GeneralManagerMeta._query_fields[f"resolve_{list_field_name}"] = resolve_list

        # Abfrage für ein einzelnes Objekt
        item_field_name = generalManagerClass.__name__.lower()
        item_field = graphene.Field(graphene_type, id=graphene.Int(required=True))

        def resolve_item(self, info, id):
            return generalManagerClass(id)

        GeneralManagerMeta._query_fields[item_field_name] = item_field
        GeneralManagerMeta._query_fields[f"resolve_{item_field_name}"] = resolve_item
