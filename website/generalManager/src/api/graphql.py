from __future__ import annotations
import graphene
from typing import Any, Callable, get_args, TYPE_CHECKING, cast
from decimal import Decimal
from datetime import date, datetime
import json
from generalManager.src.measurement.measurement import Measurement
from generalManager.src.manager.generalManager import GeneralManagerMeta
from generalManager.src.manager.generalManager import GeneralManager
from generalManager.src.manager.property import GraphQLProperty

if TYPE_CHECKING:
    from generalManager.src.interface.baseInterface import (
        InterfaceBase,
    )
    from generalManager.src.permission.basePermission import BasePermission
    from graphene import ResolveInfo as GraphQLResolveInfo


class MeasurementType(graphene.ObjectType):  # type: ignore
    value = graphene.Float()
    unit = graphene.String()


class GraphQL:
    _query_class: type
    graphql_type_registry: dict[str, type] = {}
    graphql_filter_type_registry: dict[str, type] = {}

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
                    field_type = cast(type, attr_value.graphql_type_hint)

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
    def _sort_by_options(
        generalManagerClass: GeneralManagerMeta,
    ) -> type[graphene.Enum]:
        sort_by_options = []
        for (
            field_name,
            field_type,
        ) in generalManagerClass.Interface.getAttributeTypes().items():
            if issubclass(field_type, GeneralManager):
                continue
            elif issubclass(field_type, Measurement):
                sort_by_options.append(f"{field_name}_value")
                sort_by_options.append(f"{field_name}_unit")
            else:
                sort_by_options.append(field_name)

        sort_by_options_class = type(
            f"{generalManagerClass.__name__}SortByOptions",
            (graphene.Enum,),
            {option: option for option in sort_by_options},
        )
        return sort_by_options_class

    @staticmethod
    def _createFilterOptions(field_name: str, field_type: GeneralManagerMeta):
        number_options = [
            "exact",
            "gt",
            "gte",
            "lt",
            "lte",
        ]
        string_options = [
            "exact",
            "icontains",
            "contains",
            "in",
            "startswith",
            "endswith",
        ]

        graphene_filter_type_name = f"{field_type.__name__}FilterType"
        if graphene_filter_type_name in GraphQL.graphql_filter_type_registry:
            return GraphQL.graphql_filter_type_registry[graphene_filter_type_name]
        filter_fields = {}
        for field_name, field_type in field_type.Interface.getAttributeTypes().items():
            if issubclass(field_type, GeneralManager):
                continue
            elif issubclass(field_type, Measurement):
                filter_fields[f"{field_name}_value"] = graphene.Float()
                filter_fields[f"{field_name}_unit"] = graphene.String()
                for option in number_options:
                    filter_fields[f"{field_name}_value__{option}"] = graphene.Float()
                    filter_fields[f"{field_name}_unit__{option}"] = graphene.String()

            else:
                filter_fields[field_name] = GraphQL.__map_field_to_graphene(
                    field_type, field_name
                )
                if issubclass(field_type, (int, float, Decimal, date, datetime)):
                    for option in number_options:
                        filter_fields[f"{field_name}__{option}"] = (
                            GraphQL.__map_field_to_graphene(field_type, field_name)
                        )
                elif issubclass(field_type, str):
                    for option in string_options:
                        filter_fields[f"{field_name}__{option}"] = (
                            GraphQL.__map_field_to_graphene(field_type, field_name)
                        )

        filter_class = type(
            graphene_filter_type_name,
            (graphene.InputObjectType,),
            filter_fields,
        )
        GraphQL.graphql_filter_type_registry[graphene_filter_type_name] = filter_class
        return filter_class

    @classmethod
    def __map_field_to_graphene(
        cls,
        field_type: GeneralManagerMeta | type,
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
                filter_options = cls._createFilterOptions(field_name, field_type)
                sort_by_options = cls._sort_by_options(field_type)
                return graphene.List(
                    lambda field_type=field_type: GraphQL.graphql_type_registry[
                        field_type.__name__
                    ],
                    filter=filter_options(),
                    exclude=filter_options(),
                    sort_by=sort_by_options(),
                    reverse=graphene.Boolean(),
                    page=graphene.Int(required=False, default_value=1),
                    page_size=graphene.Int(required=False, default_value=10),
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
        from generalManager.src.manager.generalManager import GeneralManager, Bucket

        def check_read_permission(
            self: GeneralManager,
            info: GraphQLResolveInfo,
            field_name: str,
        ) -> bool:
            PermissionClass: type[BasePermission] | None = getattr(
                self, "Permission", None
            )
            if PermissionClass:
                permission_allowed = PermissionClass(
                    self, info.context.user
                ).checkPermission("read", field_name)
                return permission_allowed
            return True

        if field_name.endswith("_list") and issubclass(field_type, GeneralManager):

            def list_resolver(
                self: GeneralManager,
                info: GraphQLResolveInfo,
                filter: dict[str, Any] | str | None = None,
                exclude: dict[str, Any] | str | None = None,
                sort_by: graphene.Enum | None = None,
                reverse: bool = False,
                page: int | None = None,
                page_size: int | None = None,
            ) -> Bucket[GeneralManager]:
                # Get related objects
                queryset = cast(Bucket, getattr(self, field_name).all())
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
                if sort_by:
                    sort_by_str = cast(str, getattr(sort_by, "value"))
                    queryset = queryset.sort(sort_by_str, reverse=reverse)
                if page or page_size:
                    page = page or 1
                    page_size = page_size or 10
                    offset = (page - 1) * page_size
                    queryset = queryset[offset : offset + page_size]
                return queryset

            return list_resolver

        if issubclass(field_type, Measurement):

            def measurement_resolver(
                self: GeneralManager,
                info: GraphQLResolveInfo,
                target_unit: str | None = None,
            ) -> dict[str, Any] | None:
                has_permision = check_read_permission(self, info, field_name)
                if not has_permision:
                    return None
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

        def normal_resolver(self: GeneralManager, info: GraphQLResolveInfo) -> Any:
            has_permision = check_read_permission(self, info, field_name)
            if not has_permision:
                return None
            return getattr(self, field_name)

        return normal_resolver

    @classmethod
    def __add_queries_to_schema(
        cls, graphene_type: type, generalManagerClass: GeneralManagerMeta
    ):
        if not issubclass(generalManagerClass, GeneralManager):
            raise TypeError(
                "generalManagerClass must be a subclass of GeneralManager to create a GraphQL interface"
            )
        # Sammeln der Felder
        if not hasattr(cls, "_query_fields"):
            cls._query_fields: dict[str, Any] = {}

        # Abfrage für die Liste
        list_field_name = f"{generalManagerClass.__name__.lower()}_list"
        filter_options = cls._createFilterOptions(
            generalManagerClass.__name__.lower(), generalManagerClass
        )
        sort_by_options = cls._sort_by_options(generalManagerClass)
        list_field = graphene.List(
            graphene_type,
            filter=filter_options(),
            exclude=filter_options(),
            sort_by=sort_by_options(),
            reverse=graphene.Boolean(),
            page=graphene.Int(required=False, default_value=1),
            page_size=graphene.Int(required=False, default_value=10),
        )

        def get_read_permission_filter(
            generalManagerClass: GeneralManagerMeta,
            info: GraphQLResolveInfo,
        ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
            filters = []
            PermissionClass: type[BasePermission] | None = getattr(
                generalManagerClass, "Permission", None
            )
            if PermissionClass:
                permission_filters = PermissionClass(
                    generalManagerClass, info.context.user
                ).getPermissionFilter()
                for permission_filter in permission_filters:
                    filter_dict, exclude_dict = (
                        permission_filter.get("filter", {}),
                        permission_filter.get("exclude", {}),
                    )
                    filters.append((filter_dict, exclude_dict))
            return filters

        def resolve_list(
            self: GeneralManager,
            info: GraphQLResolveInfo,
            filter: dict[str, Any] | str | None = None,
            exclude: dict[str, Any] | str | None = None,
            sort_by: graphene.Enum | None = None,
            reverse: bool = False,
            page: int | None = None,
            page_size: int | None = None,
        ):
            queryset = None
            permission_list = get_read_permission_filter(generalManagerClass, info)
            for permission_filter_dict, permission_exclude_dict in permission_list:
                permission_queryset = generalManagerClass.exclude(
                    **permission_exclude_dict
                ).filter(**permission_filter_dict)
                if queryset is None:
                    queryset = permission_queryset
                else:
                    queryset = queryset | permission_queryset
            if queryset is None:
                queryset = generalManagerClass.all()
            if filter:
                filter_dict = json.loads(filter) if isinstance(filter, str) else filter
                queryset = queryset.filter(**filter_dict)
            if exclude:
                exclude_dict = (
                    json.loads(exclude) if isinstance(exclude, str) else exclude
                )
                queryset = queryset.exclude(**exclude_dict)
            if sort_by:
                sort_by_str = cast(str, getattr(sort_by, "value"))
                queryset = queryset.sort(sort_by_str, reverse=reverse)
            if page or page_size:
                page = page or 1
                page_size = page_size or 10
                offset = (page - 1) * page_size
                queryset = queryset[offset : offset + page_size]
            return queryset

        cls._query_fields[list_field_name] = list_field
        cls._query_fields[f"resolve_{list_field_name}"] = resolve_list

        # Abfrage für ein einzelnes Objekt
        item_field_name = generalManagerClass.__name__.lower()

        generalManagerClass.Interface.input_fields
        identification_dict = {}
        for (
            input_field_name,
            input_field,
        ) in generalManagerClass.Interface.input_fields.items():
            if issubclass(input_field.type, GeneralManager):
                input_field_name = f"{input_field_name}_id"
                input_type = graphene.Int()
            elif input_field_name == "id":
                input_type = graphene.ID()
            else:
                input_type = cls.__map_field_to_graphene(
                    input_field.type, input_field_name
                )

            identification_dict[input_field_name] = input_type
            identification_dict[input_field_name].required = True
        item_field = graphene.Field(graphene_type, **identification_dict)

        def resolve_item(
            self: GeneralManager, info: GraphQLResolveInfo, **identification: dict
        ) -> GeneralManager:
            return generalManagerClass(**identification)

        cls._query_fields[item_field_name] = item_field
        cls._query_fields[f"resolve_{item_field_name}"] = resolve_item
