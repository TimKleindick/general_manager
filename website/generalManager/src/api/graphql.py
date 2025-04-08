from __future__ import annotations
import graphene
from typing import Any, Callable, get_args, TYPE_CHECKING, cast
from decimal import Decimal
from datetime import date, datetime
import json

# Eigene Module
from generalManager.src.measurement.measurement import Measurement
from generalManager.src.manager.generalManager import GeneralManagerMeta, GeneralManager
from generalManager.src.manager.property import GraphQLProperty

if TYPE_CHECKING:
    from generalManager.src.interface.baseInterface import InterfaceBase
    from generalManager.src.permission.basePermission import BasePermission
    from graphene import ResolveInfo as GraphQLResolveInfo


class MeasurementType(graphene.ObjectType):  # type: ignore
    value = graphene.Float()
    unit = graphene.String()


def getReadPermissionFilter(
    generalManagerClass: GeneralManagerMeta,
    info: GraphQLResolveInfo,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """
    Ermittelt die Filter, die auf Basis der read-Permission für den angegebenen
    Manager angewendet werden müssen.
    """
    filters = []
    PermissionClass: type[BasePermission] | None = getattr(
        generalManagerClass, "Permission", None
    )
    if PermissionClass:
        permission_filters = PermissionClass(
            generalManagerClass, info.context.user
        ).getPermissionFilter()
        for permission_filter in permission_filters:
            filter_dict = permission_filter.get("filter", {})
            exclude_dict = permission_filter.get("exclude", {})
            filters.append((filter_dict, exclude_dict))
    return filters


class GraphQL:
    """
    Baut die GraphQL-Oberfläche auf und erstellt Resolver-Funktionen
    dynamisch für die angegebene GeneralManager-Klasse.
    """

    _query_class: type[graphene.ObjectType] | None = None
    graphql_type_registry: dict[str, type] = {}
    graphql_filter_type_registry: dict[str, type] = {}

    @classmethod
    def createGraphqlInterface(cls, generalManagerClass: GeneralManagerMeta) -> None:
        """
        Erzeugt ein GraphQL-Interface für die übergebene Manager-Klasse.
        Dabei werden:
          - Attribute aus dem Interface in Graphene-Felder abgebildet
          - Zu jedem Feld ein Resolver generiert und hinzugefügt
          - Der neue Type in das Registry eingetragen und Queries angehängt.
        """
        interface_cls: InterfaceBase | None = getattr(
            generalManagerClass, "Interface", None
        )
        if not interface_cls:
            return

        graphene_type_name = f"{generalManagerClass.__name__}Type"
        fields: dict[str, Any] = {}

        # Felder aus dem Interface mappen
        for field_name, field_type in interface_cls.getAttributeTypes().items():
            fields[field_name] = cls._mapFieldToGraphene(field_type, field_name)
            resolver_name = f"resolve_{field_name}"
            fields[resolver_name] = cls._createResolver(field_name, field_type)

        # Zusätzliche GraphQLPropertys verarbeiten
        for attr_name, attr_value in generalManagerClass.__dict__.items():
            if isinstance(attr_value, GraphQLProperty):
                type_hints = get_args(attr_value.graphql_type_hint)
                field_type = (
                    type_hints[0]
                    if type_hints
                    else cast(type, attr_value.graphql_type_hint)
                )
                fields[attr_name] = cls._mapFieldToGraphene(field_type, attr_name)
                fields[f"resolve_{attr_name}"] = cls._createResolver(
                    attr_name, field_type
                )

        graphene_type = type(graphene_type_name, (graphene.ObjectType,), fields)
        cls.graphql_type_registry[generalManagerClass.__name__] = graphene_type
        cls._addQueriesToSchema(graphene_type, generalManagerClass)

    @staticmethod
    def _sortByOptions(
        generalManagerClass: GeneralManagerMeta,
    ) -> type[graphene.Enum]:
        """
        Erzeugt ein Enum für Sortieroptionen basierend auf den Attributstypen der
        Manager-Klasse.
        """
        sort_options = []
        for (
            field_name,
            field_type,
        ) in generalManagerClass.Interface.getAttributeTypes().items():
            if issubclass(field_type, GeneralManager):
                continue
            elif issubclass(field_type, Measurement):
                sort_options.append(f"{field_name}_value")
                sort_options.append(f"{field_name}_unit")
            else:
                sort_options.append(field_name)

        return type(
            f"{generalManagerClass.__name__}SortByOptions",
            (graphene.Enum,),
            {option: option for option in sort_options},
        )

    @staticmethod
    def _createFilterOptions(field_name: str, field_type: GeneralManagerMeta) -> type:
        """
        Baut dynamisch ein InputObjectType für Filteroptionen auf.
        """
        number_options = ["exact", "gt", "gte", "lt", "lte"]
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
        for attr_name, attr_type in field_type.Interface.getAttributeTypes().items():
            if issubclass(attr_type, GeneralManager):
                continue
            elif issubclass(attr_type, Measurement):
                filter_fields[f"{attr_name}_value"] = graphene.Float()
                filter_fields[f"{attr_name}_unit"] = graphene.String()
                for option in number_options:
                    filter_fields[f"{attr_name}_value__{option}"] = graphene.Float()
                    filter_fields[f"{attr_name}_unit__{option}"] = graphene.String()
            else:
                filter_fields[attr_name] = GraphQL._mapFieldToGraphene(
                    attr_type, attr_name
                )
                if issubclass(attr_type, (int, float, Decimal, date, datetime)):
                    for option in number_options:
                        filter_fields[f"{attr_name}__{option}"] = (
                            GraphQL._mapFieldToGraphene(attr_type, attr_name)
                        )
                elif issubclass(attr_type, str):
                    for option in string_options:
                        filter_fields[f"{attr_name}__{option}"] = (
                            GraphQL._mapFieldToGraphene(attr_type, attr_name)
                        )

        filter_class = type(
            graphene_filter_type_name,
            (graphene.InputObjectType,),
            filter_fields,
        )
        GraphQL.graphql_filter_type_registry[graphene_filter_type_name] = filter_class
        return filter_class

    @staticmethod
    def _mapFieldToGraphene(field_type: type, field_name: str) -> Any:
        """
        Ordnet einen Python-Typ einem entsprechenden Graphene-Feld zu.
        """
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
                filter_options = GraphQL._createFilterOptions(field_name, field_type)
                sort_by_options = GraphQL._sortByOptions(field_type)
                return graphene.List(
                    lambda: GraphQL.graphql_type_registry[field_type.__name__],
                    filter=filter_options(),
                    exclude=filter_options(),
                    sort_by=sort_by_options(),
                    reverse=graphene.Boolean(),
                    page=graphene.Int(),
                    page_size=graphene.Int(),
                )
            return graphene.Field(
                lambda: GraphQL.graphql_type_registry[field_type.__name__]
            )
        else:
            return graphene.String()

    @staticmethod
    def _parseInput(input_val: dict[str, Any] | str | None) -> dict[str, Any]:
        """
        Wandelt einen als JSON-String oder Dict gelieferten Filter/Exclude-Parameter in ein Dict um.
        """
        if input_val is None:
            return {}
        if isinstance(input_val, str):
            try:
                return json.loads(input_val)
            except Exception:
                return {}
        return input_val

    @staticmethod
    def _applyQueryParameters(
        queryset: Any,
        filter_input: dict[str, Any] | str | None,
        exclude_input: dict[str, Any] | str | None,
        sort_by: graphene.Enum | None,
        reverse: bool,
        page: int | None,
        page_size: int | None,
    ) -> Any:
        """
        Wendet Filter, Excludes, Sortierung und Paginierung auf das Queryset an.
        """
        filters = GraphQL._parseInput(filter_input)
        if filters:
            queryset = queryset.filter(**filters)

        excludes = GraphQL._parseInput(exclude_input)
        if excludes:
            queryset = queryset.exclude(**excludes)

        if sort_by:
            sort_by_str = cast(str, getattr(sort_by, "value", sort_by))
            queryset = queryset.sort(sort_by_str, reverse=reverse)

        if page is not None or page_size is not None:
            page = page or 1
            page_size = page_size or 10
            offset = (page - 1) * page_size
            queryset = queryset[offset : offset + page_size]

        return queryset

    @staticmethod
    def _applyPermissionFilters(
        queryset: Any,
        general_manager_class: type[GeneralManager],
        info: GraphQLResolveInfo,
    ) -> Any:
        """
        Wendet die vom Permission-Interface vorgegebenen Filter auf das Queryset an.
        """
        permission_filters = getReadPermissionFilter(general_manager_class, info)
        filtered_queryset = queryset
        for perm_filter, perm_exclude in permission_filters:
            qs_perm = queryset.exclude(**perm_exclude).filter(**perm_filter)
            filtered_queryset = filtered_queryset | qs_perm

        return filtered_queryset

    @staticmethod
    def _checkReadPermission(
        instance: GeneralManager, info: GraphQLResolveInfo, field_name: str
    ) -> bool:
        """
        Überprüft, ob der Benutzer Lesezugriff auf das jeweilige Feld hat.
        """
        PermissionClass: type[BasePermission] | None = getattr(
            instance, "Permission", None
        )
        if PermissionClass:
            return PermissionClass(instance, info.context.user).checkPermission(
                "read", field_name
            )
        return True

    @staticmethod
    def _createListResolver(
        base_getter: Callable[[Any], Any], fallback_manager_class: type[GeneralManager]
    ) -> Callable[..., Any]:
        """
        Erzeugt einen Resolver für List-Felder, der:
          - Eine Basisabfrage (base_queryset) über den base_getter ermittelt
          - Zuerst die permission-basierten Filter anwendet
          - Anschließend Filter, Excludes, Sortierung und Paginierung übernimmt
        """

        def resolver(
            self: GeneralManager,
            info: GraphQLResolveInfo,
            filter: dict[str, Any] | str | None = None,
            exclude: dict[str, Any] | str | None = None,
            sort_by: graphene.Enum | None = None,
            reverse: bool = False,
            page: int | None = None,
            page_size: int | None = None,
        ) -> Any:
            base_queryset = base_getter(self)
            # Verwende _manager_class aus dem Attribut falls vorhanden, ansonsten das Fallback
            manager_class = getattr(
                base_queryset, "_manager_class", fallback_manager_class
            )
            qs = GraphQL._applyPermissionFilters(base_queryset, manager_class, info)
            qs = GraphQL._applyQueryParameters(
                qs, filter, exclude, sort_by, reverse, page, page_size
            )
            return qs

        return resolver

    @staticmethod
    def _createMeasurementResolver(field_name: str) -> Callable[..., Any]:
        """
        Erzeugt einen Resolver für Felder vom Typ Measurement.
        """

        def resolver(
            self: GeneralManager,
            info: GraphQLResolveInfo,
            target_unit: str | None = None,
        ) -> dict[str, Any] | None:
            if not GraphQL._checkReadPermission(self, info, field_name):
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

        return resolver

    @staticmethod
    def _createNormalResolver(field_name: str) -> Callable[..., Any]:
        """
        Erzeugt einen Resolver für Standardfelder (keine Listen, keine Measurements).
        """

        def resolver(self: GeneralManager, info: GraphQLResolveInfo) -> Any:
            if not GraphQL._checkReadPermission(self, info, field_name):
                return None
            return getattr(self, field_name)

        return resolver

    @classmethod
    def _createResolver(cls, field_name: str, field_type: type) -> Callable[..., Any]:
        """
        Wählt anhand des Feldtyps den passenden Resolver aus.
        """
        if field_name.endswith("_list") and issubclass(field_type, GeneralManager):
            return cls._createListResolver(
                lambda self: getattr(self, field_name), field_type
            )
        if issubclass(field_type, Measurement):
            return cls._createMeasurementResolver(field_name)
        return cls._createNormalResolver(field_name)

    @classmethod
    def _addQueriesToSchema(
        cls, graphene_type: type, generalManagerClass: GeneralManagerMeta
    ) -> None:
        """
        Fügt dem Schema Abfragen hinzu (Liste und Einzelobjekt) basierend auf der
        GeneralManager-Klasse.
        """
        if not issubclass(generalManagerClass, GeneralManager):
            raise TypeError(
                "generalManagerClass must be a subclass of GeneralManager to create a GraphQL interface"
            )

        if not hasattr(cls, "_query_fields"):
            cls._query_fields: dict[str, Any] = {}

        # Resolver und Feld für die Listenabfrage
        list_field_name = f"{generalManagerClass.__name__.lower()}_list"
        filter_options = cls._createFilterOptions(
            generalManagerClass.__name__.lower(), generalManagerClass
        )
        sort_by_options = cls._sortByOptions(generalManagerClass)
        list_field = graphene.List(
            graphene_type,
            filter=filter_options(),
            exclude=filter_options(),
            sort_by=sort_by_options(),
            reverse=graphene.Boolean(),
            page=graphene.Int(),
            page_size=graphene.Int(),
        )

        list_resolver = cls._createListResolver(
            lambda self: generalManagerClass.all(), generalManagerClass
        )
        cls._query_fields[list_field_name] = list_field
        cls._query_fields[f"resolve_{list_field_name}"] = list_resolver

        # Resolver und Feld für die Einzelobjektabfrage
        item_field_name = generalManagerClass.__name__.lower()
        identification_fields = {}
        for (
            input_field_name,
            input_field,
        ) in generalManagerClass.Interface.input_fields.items():
            if issubclass(input_field.type, GeneralManager):
                key = f"{input_field_name}_id"
                identification_fields[key] = graphene.Int(required=True)
            elif input_field_name == "id":
                identification_fields[input_field_name] = graphene.ID(required=True)
            else:
                identification_fields[input_field_name] = cls._mapFieldToGraphene(
                    input_field.type, input_field_name
                )
                identification_fields[input_field_name].required = True

        item_field = graphene.Field(graphene_type, **identification_fields)

        def resolver(
            self: GeneralManager, info: GraphQLResolveInfo, **identification: dict
        ) -> GeneralManager:
            return generalManagerClass(**identification)

        cls._query_fields[item_field_name] = item_field
        cls._query_fields[f"resolve_{item_field_name}"] = resolver
