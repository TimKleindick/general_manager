"""GraphQL schema utilities for exposing GeneralManager models via Graphene."""

from __future__ import annotations

import ast as py_ast
import asyncio
from contextlib import suppress
import json
from dataclasses import dataclass
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import hashlib
from types import UnionType
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Generator,
    Iterable,
    TYPE_CHECKING,
    Type,
    Union,
    cast,
    get_args,
    get_origin,
)

import graphene  # type: ignore[import]
from graphql.language import ast
from graphql.language.ast import FieldNode, FragmentSpreadNode, InlineFragmentNode, SelectionSetNode
from asgiref.sync import async_to_sync
from channels.layers import BaseChannelLayer, get_channel_layer

from general_manager.cache.cacheTracker import DependencyTracker
from general_manager.cache.dependencyIndex import Dependency
from general_manager.cache.signals import post_data_change
from general_manager.bucket.baseBucket import Bucket
from general_manager.interface.baseInterface import InterfaceBase
from general_manager.manager.generalManager import GeneralManager
from general_manager.measurement.measurement import Measurement

from django.core.exceptions import ValidationError
from django.db.models import NOT_PROVIDED
from graphql import GraphQLError


if TYPE_CHECKING:
    from general_manager.permission.basePermission import BasePermission
    from graphene import ResolveInfo as GraphQLResolveInfo


@dataclass(slots=True)
class SubscriptionEvent:
    """Payload delivered to GraphQL subscription resolvers."""

    item: Any | None
    action: str


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
    generalManagerClass: Type[GeneralManager],
    info: GraphQLResolveInfo,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """
    Return permission-derived filter and exclude pairs for the given manager class.

    Parameters:
        generalManagerClass (Type[GeneralManager]): Manager class being queried.
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
    _subscription_class: type[graphene.ObjectType] | None = None
    _mutations: dict[str, Any] = {}
    _query_fields: dict[str, Any] = {}
    _subscription_fields: dict[str, Any] = {}
    _page_type_registry: dict[str, type[graphene.ObjectType]] = {}
    _subscription_payload_registry: dict[str, type[graphene.ObjectType]] = {}
    graphql_type_registry: dict[str, type] = {}
    graphql_filter_type_registry: dict[str, type] = {}
    manager_registry: dict[str, type[GeneralManager]] = {}
    _schema: graphene.Schema | None = None

    @staticmethod
    def _get_channel_layer(strict: bool = False) -> BaseChannelLayer | None:
        layer = cast(BaseChannelLayer | None, get_channel_layer())
        if layer is None and strict:
            raise RuntimeError(
                "No channel layer configured. Configure CHANNEL_LAYERS to enable GraphQL subscriptions."
            )
        return layer

    @classmethod
    def get_schema(cls) -> graphene.Schema | None:
        """
        Return the currently configured Graphene schema, if any.

        Returns:
            graphene.Schema | None: Active schema instance when GraphQL is initialised, otherwise ``None``.
        """
        return cls._schema

    @staticmethod
    def _group_name(
        manager_class: type[GeneralManager], identification: dict[str, Any]
    ) -> str:
        normalized = json.dumps(identification, sort_keys=True, default=str)
        digest = hashlib.sha256(
            f"{manager_class.__module__}.{manager_class.__name__}:{normalized}".encode(
                "utf-8"
            )
        ).hexdigest()[:32]
        return f"gm_subscriptions.{manager_class.__name__}.{digest}"

    @staticmethod
    async def _channel_listener(
        channel_layer: BaseChannelLayer,
        channel_name: str,
        queue: asyncio.Queue[str],
    ) -> None:
        try:
            while True:
                message = cast(dict[str, Any], await channel_layer.receive(channel_name))
                if message.get("type") != "gm.subscription.event":
                    continue
                action = cast(str | None, message.get("action"))
                if action is not None:
                    await queue.put(action)
        except asyncio.CancelledError:
            pass

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
    def createGraphqlInterface(cls, generalManagerClass: Type[GeneralManager]) -> None:
        """
        Build and register a Graphene ``ObjectType`` for the supplied manager class.

        Parameters:
            generalManagerClass (Type[GeneralManager]): Manager class whose attributes drive field generation.
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
        cls.manager_registry[generalManagerClass.__name__] = generalManagerClass
        cls._addQueriesToSchema(graphene_type, generalManagerClass)
        cls._addSubscriptionField(graphene_type, generalManagerClass)

    @staticmethod
    def _sortByOptions(
        generalManagerClass: Type[GeneralManager],
    ) -> type[graphene.Enum] | None:
        """
        Build an enum of sortable fields for the provided manager class.

        Parameters:
            generalManagerClass (Type[GeneralManager]): Manager class being inspected.

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
        field_type: Type[GeneralManager],
    ) -> type[graphene.InputObjectType] | None:
        """
        Create a Graphene ``InputObjectType`` for filters on ``field_type``.

        Parameters:
            field_type (Type[GeneralManager]): Manager class whose attributes drive filter generation.

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
    def _buildIdentificationArguments(
        cls, generalManagerClass: Type[GeneralManager]
    ) -> dict[str, Any]:
        """
        Construct GraphQL arguments that uniquely identify an item of ``generalManagerClass``.
        """
        identification_fields: dict[str, Any] = {}
        for (
            input_field_name,
            input_field,
        ) in generalManagerClass.Interface.input_fields.items():
            if issubclass(input_field.type, GeneralManager):
                key = f"{input_field_name}_id"
                identification_fields[key] = graphene.ID(required=True)
            elif input_field_name == "id":
                identification_fields[input_field_name] = graphene.ID(required=True)
            else:
                argument_field = cls._mapFieldToGrapheneRead(
                    input_field.type, input_field_name
                )
                argument_field.required = True
                identification_fields[input_field_name] = argument_field
        return identification_fields

    @classmethod
    def _addQueriesToSchema(
        cls, graphene_type: type, generalManagerClass: Type[GeneralManager]
    ) -> None:
        """
        Register list and detail query fields for ``generalManagerClass``.

        Parameters:
            graphene_type (type): Graphene ``ObjectType`` representing the manager.
            generalManagerClass (Type[GeneralManager]): Manager class being exposed.
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
        identification_fields = cls._buildIdentificationArguments(generalManagerClass)
        item_field = graphene.Field(graphene_type, **identification_fields)

        def resolver(
            self: GeneralManager, info: GraphQLResolveInfo, **identification: dict
        ) -> GeneralManager:
            return generalManagerClass(**identification)

        cls._query_fields[item_field_name] = item_field
        cls._query_fields[f"resolve_{item_field_name}"] = resolver

    @staticmethod
    def _prime_graphql_properties(
        instance: GeneralManager, property_names: Iterable[str] | None = None
    ) -> None:
        """
        Eagerly resolve GraphQLProperty attributes to capture dependency metadata.
        """
        interface_cls = getattr(instance.__class__, "Interface", None)
        if interface_cls is None:
            return
        available_properties = interface_cls.getGraphQLProperties()
        if property_names is None:
            names = available_properties.keys()
        else:
            names = [name for name in property_names if name in available_properties]
        for prop_name in names:
            getattr(instance, prop_name)

    @classmethod
    def _dependencies_from_tracker(
        cls, dependency_records: Iterable[Dependency]
    ) -> list[tuple[type[GeneralManager], dict[str, Any]]]:
        """
        Convert DependencyTracker records into manager/identification tuples.
        """
        resolved: list[tuple[type[GeneralManager], dict[str, Any]]] = []
        for manager_name, operation, identifier in dependency_records:
            if operation != "identification":
                continue
            manager_class = cls.manager_registry.get(manager_name)
            if manager_class is None:
                continue
            try:
                parsed = py_ast.literal_eval(identifier)
            except (ValueError, SyntaxError):
                continue
            if not isinstance(parsed, dict):
                continue
            resolved.append((manager_class, parsed))
        return resolved

    @classmethod
    def _subscription_property_names(
        cls,
        info: GraphQLResolveInfo,
        manager_class: type[GeneralManager],
    ) -> set[str]:
        """
        Return GraphQLProperty names referenced under the subscription payload item.
        """
        interface_cls = getattr(manager_class, "Interface", None)
        if interface_cls is None:
            return set()
        available_properties = set(interface_cls.getGraphQLProperties().keys())
        if not available_properties:
            return set()

        property_names: set[str] = set()

        def collect_from_selection(selection_set: SelectionSetNode | None) -> None:
            if selection_set is None:
                return
            for selection in selection_set.selections:
                if isinstance(selection, FieldNode):
                    name = selection.name.value
                    if name in available_properties:
                        property_names.add(name)
                elif isinstance(selection, FragmentSpreadNode):
                    fragment = info.fragments.get(selection.name.value)
                    if fragment is not None:
                        collect_from_selection(fragment.selection_set)
                elif isinstance(selection, InlineFragmentNode):
                    collect_from_selection(selection.selection_set)

        def inspect_selection_set(selection_set: SelectionSetNode | None) -> None:
            if selection_set is None:
                return
            for selection in selection_set.selections:
                if isinstance(selection, FieldNode):
                    if selection.name.value == "item":
                        collect_from_selection(selection.selection_set)
                    else:
                        inspect_selection_set(selection.selection_set)
                elif isinstance(selection, FragmentSpreadNode):
                    fragment = info.fragments.get(selection.name.value)
                    if fragment is not None:
                        inspect_selection_set(fragment.selection_set)
                elif isinstance(selection, InlineFragmentNode):
                    inspect_selection_set(selection.selection_set)

        for node in info.field_nodes:
            inspect_selection_set(node.selection_set)
        return property_names

    @classmethod
    def _resolve_subscription_dependencies(
        cls,
        manager_class: type[GeneralManager],
        instance: GeneralManager,
        dependency_records: Iterable[Dependency] | None = None,
    ) -> list[tuple[type[GeneralManager], dict[str, Any]]]:
        """
        Derive dependency definitions for calculation subscriptions based on manager inputs.
        """
        dependencies: list[tuple[type[GeneralManager], dict[str, Any]]] = []
        seen: set[tuple[str, str]] = set()
        if dependency_records:
            for dependency_class, dependency_identification in cls._dependencies_from_tracker(
                dependency_records
            ):
                if (
                    dependency_class is manager_class
                    and dependency_identification == instance.identification
                ):
                    continue
                key = (dependency_class.__name__, repr(dependency_identification))
                if key in seen:
                    continue
                seen.add(key)
                dependencies.append((dependency_class, dependency_identification))
        interface_cls = manager_class.Interface

        for (
            input_name,
            input_field,
        ) in interface_cls.input_fields.items():
            if not issubclass(input_field.type, GeneralManager):
                continue

            raw_value = instance._interface.identification.get(input_name)
            if raw_value is None:
                continue

            values = raw_value if isinstance(raw_value, list) else [raw_value]
            for value in values:
                if isinstance(value, GeneralManager):
                    identification = deepcopy(value.identification)
                    key = (input_field.type.__name__, repr(identification))
                    if key in seen:
                        continue
                    seen.add(key)
                    dependencies.append(
                        (
                            cast(type[GeneralManager], input_field.type),
                            identification,
                        )
                    )
                elif isinstance(value, dict):
                    identification_dict = deepcopy(cast(dict[str, Any], value))
                    key = (input_field.type.__name__, repr(identification_dict))
                    if key in seen:
                        continue
                    seen.add(key)
                    dependencies.append(
                        (
                            cast(type[GeneralManager], input_field.type),
                            identification_dict,
                        )
                    )

        return dependencies

    @staticmethod
    def _instantiate_manager(
        manager_class: type[GeneralManager],
        identification: dict[str, Any],
        *,
        collect_dependencies: bool = False,
        property_names: Iterable[str] | None = None,
    ) -> tuple[GeneralManager, set[Dependency]]:
        """
        Helper used by async subscriptions to create manager instances in a worker thread.
        """
        if collect_dependencies:
            with DependencyTracker() as captured_dependencies:
                instance = manager_class(**identification)
                GraphQL._prime_graphql_properties(instance, property_names)
            return instance, captured_dependencies

        instance = manager_class(**identification)
        return instance, set()

    @classmethod
    def _addSubscriptionField(
        cls, graphene_type: type[graphene.ObjectType], generalManagerClass: Type[GeneralManager]
    ) -> None:
        """
        Register GraphQL subscription fields notifying about changes for ``generalManagerClass``.
        """
        field_name = f"on_{generalManagerClass.__name__.lower()}_change"
        if field_name in cls._subscription_fields:
            return

        payload_type = cls._subscription_payload_registry.get(
            generalManagerClass.__name__
        )
        if payload_type is None:
            payload_type = type(
                f"{generalManagerClass.__name__}SubscriptionEvent",
                (graphene.ObjectType,),
                {
                    "item": graphene.Field(graphene_type),
                    "action": graphene.String(required=True),
                },
            )
            cls._subscription_payload_registry[
                generalManagerClass.__name__
            ] = payload_type

        identification_args = cls._buildIdentificationArguments(generalManagerClass)
        subscription_field = graphene.Field(payload_type, **identification_args)

        async def subscribe(
            _root: Any,
            info: GraphQLResolveInfo,
            **identification: Any,
        ) -> AsyncIterator[SubscriptionEvent]:
            identification_copy = deepcopy(identification)
            property_names = cls._subscription_property_names(
                info, cast(type[GeneralManager], generalManagerClass)
            )
            try:
                instance, dependency_records = await asyncio.to_thread(
                    cls._instantiate_manager,
                    cast(type[GeneralManager], generalManagerClass),
                    identification_copy,
                    collect_dependencies=True,
                    property_names=property_names,
                )
            except Exception as exc:  # pragma: no cover - bubbled to GraphQL
                raise GraphQLError(str(exc)) from exc

            try:
                channel_layer = cls._get_channel_layer(strict=True)
                if channel_layer is None:
                    raise RuntimeError("Channel layer is not configured")
            except RuntimeError as exc:
                raise GraphQLError(str(exc)) from exc
            channel_name = cast(str, await channel_layer.new_channel())
            queue: asyncio.Queue[str] = asyncio.Queue[str]()

            group_names = {
                cls._group_name(
                    cast(type[GeneralManager], generalManagerClass),
                    instance.identification,
                )
            }
            dependencies = cls._resolve_subscription_dependencies(
                cast(type[GeneralManager], generalManagerClass),
                instance,
                dependency_records,
            )
            for dependency_class, dependency_identification in dependencies:
                group_names.add(
                    cls._group_name(dependency_class, dependency_identification)
                )

            for group in group_names:
                await channel_layer.group_add(group, channel_name)

            listener_task = asyncio.create_task(
                cls._channel_listener(channel_layer, channel_name, queue)
            )

            async def event_stream() -> AsyncIterator[SubscriptionEvent]:
                try:
                    yield SubscriptionEvent(item=instance, action="snapshot")
                    while True:
                        action = await queue.get()
                        try:
                            item, _ = await asyncio.to_thread(
                                cls._instantiate_manager,
                                cast(type[GeneralManager], generalManagerClass),
                                identification_copy,
                                property_names=property_names,
                            )
                        except Exception:
                            item = None
                        yield SubscriptionEvent(item=item, action=action)
                finally:
                    listener_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await listener_task
                    for group in group_names:
                        await channel_layer.group_discard(group, channel_name)

            return event_stream()

        def resolve(
            payload: SubscriptionEvent,
            info: GraphQLResolveInfo,
            **_: Any,
        ) -> SubscriptionEvent:
            return payload

        cls._subscription_fields[field_name] = subscription_field
        cls._subscription_fields[f"subscribe_{field_name}"] = subscribe
        cls._subscription_fields[f"resolve_{field_name}"] = resolve

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

    @classmethod
    def _handle_data_change(
        cls,
        sender: type[GeneralManager] | GeneralManager,
        instance: GeneralManager | None,
        action: str,
        **_: Any,
    ) -> None:
        """
        Dispatch subscription updates for managers participating in GraphQL subscriptions.
        """
        if instance is None or not isinstance(instance, GeneralManager):
            return

        if isinstance(sender, type) and issubclass(sender, GeneralManager):
            manager_class: type[GeneralManager] = sender
        else:
            manager_class = instance.__class__

        if manager_class.__name__ not in cls.manager_registry:
            return

        channel_layer = cls._get_channel_layer()
        if channel_layer is None:
            return

        group_name = cls._group_name(manager_class, instance.identification)
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "gm.subscription.event",
                "action": action,
            },
        )


post_data_change.connect(GraphQL._handle_data_change, weak=False)
