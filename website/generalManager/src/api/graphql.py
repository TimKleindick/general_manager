from __future__ import annotations
import graphene
from typing import Any, Callable, get_type_hints, get_args, TYPE_CHECKING
from decimal import Decimal
from datetime import date, datetime
import json
from generalManager.src.measurement.measurement import Measurement
from generalManager.src.manager.generalManager import GeneralManagerMeta

if TYPE_CHECKING:
    from generalManager.src.interface import (
        InterfaceBase,
    )
    from generalManager.src.manager.generalManager import GeneralManager


class GraphQLProperty(property):
    def __init__(self, fget: Callable[..., Any], doc: str | None = None):
        super().__init__(fget, doc=doc)
        self.is_graphql_resolver = True
        self.graphql_type_hint = get_type_hints(fget).get("return", None)


def graphQlProperty(func: Callable[..., Any]):
    """
    Dekorator für GraphQL-Feld-Resolver, der automatisch:
    - die Methode als benutzerdefiniertes Property registriert,
    - die Resolver-Informationen speichert,
    - den Field-Typ aus dem Type-Hint ableitet.
    """
    return GraphQLProperty(func)


class MeasurementType(graphene.ObjectType):  # type: ignore
    value = graphene.Float()
    unit = graphene.String()


class GraphQL:
    _query_class: type
    graphql_type_registry: dict[str, type] = {}

    @classmethod
    def _createGraphQlInterface(cls, generalManagerClass: GeneralManagerMeta):
        interface_cls: InterfaceBase | None = getattr(
            generalManagerClass, "Interface", None
        )
        if not interface_cls:
            return

        graphene_type_name = f"{generalManagerClass.__name__}Type"

        # Felder zum Graphene-Objekt hinzufügen
        fields: dict[str, Any] = {}
        for field_name, field_type in interface_cls.getAttributeTypes().items():
            fields[field_name] = cls.__map_field_to_graphene(field_type, field_name)
            resolver_name = f"resolve_{field_name}"
            resolver = cls.__create_resolver(field_name, field_type)
            fields[resolver_name] = resolver

        for attr_name, attr_value in generalManagerClass.__dict__.items():
            if isinstance(attr_value, GraphQLProperty):
                type_hint = get_args(attr_value.graphql_type_hint)
                if type_hint:
                    field_type: type = type_hint[0] or type_hint[1]
                else:
                    field_type = attr_value.graphql_type_hint

                fields[attr_name] = cls.__map_field_to_graphene(field_type, attr_name)

                fields[f"resolve_{attr_name}"] = cls.__create_resolver(
                    attr_name, field_type
                )

        graphene_type = type(graphene_type_name, (graphene.ObjectType,), fields)

        # Hinzufügen des Typs zum Registry
        cls.graphql_type_registry[generalManagerClass.__name__] = graphene_type

        # Hinzufügen der Abfragen zum Schema
        cls.__add_queries_to_schema(graphene_type, generalManagerClass)

    @staticmethod
    def __map_field_to_graphene(
        field_type: type,
        field_name: str,
    ) -> (
        graphene.Field
        | graphene.Int
        | graphene.Float
        | graphene.Boolean
        | graphene.Date
        | graphene.List
        | graphene.String
        | str
    ):
        from generalManager.src.manager.generalManager import GeneralManager

        if issubclass(field_type, str):
            return graphene.String()
        elif issubclass(field_type, bool):
            return graphene.Boolean()
        elif issubclass(field_type, int):
            return graphene.Int()
        elif issubclass(field_type, (float, Decimal)):
            return graphene.Float()
        elif issubclass(field_type, (date, datetime)):
            return graphene.Date()
        elif issubclass(field_type, Measurement):
            return graphene.Field(MeasurementType, target_unit=graphene.String())
        elif issubclass(field_type, GeneralManager):
            if field_name.endswith("_list"):
                return graphene.List(
                    lambda field_type=field_type: GraphQL.graphql_type_registry[
                        field_type.__name__
                    ],
                    filter=graphene.JSONString(),
                    exclude=graphene.JSONString(),
                )
            return graphene.Field(
                lambda field_type=field_type: GraphQL.graphql_type_registry[
                    field_type.__name__
                ]
            )
        else:
            return graphene.String()

    @staticmethod
    def __create_resolver(field_name: str, field_type: type) -> Callable[..., Any]:
        from generalManager.src.manager.generalManager import GeneralManager

        if field_name.endswith("_list") and issubclass(field_type, GeneralManager):

            def list_resolver(
                self: GeneralManager,
                info: str,
                filter: dict[str, Any] | str | None = None,
                exclude: dict[str, Any] | str | None = None,
            ):
                # Get related objects
                queryset = getattr(self, field_name).all()
                try:
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
                except Exception:
                    pass
                return queryset

            return list_resolver

        if issubclass(field_type, Measurement):

            def measurement_resolver(
                self: GeneralManager, info: str, target_unit: str | None = None
            ) -> dict[str, Any] | None:
                result = getattr(self, field_name)
                if not isinstance(result, Measurement):
                    return None
                if target_unit:
                    result = result.to(target_unit)
                return {
                    "value": result.quantity.magnitude,
                    "unit": result.quantity.units,
                }

            return measurement_resolver

        def normal_resolver(self: GeneralManager, info: str) -> Any:
            return getattr(self, field_name)

        return normal_resolver

    @classmethod
    def __add_queries_to_schema(
        cls, graphene_type: type, generalManagerClass: type[GeneralManager]
    ):
        # Sammeln der Felder
        if not hasattr(GeneralManagerMeta, "_query_fields"):
            cls._query_fields: dict[str, Any] = {}

        # Abfrage für die Liste
        list_field_name = f"{generalManagerClass.__name__.lower()}_list"
        list_field = graphene.List(
            graphene_type, filter=graphene.JSONString(), exclude=graphene.JSONString()
        )

        def resolve_list(
            self: GeneralManager,
            info: str,
            filter: dict[str, Any] | str | None = None,
            exclude: dict[str, Any] | str | None = None,
        ):
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

        cls._query_fields[list_field_name] = list_field
        cls._query_fields[f"resolve_{list_field_name}"] = resolve_list

        # Abfrage für ein einzelnes Objekt
        item_field_name = generalManagerClass.__name__.lower()
        item_field = graphene.Field(graphene_type, id=graphene.Int(required=True))

        def resolve_item(self: GeneralManager, info: str, id: int) -> GeneralManager:
            return generalManagerClass(id)

        cls._query_fields[item_field_name] = item_field
        cls._query_fields[f"resolve_{item_field_name}"] = resolve_item
