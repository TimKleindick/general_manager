"""GraphQL schema utilities for exposing GeneralManager models via Graphene."""

from __future__ import annotations
import graphene  # type: ignore[import]
from typing import (
    Any,
    Callable,
    get_args,
    get_origin,
    TYPE_CHECKING,
    cast,
    Type,
    Generator,
    Union,
)
from types import UnionType

from decimal import Decimal
from datetime import date, datetime
import json
from graphql.language import ast

from general_manager.measurement.measurement import Measurement
from general_manager.manager.generalManager import GeneralManagerMeta, GeneralManager
from general_manager.api.property import GraphQLProperty
from general_manager.bucket.baseBucket import Bucket
from general_manager.interface.baseInterface import InterfaceBase
from django.db.models import NOT_PROVIDED
from django.core.exceptions import ValidationError

from graphql import GraphQLError


if TYPE_CHECKING:
    from general_manager.permission.basePermission import BasePermission
    from graphene import ResolveInfo as GraphQLResolveInfo


class MeasurementType(graphene.ObjectType):
    value = graphene.Float()
    unit = graphene.String()


class MeasurementScalar(graphene.Scalar):
    """
    A measurement in format "value unit", e.g. "12.5 m/s".
    """

    @staticmethod
    def serialize(value: Measurement) -> str:
        if not isinstance(value, Measurement):
            raise TypeError(f"Expected Measurement, got {type(value)}")
        return str(value)

    @staticmethod
    def parse_value(value: str) -> Measurement:
        return Measurement.from_string(value)

    @staticmethod
    def parse_literal(node: Any) -> Measurement | None:
        if isinstance(node, ast.StringValueNode):
            return Measurement.from_string(node.value)
        return None


class PageInfo(graphene.ObjectType):
    total_count = graphene.Int(required=True)
    page_size = graphene.Int(required=False)
    current_page = graphene.Int(required=True)
    total_pages = graphene.Int(required=True)


def getReadPermissionFilter(
    generalManagerClass: GeneralManagerMeta,
    info: GraphQLResolveInfo,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """
    Return permission-derived filter and exclude pairs for the given manager class.

    Parameters:
        generalManagerClass (GeneralManagerMeta): Manager class being queried.
        info (GraphQLResolveInfo): GraphQL resolver info containing the request user.

    Returns:
        list[tuple[dict[str, Any], dict[str, Any]]]: List of ``(filter, exclude)`` mappings.
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
    """Static helper that builds GraphQL types, queries, and mutations for managers."""

    _query_class: type[graphene.ObjectType] | None = None
    _mutation_class: type[graphene.ObjectType] | None = None
    _mutations: dict[str, Any] = {}
    _query_fields: dict[str, Any] = {}
    _page_type_registry: dict[str, type[graphene.ObjectType]] = {}
    graphql_type_registry: dict[str, type] = {}
    graphql_filter_type_registry: dict[str, type] = {}

    @classmethod
    def createGraphqlMutation(cls, generalManagerClass: type[GeneralManager]) -> None:
        """
        Register create, update, and delete mutations for ``generalManagerClass``.

        Parameters:
            generalManagerClass (type[GeneralManager]): Manager class whose interface drives mutation generation.
        """

        interface_cls: InterfaceBase | None = getattr(
            generalManagerClass, "Interface", None
        )
        if not interface_cls:
            return None

        default_return_values = {
            "success": graphene.Boolean(),
            generalManagerClass.__name__: graphene.Field(
                lambda: GraphQL.graphql_type_registry[generalManagerClass.__name__]
            ),
        }
        if InterfaceBase.create.__code__ != interface_cls.create.__code__:
            create_name = f"create{generalManagerClass.__name__}"
            cls._mutations[create_name] = cls.generateCreateMutationClass(
                generalManagerClass, default_return_values
            )

        if InterfaceBase.update.__code__ != interface_cls.update.__code__:
            update_name = f"update{generalManagerClass.__name__}"
            cls._mutations[update_name] = cls.generateUpdateMutationClass(
                generalManagerClass, default_return_values
            )

        if InterfaceBase.deactivate.__code__ != interface_cls.deactivate.__code__:
            delete_name = f"delete{generalManagerClass.__name__}"
            cls._mutations[delete_name] = cls.generateDeleteMutationClass(
                generalManagerClass, default_return_values
            )

    @classmethod
    def createGraphqlInterface(cls, generalManagerClass: GeneralManagerMeta) -> None:
        """
        Build and register a Graphene ``ObjectType`` for the supplied manager class.

        Parameters:
            generalManagerClass (GeneralManagerMeta): Manager class whose attributes drive field generation.
        """
        interface_cls: InterfaceBase | None = getattr(
            generalManagerClass, "Interface", None
        )
        if not interface_cls:
            return None

        graphene_type_name = f"{generalManagerClass.__name__}Type"
        fields: dict[str, Any] = {}

        # Map Attribute Types to Graphene Fields
        for field_name, field_info in interface_cls.getAttributeTypes().items():
            field_type = field_info["type"]
            fields[field_name] = cls._mapFieldToGrapheneRead(field_type, field_name)
            resolver_name = f"resolve_{field_name}"
            fields[resolver_name] = cls._createResolver(field_name, field_type)

        # handle GraphQLProperty attributes
        for (
            attr_name,
            attr_value,
        ) in generalManagerClass.Interface.getGraphQLProperties().items():
            raw_hint = attr_value.graphql_type_hint
            origin = get_origin(raw_hint)
            type_args = [t for t in get_args(raw_hint) if t is not type(None)]

            if origin in (Union, UnionType) and type_args:
                raw_hint = type_args[0]
                origin = get_origin(raw_hint)
                type_args = [t for t in get_args(raw_hint) if t is not type(None)]

            if origin in (list, tuple, set):
                element = type_args[0] if type_args else Any
                if isinstance(element, type) and issubclass(element, GeneralManager):  # type: ignore
                    graphene_field = graphene.List(
                        lambda elem=element: GraphQL.graphql_type_registry[
                            elem.__name__
                        ]
                    )
                else:
                    base_type = GraphQL._mapFieldToGrapheneBaseType(
                        cast(type, element if isinstance(element, type) else str)
                    )
                    graphene_field = graphene.List(base_type)
                resolved_type = cast(
                    type, element if isinstance(element, type) else str
                )
            else:
                resolved_type = (
                    cast(type, type_args[0]) if type_args else cast(type, raw_hint)
                )
                graphene_field = cls._mapFieldToGrapheneRead(resolved_type, attr_name)

            fields[attr_name] = graphene_field
            fields[f"resolve_{attr_name}"] = cls._createResolver(
                attr_name, resolved_type
            )

        graphene_type = type(graphene_type_name, (graphene.ObjectType,), fields)
        cls.graphql_type_registry[generalManagerClass.__name__] = graphene_type
        cls._addQueriesToSchema(graphene_type, generalManagerClass)

    @staticmethod
    def _sortByOptions(
        generalManagerClass: GeneralManagerMeta,
    ) -> type[graphene.Enum] | None:
        """
        Build an enum of sortable fields for the provided manager class.

        Parameters:
            generalManagerClass (GeneralManagerMeta): Manager class being inspected.

        Returns:
            type[graphene.Enum] | None: Enum of sortable fields, or ``None`` when no options exist.
        """
        sort_options = []
        for (
            field_name,
            field_info,
        ) in generalManagerClass.Interface.getAttributeTypes().items():
            field_type = field_info["type"]
            if issubclass(field_type, GeneralManager):
                continue
            else:
                sort_options.append(field_name)

        for (
            prop_name,
            prop,
        ) in generalManagerClass.Interface.getGraphQLProperties().items():
            if prop.sortable is False:
                continue
            type_hints = [
                t for t in get_args(prop.graphql_type_hint) if t is not type(None)
            ]
            field_type = (
                type_hints[0] if type_hints else cast(type, prop.graphql_type_hint)
            )
            sort_options.append(prop_name)

        if not sort_options:
            return None

        return type(
            f"{generalManagerClass.__name__}SortByOptions",
            (graphene.Enum,),
            {option: option for option in sort_options},
        )

    @staticmethod
    def _getFilterOptions(attribute_type: type, attribute_name: str) -> Generator[
        tuple[
            str, type[graphene.ObjectType] | MeasurementScalar | graphene.List | None
        ],
        None,
        None,
    ]:
        """
        Yield filter field names and Graphene types for a given attribute.

        Parameters:
            attribute_type (type): Python type declared for the attribute.
            attribute_name (str): Name of the attribute.

        Yields:
            tuple[str, Graphene type | None]: Filter name and corresponding Graphene type.
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

        if issubclass(attribute_type, GeneralManager):
            yield attribute_name, None
        elif issubclass(attribute_type, Measurement):
            yield attribute_name, MeasurementScalar()
            for option in number_options:
                yield f"{attribute_name}__{option}", MeasurementScalar()
        else:
            yield attribute_name, GraphQL._mapFieldToGrapheneRead(
                attribute_type, attribute_name
            )
            if issubclass(attribute_type, (int, float, Decimal, date, datetime)):
                for option in number_options:
                    yield f"{attribute_name}__{option}", (
                        GraphQL._mapFieldToGrapheneRead(attribute_type, attribute_name)
                    )
            elif issubclass(attribute_type, str):
                base_type = GraphQL._mapFieldToGrapheneBaseType(attribute_type)
                for option in string_options:
                    if option == "in":
                        yield f"{attribute_name}__in", graphene.List(base_type)
                    else:
                        yield f"{attribute_name}__{option}", (
                            GraphQL._mapFieldToGrapheneRead(
                                attribute_type, attribute_name
                            )
                        )

    @staticmethod
    def _createFilterOptions(
        field_type: GeneralManagerMeta,
    ) -> type[graphene.InputObjectType] | None:
        """
        Create a Graphene ``InputObjectType`` for filters on ``field_type``.

        Parameters:
            field_type (GeneralManagerMeta): Manager class whose attributes drive filter generation.

        Returns:
            type[graphene.InputObjectType] | None: Input type containing filter fields, or ``None`` if not applicable.
        """

        graphene_filter_type_name = f"{field_type.__name__}FilterType"
        if graphene_filter_type_name in GraphQL.graphql_filter_type_registry:
            return GraphQL.graphql_filter_type_registry[graphene_filter_type_name]

        filter_fields: dict[str, Any] = {}
        for attr_name, attr_info in field_type.Interface.getAttributeTypes().items():
            attr_type = attr_info["type"]
            filter_fields = {
                **filter_fields,
                **{
                    k: v
                    for k, v in GraphQL._getFilterOptions(attr_type, attr_name)
                    if v is not None
                },
            }
        for prop_name, prop in field_type.Interface.getGraphQLProperties().items():
            if not prop.filterable:
                continue
            hints = [t for t in get_args(prop.graphql_type_hint) if t is not type(None)]
            prop_type = hints[0] if hints else cast(type, prop.graphql_type_hint)
            filter_fields = {
                **filter_fields,
                **{
                    k: v
                    for k, v in GraphQL._getFilterOptions(prop_type, prop_name)
                    if v is not None
                },
            }

        if not filter_fields:
            return None

        filter_class = type(
            graphene_filter_type_name,
            (graphene.InputObjectType,),
            filter_fields,
        )
        GraphQL.graphql_filter_type_registry[graphene_filter_type_name] = filter_class
        return filter_class

    @staticmethod
    def _mapFieldToGrapheneRead(field_type: type, field_name: str) -> Any:
        """
        Map a field type and name to the appropriate Graphene field for reads.

        Parameters:
            field_type (type): Python type declared on the interface.
            field_name (str): Attribute name being exposed.

        Returns:
            Any: Graphene field or type configured for the attribute.
        """
        if issubclass(field_type, Measurement):
            return graphene.Field(MeasurementType, target_unit=graphene.String())
        elif issubclass(field_type, GeneralManager):
            if field_name.endswith("_list"):
                attributes = {
                    "reverse": graphene.Boolean(),
                    "page": graphene.Int(),
                    "page_size": graphene.Int(),
                    "group_by": graphene.List(graphene.String),
                }
                filter_options = GraphQL._createFilterOptions(field_type)
                if filter_options:
                    attributes["filter"] = graphene.Argument(filter_options)
                    attributes["exclude"] = graphene.Argument(filter_options)

                sort_by_options = GraphQL._sortByOptions(field_type)
                if sort_by_options:
                    attributes["sort_by"] = graphene.Argument(sort_by_options)

                page_type = GraphQL._getOrCreatePageType(
                    field_type.__name__ + "Page",
                    lambda: GraphQL.graphql_type_registry[field_type.__name__],
                )
                return graphene.Field(page_type, **attributes)

            return graphene.Field(
                lambda: GraphQL.graphql_type_registry[field_type.__name__]
            )
        else:
            return GraphQL._mapFieldToGrapheneBaseType(field_type)()

    @staticmethod
    def _mapFieldToGrapheneBaseType(field_type: type) -> Type[Any]:
        """
        Map a Python type to the corresponding Graphene scalar/class.

        Parameters:
            field_type (type): Python type declared on the interface.

        Returns:
            Type[Any]: Graphene scalar or type implementing the field.
        """
        if issubclass(field_type, dict):
            raise TypeError("GraphQL does not support dict fields")
        if issubclass(field_type, str):
            return graphene.String
        elif issubclass(field_type, bool):
            return graphene.Boolean
        elif issubclass(field_type, int):
            return graphene.Int
        elif issubclass(field_type, (float, Decimal)):
            return graphene.Float
        elif issubclass(field_type, datetime):
            return graphene.DateTime
        elif issubclass(field_type, date):
            return graphene.Date
        elif issubclass(field_type, Measurement):
            return MeasurementScalar
        else:
            return graphene.String

    @staticmethod
    def _parseInput(input_val: dict[str, Any] | str | None) -> dict[str, Any]:
        """
        Normalise filter/exclude input into a dictionary.

        Parameters:
            input_val (dict[str, Any] | str | None): Raw filter/exclude value.

        Returns:
            dict[str, Any]: Parsed dictionary suitable for queryset filtering.
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
        queryset: Bucket[GeneralManager],
        filter_input: dict[str, Any] | str | None,
        exclude_input: dict[str, Any] | str | None,
        sort_by: graphene.Enum | None,
        reverse: bool,
    ) -> Bucket[GeneralManager]:
        """
        Apply filtering, exclusion, and sorting to a queryset based on provided parameters.

        Parameters:
            filter_input: Filters to apply, as a dictionary or JSON string.
            exclude_input: Exclusions to apply, as a dictionary or JSON string.
            sort_by: Field to sort by, as a Graphene Enum value.
            reverse: If True, reverses the sort order.

        Returns:
            The queryset after applying filters, exclusions, and sorting.
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

        return queryset

    @staticmethod
    def _applyPermissionFilters(
        queryset: Bucket,
        general_manager_class: type[GeneralManager],
        info: GraphQLResolveInfo,
    ) -> Bucket:
        """
        Apply permission-based filters to ``queryset`` for the current user.

        Parameters:
            queryset (Bucket): Queryset being filtered.
            general_manager_class (type[GeneralManager]): Manager class providing permissions.
            info (GraphQLResolveInfo): Resolver info containing the request user.

        Returns:
            Bucket: Queryset constrained by read permissions.
        """
        permission_filters = getReadPermissionFilter(general_manager_class, info)
        if not permission_filters:
            return queryset

        filtered_queryset = queryset.none()
        for perm_filter, perm_exclude in permission_filters:
            qs_perm = queryset.exclude(**perm_exclude).filter(**perm_filter)
            filtered_queryset = filtered_queryset | qs_perm

        return filtered_queryset

    @staticmethod
    def _checkReadPermission(
        instance: GeneralManager, info: GraphQLResolveInfo, field_name: str
    ) -> bool:
        """Return True if the user may read ``field_name`` on ``instance``."""
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
        Build a resolver for list fields applying filters, permissions, and paging.

        Parameters:
            base_getter (Callable[[Any], Any]): Callable returning the base queryset.
            fallback_manager_class (type[GeneralManager]): Manager used when ``base_getter`` returns ``None``.

        Returns:
            Callable[..., Any]: Resolver function compatible with Graphene.
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
            group_by: list[str] | None = None,
        ) -> dict[str, Any]:
            """
            Resolves a list field by returning filtered, excluded, sorted, grouped, and paginated results with permission checks.

            Parameters:
                filter: Filter criteria as a dictionary or JSON string.
                exclude: Exclusion criteria as a dictionary or JSON string.
                sort_by: Field to sort by, as a Graphene Enum.
                reverse: If True, reverses the sort order.
                page: Page number for pagination.
                page_size: Number of items per page.
                group_by: List of field names to group results by.

            Returns:
                A dictionary containing the paginated items under "items" and pagination metadata under "pageInfo".
            """
            base_queryset = base_getter(self)
            # use _manager_class from the attribute if available, otherwise fallback
            manager_class = getattr(
                base_queryset, "_manager_class", fallback_manager_class
            )
            qs = GraphQL._applyPermissionFilters(base_queryset, manager_class, info)
            qs = GraphQL._applyQueryParameters(qs, filter, exclude, sort_by, reverse)
            qs = GraphQL._applyGrouping(qs, group_by)

            total_count = len(qs)

            qs_paginated = GraphQL._applyPagination(qs, page, page_size)

            page_info = {
                "total_count": total_count,
                "page_size": page_size,
                "current_page": page or 1,
                "total_pages": (
                    ((total_count + page_size - 1) // page_size) if page_size else 1
                ),
            }
            return {
                "items": qs_paginated,
                "pageInfo": page_info,
            }

        return resolver

    @staticmethod
    def _applyPagination(
        queryset: Bucket[GeneralManager], page: int | None, page_size: int | None
    ) -> Bucket[GeneralManager]:
        """
        Returns a paginated subset of the queryset based on the given page number and page size.

        If neither `page` nor `page_size` is provided, the entire queryset is returned. Defaults to page 1 and page size 10 if only one parameter is specified.

        Parameters:
            page (int | None): The page number to retrieve (1-based).
            page_size (int | None): The number of items per page.

        Returns:
            Bucket[GeneralManager]: The paginated queryset.
        """
        if page is not None or page_size is not None:
            page = page or 1
            page_size = page_size or 10
            offset = (page - 1) * page_size
            queryset = cast(Bucket, queryset[offset : offset + page_size])
        return queryset

    @staticmethod
    def _applyGrouping(
        queryset: Bucket[GeneralManager], group_by: list[str] | None
    ) -> Bucket[GeneralManager]:
        """
        Groups the queryset by the specified fields.

        If `group_by` is `[""]`, groups by all default fields. If `group_by` is a list of field names, groups by those fields. Returns the grouped queryset.
        """
        if group_by is not None:
            if group_by == [""]:
                queryset = queryset.group_by()
            else:
                queryset = queryset.group_by(*group_by)
        return queryset

    @staticmethod
    def _createMeasurementResolver(field_name: str) -> Callable[..., Any]:
        """
        Creates a resolver for a Measurement field that returns its value and unit, with optional unit conversion.

        The generated resolver checks read permissions for the specified field. If permitted and the field is a Measurement, it returns a dictionary containing the measurement's value and unit, converting to the specified target unit if provided. Returns None if permission is denied or the field is not a Measurement.
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
                "unit": str(result.quantity.units),
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
        Returns a resolver function for a field, selecting list, measurement, or standard resolution based on the field's type and name.

        For fields ending with `_list` referencing a `GeneralManager` subclass, provides a resolver supporting pagination and filtering. For `Measurement` fields, returns a resolver that handles unit conversion and permission checks. For all other fields, returns a standard resolver with permission enforcement.
        """
        if field_name.endswith("_list") and issubclass(field_type, GeneralManager):
            return cls._createListResolver(
                lambda self: getattr(self, field_name), field_type
            )
        if issubclass(field_type, Measurement):
            return cls._createMeasurementResolver(field_name)
        return cls._createNormalResolver(field_name)

    @classmethod
    def _getOrCreatePageType(
        cls,
        page_type_name: str,
        item_type: type[graphene.ObjectType] | Callable[[], type[graphene.ObjectType]],
    ) -> type[graphene.ObjectType]:
        """
        Returns a paginated GraphQL ObjectType for the specified item type, creating and caching it if it does not already exist.

        The returned ObjectType includes an `items` field (a required list of the item type) and a `pageInfo` field (pagination metadata).
        """
        if page_type_name not in cls._page_type_registry:
            cls._page_type_registry[page_type_name] = type(
                page_type_name,
                (graphene.ObjectType,),
                {
                    "items": graphene.List(item_type, required=True),
                    "pageInfo": graphene.Field(PageInfo, required=True),
                },
            )
        return cls._page_type_registry[page_type_name]

    @classmethod
    def _addQueriesToSchema(
        cls, graphene_type: type, generalManagerClass: GeneralManagerMeta
    ) -> None:
        """
        Register list and detail query fields for ``generalManagerClass``.

        Parameters:
            graphene_type (type): Graphene ``ObjectType`` representing the manager.
            generalManagerClass (GeneralManagerMeta): Manager class being exposed.
        """
        if not issubclass(generalManagerClass, GeneralManager):
            raise TypeError(
                "generalManagerClass must be a subclass of GeneralManager to create a GraphQL interface"
            )

        if not hasattr(cls, "_query_fields"):
            cls._query_fields = cast(dict[str, Any], {})

        # resolver and field for the list query
        list_field_name = f"{generalManagerClass.__name__.lower()}_list"
        attributes = {
            "reverse": graphene.Boolean(),
            "page": graphene.Int(),
            "page_size": graphene.Int(),
            "group_by": graphene.List(graphene.String),
        }
        filter_options = cls._createFilterOptions(generalManagerClass)
        if filter_options:
            attributes["filter"] = graphene.Argument(filter_options)
            attributes["exclude"] = graphene.Argument(filter_options)
        sort_by_options = cls._sortByOptions(generalManagerClass)
        if sort_by_options:
            attributes["sort_by"] = graphene.Argument(sort_by_options)

        page_type = cls._getOrCreatePageType(
            graphene_type.__name__ + "Page", graphene_type
        )
        list_field = graphene.Field(page_type, **attributes)

        list_resolver = cls._createListResolver(
            lambda self: generalManagerClass.all(), generalManagerClass
        )
        cls._query_fields[list_field_name] = list_field
        cls._query_fields[f"resolve_{list_field_name}"] = list_resolver

        # resolver and field for the single item query
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
                identification_fields[input_field_name] = cls._mapFieldToGrapheneRead(
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

    @classmethod
    def createWriteFields(cls, interface_cls: InterfaceBase) -> dict[str, Any]:
        """
        Generate Graphene input fields for writable interface attributes.

        Parameters:
            interface_cls (InterfaceBase): Interface whose attributes drive the input field map.

        Returns:
            dict[str, Any]: Mapping of attribute names to Graphene field definitions.
        """
        fields: dict[str, Any] = {}

        for name, info in interface_cls.getAttributeTypes().items():
            if name in ["changed_by", "created_at", "updated_at"]:
                continue
            if info["is_derived"]:
                continue

            typ = info["type"]
            req = info["is_required"]
            default = info["default"]

            if issubclass(typ, GeneralManager):
                if name.endswith("_list"):
                    fld = graphene.List(
                        graphene.ID,
                        required=req,
                        default_value=default,
                    )
                else:
                    fld = graphene.ID(
                        required=req,
                        default_value=default,
                    )
            else:
                base_cls = cls._mapFieldToGrapheneBaseType(typ)
                fld = base_cls(
                    required=req,
                    default_value=default,
                )

            # mark for generate* code to know what is editable
            setattr(fld, "editable", info["is_editable"])
            fields[name] = fld

        # history_comment is always optional without a default value
        fields["history_comment"] = graphene.String()
        setattr(fields["history_comment"], "editable", True)

        return fields

    @classmethod
    def generateCreateMutationClass(
        cls,
        generalManagerClass: type[GeneralManager],
        default_return_values: dict[str, Any],
    ) -> type[graphene.Mutation] | None:
        """
        Dynamically generates a Graphene mutation class for creating an instance of a specified GeneralManager subclass.

        The generated mutation class uses the manager's interface to define input arguments, filters out fields with `NOT_PROVIDED` values, and invokes the manager's `create` method with the provided data and the current user's ID. On success, it returns a dictionary with a success flag and the created instance; on failure, it raises a GraphQL error. Returns `None` if the manager class does not define an interface.

        Returns:
            The generated Graphene mutation class, or `None` if the manager class does not define an interface.
        """
        interface_cls: InterfaceBase | None = getattr(
            generalManagerClass, "Interface", None
        )
        if not interface_cls:
            return None

        def create_mutation(
            self,
            info: GraphQLResolveInfo,
            **kwargs: Any,
        ) -> dict:
            """
            Creates a new instance of the manager class using the provided arguments.

            Filters out any fields set to `NOT_PROVIDED` before invoking the creation method. Returns a dictionary with a success flag and the created instance keyed by the manager class name. If creation fails, raises a GraphQL error and returns a dictionary with `success` set to `False`.
            """
            try:
                kwargs = {
                    field_name: value
                    for field_name, value in kwargs.items()
                    if value is not NOT_PROVIDED
                }
                instance = generalManagerClass.create(
                    **kwargs, creator_id=info.context.user.id
                )
            except Exception as e:
                GraphQL._handleGraphQLError(e)
                return {
                    "success": False,
                }

            return {
                "success": True,
                generalManagerClass.__name__: instance,
            }

        return type(
            f"Create{generalManagerClass.__name__}",
            (graphene.Mutation,),
            {
                **default_return_values,
                "__doc__": f"Mutation to create {generalManagerClass.__name__}",
                "Arguments": type(
                    "Arguments",
                    (),
                    {
                        field_name: field
                        for field_name, field in cls.createWriteFields(
                            interface_cls
                        ).items()
                        if field_name not in generalManagerClass.Interface.input_fields
                    },
                ),
                "mutate": create_mutation,
            },
        )

    @classmethod
    def generateUpdateMutationClass(
        cls,
        generalManagerClass: type[GeneralManager],
        default_return_values: dict[str, Any],
    ) -> type[graphene.Mutation] | None:
        """
        Generates a GraphQL mutation class for updating an instance of a GeneralManager subclass.

        The generated mutation accepts editable fields as arguments, calls the manager's `update` method with the provided values and the current user's ID, and returns a dictionary containing a success flag and the updated instance. Returns `None` if the manager class does not define an `Interface`.

        Returns:
            The generated Graphene mutation class, or `None` if no interface is defined.
        """
        interface_cls: InterfaceBase | None = getattr(
            generalManagerClass, "Interface", None
        )
        if not interface_cls:
            return None

        def update_mutation(
            self,
            info: GraphQLResolveInfo,
            **kwargs: Any,
        ) -> dict:
            """
            Updates an instance of a GeneralManager subclass with the specified field values.

            Parameters:
                info (GraphQLResolveInfo): The GraphQL resolver context, including user and request data.
                **kwargs: Field values to update, including the required 'id' of the instance.

            Returns:
                dict: A dictionary with 'success' (bool) and the updated instance keyed by its class name.
            """
            try:
                manager_id = kwargs.pop("id", None)
                if manager_id is None:
                    raise ValueError("id is required")
                instance = generalManagerClass(id=manager_id).update(
                    creator_id=info.context.user.id, **kwargs
                )
            except Exception as e:
                GraphQL._handleGraphQLError(e)
                return {
                    "success": False,
                }

            return {
                "success": True,
                generalManagerClass.__name__: instance,
            }

        return type(
            f"Update{generalManagerClass.__name__}",
            (graphene.Mutation,),
            {
                **default_return_values,
                "__doc__": f"Mutation to update {generalManagerClass.__name__}",
                "Arguments": type(
                    "Arguments",
                    (),
                    {
                        "id": graphene.ID(required=True),
                        **{
                            field_name: field
                            for field_name, field in cls.createWriteFields(
                                interface_cls
                            ).items()
                            if field.editable
                        },
                    },
                ),
                "mutate": update_mutation,
            },
        )

    @classmethod
    def generateDeleteMutationClass(
        cls,
        generalManagerClass: type[GeneralManager],
        default_return_values: dict[str, Any],
    ) -> type[graphene.Mutation] | None:
        """
        Generates a GraphQL mutation class for deactivating (soft-deleting) an instance of a GeneralManager subclass.

        The generated mutation accepts input fields defined by the manager's interface, deactivates the specified instance using its ID, and returns a dictionary containing a success status and the deactivated instance keyed by the class name. Returns None if the manager class does not define an interface.

        Returns:
            The generated Graphene mutation class, or None if no interface is defined.
        """
        interface_cls: InterfaceBase | None = getattr(
            generalManagerClass, "Interface", None
        )
        if not interface_cls:
            return None

        def delete_mutation(
            self,
            info: GraphQLResolveInfo,
            **kwargs: Any,
        ) -> dict:
            """
            Deactivates an instance of a GeneralManager subclass and returns the operation result.

            Returns:
                dict: A dictionary with a "success" boolean and the deactivated instance keyed by its class name.
            """
            try:
                manager_id = kwargs.pop("id", None)
                if manager_id is None:
                    raise ValueError("id is required")
                instance = generalManagerClass(id=manager_id).deactivate(
                    creator_id=info.context.user.id
                )
            except Exception as e:
                GraphQL._handleGraphQLError(e)
                return {
                    "success": False,
                }

            return {
                "success": True,
                generalManagerClass.__name__: instance,
            }

        return type(
            f"Delete{generalManagerClass.__name__}",
            (graphene.Mutation,),
            {
                **default_return_values,
                "__doc__": f"Mutation to delete {generalManagerClass.__name__}",
                "Arguments": type(
                    "Arguments",
                    (),
                    {
                        field_name: field
                        for field_name, field in cls.createWriteFields(
                            interface_cls
                        ).items()
                        if field_name in generalManagerClass.Interface.input_fields
                    },
                ),
                "mutate": delete_mutation,
            },
        )

    @staticmethod
    def _handleGraphQLError(error: Exception) -> None:
        """
        Raise a ``GraphQLError`` with a code based on the exception type.

        Parameters:
            error (Exception): Exception raised during mutation execution.

        Raises:
            GraphQLError: Error with an appropriate ``extensions['code']`` value.
        """
        if isinstance(error, PermissionError):
            raise GraphQLError(
                str(error),
                extensions={
                    "code": "PERMISSION_DENIED",
                },
            )
        elif isinstance(error, (ValueError, ValidationError, TypeError)):
            raise GraphQLError(
                str(error),
                extensions={
                    "code": "BAD_USER_INPUT",
                },
            )
        else:
            raise GraphQLError(
                str(error),
                extensions={
                    "code": "INTERNAL_SERVER_ERROR",
                },
            )
