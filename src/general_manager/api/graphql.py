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
import re
from types import UnionType
from typing import (
    Any,
    AsyncIterator,
    Callable,
    ClassVar,
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
from graphql.language.ast import (
    FieldNode,
    FragmentSpreadNode,
    InlineFragmentNode,
    SelectionSetNode,
)
from asgiref.sync import async_to_sync
from channels.layers import BaseChannelLayer, get_channel_layer

from general_manager.bucket.base_bucket import Bucket
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_index import Dependency
from general_manager.cache.signals import post_data_change
from general_manager.interface.base_interface import InterfaceBase
from general_manager.logging import get_logger
from general_manager.manager.general_manager import GeneralManager
from general_manager.measurement.measurement import Measurement

from django.core.exceptions import ValidationError
from django.db.models import NOT_PROVIDED
from graphql import GraphQLError


if TYPE_CHECKING:
    from general_manager.permission.base_permission import BasePermission
    from graphene import ResolveInfo as GraphQLResolveInfo


logger = get_logger("api.graphql")


@dataclass(slots=True)
class SubscriptionEvent:
    """Payload delivered to GraphQL subscription resolvers."""

    item: Any | None
    action: str


class InvalidMeasurementValueError(TypeError):
    """Raised when a scalar receives a value that is not a Measurement instance."""

    def __init__(self, value: object) -> None:
        """
        Initialize the error raised when a scalar value is not a Measurement instance.

        Parameters:
            value (object): The value that failed validation; its type name is included in the exception message.
        """
        super().__init__(f"Expected Measurement, got {type(value).__name__}.")


class MissingChannelLayerError(RuntimeError):
    """Raised when GraphQL subscriptions run without a configured channel layer."""

    def __init__(self) -> None:
        """
        Indicates that Django Channels channel layer is not configured for GraphQL subscriptions.

        Raised when subscription functionality requires a configured channel layer (e.g., CHANNEL_LAYERS) but none is present.
        """
        super().__init__(
            "No channel layer configured. Configure CHANNEL_LAYERS to enable GraphQL subscriptions."
        )


class UnsupportedGraphQLFieldTypeError(TypeError):
    """Raised when attempting to map an unsupported Python type to GraphQL."""

    def __init__(self, field_type: type) -> None:
        """
        Exception raised when a Python `dict` type is encountered while mapping a field to GraphQL, which is not supported.

        Parameters:
            field_type (type): The offending Python type that was provided for the field. The exception message includes this type's `__name__`.
        """
        super().__init__(
            f"GraphQL does not support dict fields (received {field_type.__name__})."
        )


class InvalidGeneralManagerClassError(TypeError):
    """Raised when a non-GeneralManager subclass is used in the GraphQL registry."""

    def __init__(self, received_class: type) -> None:
        """
        Initialize the exception indicating the provided class is not a GeneralManager subclass.

        Parameters:
            received_class (type): The class that was supplied but is not a subclass of GeneralManager.
        """
        super().__init__(
            f"{received_class.__name__} must be a subclass of GeneralManager to create a GraphQL interface."
        )


class MissingManagerIdentifierError(ValueError):
    """Raised when a GraphQL mutation is missing the required manager identifier."""

    def __init__(self) -> None:
        """
        Initialize the exception indicating a required manager identifier is missing.

        This exception instance carries the default message "id is required.".
        """
        super().__init__("id is required.")


HANDLED_MANAGER_ERRORS: tuple[type[Exception], ...] = (
    PermissionError,
    ValidationError,
    ValueError,
    LookupError,
    TypeError,
    AttributeError,
    GraphQLError,
    RuntimeError,
)


class MeasurementType(graphene.ObjectType):
    value = graphene.Float()
    unit = graphene.String()


class MeasurementScalar(graphene.Scalar):
    """
    A measurement in format "value unit", e.g. "12.5 m/s".
    """

    @staticmethod
    def serialize(value: Measurement) -> str:
        """
        Convert a Measurement to its string representation.

        Parameters:
            value (Measurement): Measurement to serialize.

        Returns:
            str: String representation of the given Measurement.

        Raises:
            InvalidMeasurementValueError: If `value` is not a Measurement instance.
        """
        if not isinstance(value, Measurement):
            raise InvalidMeasurementValueError(value)
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


def get_read_permission_filter(
    generalManagerClass: Type[GeneralManager],
    info: GraphQLResolveInfo,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """
    Produce a list of permission-derived filter and exclude mappings for queries against a manager class.

    Parameters:
        generalManagerClass (Type[GeneralManager]): Manager class to derive permission filters for.
        info (GraphQLResolveInfo): GraphQL resolver info whose context provides the current user.

    Returns:
        list[tuple[dict[str, Any], dict[str, Any]]]: A list of `(filter, exclude)` tuples where each `filter` and `exclude` is a mapping of query constraints produced by the manager's Permission class.
    """
    filters = []
    PermissionClass: type[BasePermission] | None = getattr(
        generalManagerClass, "Permission", None
    )
    if PermissionClass:
        permission_filters = PermissionClass(
            generalManagerClass, info.context.user
        ).get_permission_filter()
        for permission_filter in permission_filters:
            filter_dict = permission_filter.get("filter", {})
            exclude_dict = permission_filter.get("exclude", {})
            filters.append((filter_dict, exclude_dict))
    return filters


class GraphQL:
    """Static helper that builds GraphQL types, queries, and mutations for managers."""

    _query_class: ClassVar[type[graphene.ObjectType] | None] = None
    _mutation_class: ClassVar[type[graphene.ObjectType] | None] = None
    _subscription_class: ClassVar[type[graphene.ObjectType] | None] = None
    _mutations: ClassVar[dict[str, Any]] = {}
    _query_fields: ClassVar[dict[str, Any]] = {}
    _subscription_fields: ClassVar[dict[str, Any]] = {}
    _page_type_registry: ClassVar[dict[str, type[graphene.ObjectType]]] = {}
    _subscription_payload_registry: ClassVar[dict[str, type[graphene.ObjectType]]] = {}
    graphql_type_registry: ClassVar[dict[str, type]] = {}
    graphql_filter_type_registry: ClassVar[dict[str, type]] = {}
    manager_registry: ClassVar[dict[str, type[GeneralManager]]] = {}
    _schema: ClassVar[graphene.Schema | None] = None

    @staticmethod
    def _get_channel_layer(strict: bool = False) -> BaseChannelLayer | None:
        """
        Retrieve the configured channel layer for GraphQL subscriptions.

        Parameters:
            strict (bool): When True, raise MissingChannelLayerError if no channel layer is configured.

        Returns:
            BaseChannelLayer | None: The configured channel layer instance if available, otherwise None.

        Raises:
            MissingChannelLayerError: If `strict` is True and no channel layer is configured.
        """
        layer = cast(BaseChannelLayer | None, get_channel_layer())
        if layer is None and strict:
            raise MissingChannelLayerError()
        return layer

    @classmethod
    def get_schema(cls) -> graphene.Schema | None:
        """
        Get the currently configured Graphene schema for the GraphQL registry.

        Returns:
            The active `graphene.Schema` instance, or `None` if no schema has been created.
        """
        return cls._schema

    @staticmethod
    def _group_name(
        manager_class: type[GeneralManager], identification: dict[str, Any]
    ) -> str:
        """
        Builds a deterministic channel group name for subscription events for a specific manager instance.

        Parameters:
            manager_class (type[GeneralManager]): GeneralManager subclass used to namespace the group.
            identification (dict[str, Any]): Identifying fields for the manager instance; the mapping is JSON-normalized (sorted keys) before being incorporated.

        Returns:
            group_name (str): A deterministic channel group identifier derived from the manager class and normalized identification.
        """
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
        """
        Listen to a channel layer for "gm.subscription.event" messages and enqueue their `action` values.

        Continuously receives messages from the given channel_name on the channel_layer, and when a message of type "gm.subscription.event" contains an `action`, that action string is put into the provided asyncio queue. Ignores messages of other types. The loop exits silently when the task is cancelled.

        Parameters:
                channel_layer (BaseChannelLayer): Channel layer to receive messages from.
                channel_name (str): Name of the channel to listen on.
                queue (asyncio.Queue[str]): Async queue to which received action strings will be enqueued.
        """
        try:
            while True:
                message = cast(
                    dict[str, Any], await channel_layer.receive(channel_name)
                )
                if message.get("type") != "gm.subscription.event":
                    continue
                action = cast(str | None, message.get("action"))
                if action is not None:
                    await queue.put(action)
        except asyncio.CancelledError:
            pass

    @classmethod
    def create_graphql_mutation(cls, generalManagerClass: type[GeneralManager]) -> None:
        """
        Register GraphQL mutation classes for a GeneralManager based on its Interface.

        If the manager's Interface overrides the default `create`, `update`, or `delete` methods, corresponding mutation classes are generated and stored in the class-level mutation registry under the names `create<ManagerName>`, `update<ManagerName>`, and `delete<ManagerName>`. Each generated mutation exposes a `success` flag and a field named for the manager class that returns the created/updated/deleted object when available.

        Parameters:
            generalManagerClass (type[GeneralManager]): Manager class whose `Interface` determines which mutations are generated and registered.
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
            cls._mutations[create_name] = cls.generate_create_mutation_class(
                generalManagerClass, default_return_values
            )
            logger.debug(
                "registered graphql mutation",
                context={
                    "manager": generalManagerClass.__name__,
                    "mutation": create_name,
                },
            )

        if InterfaceBase.update.__code__ != interface_cls.update.__code__:
            update_name = f"update{generalManagerClass.__name__}"
            cls._mutations[update_name] = cls.generate_update_mutation_class(
                generalManagerClass, default_return_values
            )
            logger.debug(
                "registered graphql mutation",
                context={
                    "manager": generalManagerClass.__name__,
                    "mutation": update_name,
                },
            )

        if InterfaceBase.delete.__code__ != interface_cls.delete.__code__:
            delete_name = f"delete{generalManagerClass.__name__}"
            cls._mutations[delete_name] = cls.generate_delete_mutation_class(
                generalManagerClass, default_return_values
            )
            logger.debug(
                "registered graphql mutation",
                context={
                    "manager": generalManagerClass.__name__,
                    "mutation": delete_name,
                },
            )

    @classmethod
    def create_graphql_interface(
        cls, generalManagerClass: Type[GeneralManager]
    ) -> None:
        """
        Create and register a Graphene ObjectType for a GeneralManager class and expose its queries and subscription.

        Builds a Graphene type by mapping the manager's Interface attributes and GraphQLProperties to Graphene fields and resolvers, registers the resulting type and manager in the GraphQL registries, and adds corresponding query and subscription fields to the schema.

        Parameters:
            generalManagerClass (Type[GeneralManager]): The manager class whose Interface and GraphQLProperties are used to generate Graphene fields and resolvers.
        """
        interface_cls: InterfaceBase | None = getattr(
            generalManagerClass, "Interface", None
        )
        if not interface_cls:
            return None

        logger.info(
            "building graphql interface",
            context={"manager": generalManagerClass.__name__},
        )

        graphene_type_name = f"{generalManagerClass.__name__}Type"
        fields: dict[str, Any] = {}

        # Map Attribute Types to Graphene Fields
        for field_name, field_info in interface_cls.get_attribute_types().items():
            field_type = field_info["type"]
            fields[field_name] = cls._map_field_to_graphene_read(field_type, field_name)
            resolver_name = f"resolve_{field_name}"
            fields[resolver_name] = cls._create_resolver(field_name, field_type)

        # handle GraphQLProperty attributes
        for (
            attr_name,
            attr_value,
        ) in generalManagerClass.Interface.get_graph_ql_properties().items():
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
                    base_type = GraphQL._map_field_to_graphene_base_type(
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
                graphene_field = cls._map_field_to_graphene_read(
                    resolved_type, attr_name
                )

            fields[attr_name] = graphene_field
            fields[f"resolve_{attr_name}"] = cls._create_resolver(
                attr_name, resolved_type
            )

        graphene_type = type(graphene_type_name, (graphene.ObjectType,), fields)
        cls.graphql_type_registry[generalManagerClass.__name__] = graphene_type
        cls.manager_registry[generalManagerClass.__name__] = generalManagerClass
        cls._add_queries_to_schema(graphene_type, generalManagerClass)
        cls._add_subscription_field(graphene_type, generalManagerClass)
        exposed_fields = sorted(
            name for name in fields.keys() if not name.startswith("resolve_")
        )
        logger.debug(
            "registered graphql interface",
            context={
                "manager": generalManagerClass.__name__,
                "fields": exposed_fields,
            },
        )

    @staticmethod
    def _sort_by_options(
        generalManagerClass: Type[GeneralManager],
    ) -> type[graphene.Enum] | None:
        """
        Builds an enum of sortable field names for the given manager class.

        Returns:
            An Enum type whose members are the sortable field names for the manager, or `None` if no sortable fields exist.
        """
        sort_options = []
        for (
            field_name,
            field_info,
        ) in generalManagerClass.Interface.get_attribute_types().items():
            field_type = field_info["type"]
            if issubclass(field_type, GeneralManager):
                continue
            else:
                sort_options.append(field_name)

        for (
            prop_name,
            prop,
        ) in generalManagerClass.Interface.get_graph_ql_properties().items():
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
    def _get_filter_options(
        attribute_type: type, attribute_name: str
    ) -> Generator[
        tuple[
            str, type[graphene.ObjectType] | MeasurementScalar | graphene.List | None
        ],
        None,
        None,
    ]:
        """
        Produce filter field names and corresponding Graphene input types for a given attribute.

        Parameters:
            attribute_type (type): The Python type declared for the attribute.
            attribute_name (str): The attribute's name used as the base for generated filter field names.

        Yields:
            tuple[str, type | MeasurementScalar | graphene.List | None]:
                A pair where the first element is a filter field name (e.g., "age", "price__gte",
                "name__icontains") and the second element is the Graphene input type for that filter
                or `None` when no Graphene input type should be exposed (for example, nested
                GeneralManager references).
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
            yield (
                attribute_name,
                GraphQL._map_field_to_graphene_read(attribute_type, attribute_name),
            )
            if issubclass(attribute_type, (int, float, Decimal, date, datetime)):
                for option in number_options:
                    yield (
                        f"{attribute_name}__{option}",
                        (
                            GraphQL._map_field_to_graphene_read(
                                attribute_type, attribute_name
                            )
                        ),
                    )
            elif issubclass(attribute_type, str):
                base_type = GraphQL._map_field_to_graphene_base_type(attribute_type)
                for option in string_options:
                    if option == "in":
                        yield f"{attribute_name}__in", graphene.List(base_type)
                    else:
                        yield (
                            f"{attribute_name}__{option}",
                            (
                                GraphQL._map_field_to_graphene_read(
                                    attribute_type, attribute_name
                                )
                            ),
                        )

    @staticmethod
    def _create_filter_options(
        field_type: Type[GeneralManager],
    ) -> type[graphene.InputObjectType] | None:
        """
        Create a Graphene InputObjectType that exposes available filter fields for a GeneralManager subclass.

        Builds filter fields from the manager's Interface attributes and any GraphQLProperty marked filterable. Returns None when no filterable fields are found.

        Parameters:
            field_type (Type[GeneralManager]): Manager class whose Interface and GraphQLProperties determine the filter fields.

        Returns:
            type[graphene.InputObjectType] | None: Generated InputObjectType class for filters, or `None` if no filter fields are applicable.
        """

        graphene_filter_type_name = f"{field_type.__name__}FilterType"
        if graphene_filter_type_name in GraphQL.graphql_filter_type_registry:
            return GraphQL.graphql_filter_type_registry[graphene_filter_type_name]

        filter_fields: dict[str, Any] = {}
        for attr_name, attr_info in field_type.Interface.get_attribute_types().items():
            attr_type = attr_info["type"]
            filter_fields = {
                **filter_fields,
                **{
                    k: v
                    for k, v in GraphQL._get_filter_options(attr_type, attr_name)
                    if v is not None
                },
            }
        for prop_name, prop in field_type.Interface.get_graph_ql_properties().items():
            if not prop.filterable:
                continue
            hints = [t for t in get_args(prop.graphql_type_hint) if t is not type(None)]
            prop_type = hints[0] if hints else cast(type, prop.graphql_type_hint)
            filter_fields = {
                **filter_fields,
                **{
                    k: v
                    for k, v in GraphQL._get_filter_options(prop_type, prop_name)
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
    def _map_field_to_graphene_read(field_type: type, field_name: str) -> Any:
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
                attributes: dict[str, Any] = {
                    "reverse": graphene.Boolean(),
                    "page": graphene.Int(),
                    "page_size": graphene.Int(),
                    "group_by": graphene.List(graphene.String),
                }
                filter_options = GraphQL._create_filter_options(field_type)
                if filter_options:
                    attributes["filter"] = graphene.Argument(filter_options)
                    attributes["exclude"] = graphene.Argument(filter_options)

                sort_by_options = GraphQL._sort_by_options(field_type)
                if sort_by_options:
                    attributes["sort_by"] = graphene.Argument(sort_by_options)

                page_type = GraphQL._get_or_create_page_type(
                    field_type.__name__ + "Page",
                    lambda: GraphQL.graphql_type_registry[field_type.__name__],
                )
                return graphene.Field(page_type, **attributes)

            return graphene.Field(
                lambda: GraphQL.graphql_type_registry[field_type.__name__]
            )
        else:
            return GraphQL._map_field_to_graphene_base_type(field_type)()

    @staticmethod
    def _map_field_to_graphene_base_type(field_type: type) -> Type[Any]:
        """
        Map a Python interface type to the corresponding Graphene scalar or custom scalar.

        Parameters:
            field_type (type): Python type from the interface to map.

        Returns:
            Type[Any]: The Graphene scalar or custom scalar type used to represent the input type (e.g., graphene.String, graphene.Int, MeasurementScalar).

        Raises:
            UnsupportedGraphQLFieldTypeError: If `field_type` is `dict`, which is not supported for GraphQL mapping.
        """
        base_type = (
            get_origin(field_type) or field_type
        )  # Handle typing generics safely.
        if not isinstance(base_type, type):
            return graphene.String
        if issubclass(base_type, dict):
            raise UnsupportedGraphQLFieldTypeError(field_type)
        if issubclass(base_type, str):
            return graphene.String
        elif issubclass(base_type, bool):
            return graphene.Boolean
        elif issubclass(base_type, int):
            return graphene.Int
        elif issubclass(base_type, (float, Decimal)):
            return graphene.Float
        elif issubclass(base_type, datetime):
            return graphene.DateTime
        elif issubclass(base_type, date):
            return graphene.Date
        elif issubclass(base_type, Measurement):
            return MeasurementScalar
        else:
            return graphene.String

    @staticmethod
    def _parse_input(input_val: dict[str, Any] | str | None) -> dict[str, Any]:
        """
        Normalize a filter or exclude input into a dictionary.

        Accepts a dict, a JSON-encoded string, or None. If given None or an invalid JSON string, returns an empty dict.

        Parameters:
            input_val: Filter or exclude input provided as a dict, a JSON-encoded string, or None.

        Returns:
            dict: Mapping of filter keys to values; empty dict if input is None or invalid JSON.
        """
        if input_val is None:
            return {}
        if isinstance(input_val, str):
            try:
                return json.loads(input_val)
            except (json.JSONDecodeError, ValueError):
                return {}
        return input_val

    @staticmethod
    def _apply_query_parameters(
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
        filters = GraphQL._parse_input(filter_input)
        if filters:
            queryset = queryset.filter(**filters)

        excludes = GraphQL._parse_input(exclude_input)
        if excludes:
            queryset = queryset.exclude(**excludes)

        if sort_by:
            sort_by_str = cast(str, getattr(sort_by, "value", sort_by))
            queryset = queryset.sort(sort_by_str, reverse=reverse)

        return queryset

    @staticmethod
    def _apply_permission_filters(
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
        permission_filters = get_read_permission_filter(general_manager_class, info)
        if not permission_filters:
            return queryset

        filtered_queryset = queryset.none()
        for perm_filter, perm_exclude in permission_filters:
            qs_perm = queryset.exclude(**perm_exclude).filter(**perm_filter)
            filtered_queryset = filtered_queryset | qs_perm

        return filtered_queryset

    @staticmethod
    def _check_read_permission(
        instance: GeneralManager, info: GraphQLResolveInfo, field_name: str
    ) -> bool:
        """Return True if the user may read ``field_name`` on ``instance``."""
        PermissionClass: type[BasePermission] | None = getattr(
            instance, "Permission", None
        )
        if PermissionClass:
            return PermissionClass(instance, info.context.user).check_permission(
                "read", field_name
            )
        return True

    @staticmethod
    def _create_list_resolver(
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
            if base_queryset is None:
                base_queryset = fallback_manager_class.all()
            # use _manager_class from the attribute if available, otherwise fallback
            manager_class = getattr(
                base_queryset, "_manager_class", fallback_manager_class
            )
            qs = GraphQL._apply_permission_filters(base_queryset, manager_class, info)
            qs = GraphQL._apply_query_parameters(qs, filter, exclude, sort_by, reverse)
            qs = GraphQL._apply_grouping(qs, group_by)

            total_count = len(qs)

            qs_paginated = GraphQL._apply_pagination(qs, page, page_size)

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
    def _apply_pagination(
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
    def _apply_grouping(
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
    def _create_measurement_resolver(field_name: str) -> Callable[..., Any]:
        """
        Creates a resolver for a Measurement field that returns its value and unit, with optional unit conversion.

        The generated resolver checks read permissions for the specified field. If permitted and the field is a Measurement, it returns a dictionary containing the measurement's value and unit, converting to the specified target unit if provided. Returns None if permission is denied or the field is not a Measurement.
        """

        def resolver(
            self: GeneralManager,
            info: GraphQLResolveInfo,
            target_unit: str | None = None,
        ) -> dict[str, Any] | None:
            if not GraphQL._check_read_permission(self, info, field_name):
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
    def _create_normal_resolver(field_name: str) -> Callable[..., Any]:
        """
        Create a resolver for scalar fields (no lists, no Measurement instances).
        """

        def resolver(self: GeneralManager, info: GraphQLResolveInfo) -> Any:
            if not GraphQL._check_read_permission(self, info, field_name):
                return None
            return getattr(self, field_name)

        return resolver

    @classmethod
    def _create_resolver(cls, field_name: str, field_type: type) -> Callable[..., Any]:
        """
        Returns a resolver function for a field, selecting list, measurement, or standard resolution based on the field's type and name.

        For fields ending with `_list` referencing a `GeneralManager` subclass, provides a resolver supporting pagination and filtering. For `Measurement` fields, returns a resolver that handles unit conversion and permission checks. For all other fields, returns a standard resolver with permission enforcement.
        """
        if field_name.endswith("_list") and issubclass(field_type, GeneralManager):
            return cls._create_list_resolver(
                lambda self: getattr(self, field_name), field_type
            )
        if issubclass(field_type, Measurement):
            return cls._create_measurement_resolver(field_name)
        return cls._create_normal_resolver(field_name)

    @classmethod
    def _get_or_create_page_type(
        cls,
        page_type_name: str,
        item_type: type[graphene.ObjectType] | Callable[[], type[graphene.ObjectType]],
    ) -> type[graphene.ObjectType]:
        """
        Provide or retrieve a GraphQL ObjectType that represents a paginated page for the given item type.

        Creates and caches a GraphQL ObjectType with two fields:
        - `items`: a required list of the provided item type.
        - `pageInfo`: a required PageInfo object containing pagination metadata.

        Parameters:
            page_type_name (str): The name to use for the generated GraphQL ObjectType.
            item_type (type[graphene.ObjectType] | Callable[[], type[graphene.ObjectType]]):
                The Graphene ObjectType used for items, or a zero-argument callable that returns it (to support forward references).

        Returns:
            type[graphene.ObjectType]: A Graphene ObjectType with `items` and `pageInfo` fields.
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
    def _build_identification_arguments(
        cls, generalManagerClass: Type[GeneralManager]
    ) -> dict[str, Any]:
        """
        Build the GraphQL arguments required to uniquely identify an instance of the given manager class.

        For each input field defined on the manager's Interface: use "<name>_id" as a required ID argument for fields that reference another GeneralManager, use "id" as a required ID argument when present, and map other fields to their corresponding Graphene base type marked required.

        Parameters:
            generalManagerClass: GeneralManager subclass whose Interface.input_fields are used to derive identification arguments.

        Returns:
            dict[str, Any]: Mapping of argument name to a Graphene Argument suitable for identifying a single manager instance.
        """
        identification_fields: dict[str, Any] = {}
        for (
            input_field_name,
            input_field,
        ) in generalManagerClass.Interface.input_fields.items():
            if issubclass(input_field.type, GeneralManager):
                key = f"{input_field_name}_id"
                identification_fields[key] = graphene.Argument(
                    graphene.ID, required=True
                )
            elif input_field_name == "id":
                identification_fields[input_field_name] = graphene.Argument(
                    graphene.ID, required=True
                )
            else:
                base_type = cls._map_field_to_graphene_base_type(input_field.type)
                identification_fields[input_field_name] = graphene.Argument(
                    base_type, required=True
                )
        return identification_fields

    @classmethod
    def _add_queries_to_schema(
        cls, graphene_type: type, generalManagerClass: Type[GeneralManager]
    ) -> None:
        """
        Registers list and detail GraphQL query fields for the given manager type into the class query registry.

        Parameters:
            graphene_type (type): The Graphene ObjectType that represents the manager's GraphQL type.
            generalManagerClass (Type[GeneralManager]): The GeneralManager subclass to expose via queries.

        Raises:
            TypeError: If `generalManagerClass` is not a subclass of GeneralManager.
        """
        if not issubclass(generalManagerClass, GeneralManager):
            raise InvalidGeneralManagerClassError(generalManagerClass)

        if not hasattr(cls, "_query_fields"):
            cls._query_fields = cast(dict[str, Any], {})

        # resolver and field for the list query
        list_field_name = f"{generalManagerClass.__name__.lower()}_list"
        attributes: dict[str, Any] = {
            "reverse": graphene.Boolean(),
            "page": graphene.Int(),
            "page_size": graphene.Int(),
            "group_by": graphene.List(graphene.String),
        }
        filter_options = cls._create_filter_options(generalManagerClass)
        if filter_options:
            attributes["filter"] = graphene.Argument(filter_options)
            attributes["exclude"] = graphene.Argument(filter_options)
        sort_by_options = cls._sort_by_options(generalManagerClass)
        if sort_by_options:
            attributes["sort_by"] = graphene.Argument(sort_by_options)

        page_type = cls._get_or_create_page_type(
            graphene_type.__name__ + "Page", graphene_type
        )
        list_field = graphene.Field(page_type, **attributes)

        def _all_items(_: Any) -> Any:
            """
            Return all instances for the associated GeneralManager class.

            Returns:
                All instances for the associated GeneralManager class, typically provided as a Bucket/QuerySet-like iterable.
            """
            return generalManagerClass.all()

        list_resolver = cls._create_list_resolver(_all_items, generalManagerClass)
        cls._query_fields[list_field_name] = list_field
        cls._query_fields[f"resolve_{list_field_name}"] = list_resolver

        # resolver and field for the single item query
        item_field_name = generalManagerClass.__name__.lower()
        identification_fields = cls._build_identification_arguments(generalManagerClass)
        item_field = graphene.Field(graphene_type, **identification_fields)

        def resolver(
            self: GeneralManager, info: GraphQLResolveInfo, **identification: dict
        ) -> GeneralManager:
            """
            Instantiate and return a GeneralManager for the provided identification arguments.

            Parameters:
                identification (dict): Mapping of identification argument names to values passed to the manager constructor.

            Returns:
                GeneralManager: The manager instance identified by the provided arguments.
            """
            return generalManagerClass(**identification)

        cls._query_fields[item_field_name] = item_field
        cls._query_fields[f"resolve_{item_field_name}"] = resolver

    @staticmethod
    def _normalize_graphql_name(name: str) -> str:
        """
        Convert a GraphQL selection name (potentially camelCase) to the corresponding Python attribute name.

        Parameters:
            name (str): GraphQL field name from a selection set.

        Returns:
            str: The snake_case representation matching the GraphQLProperty definition.
        """
        if "_" in name:
            return name
        snake = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
        snake = re.sub("([a-z0-9])([A-Z])", r"\1_\2", snake)
        return snake.lower()

    @staticmethod
    def _prime_graphql_properties(
        instance: GeneralManager, property_names: Iterable[str] | None = None
    ) -> None:
        """
        Eagerly evaluate GraphQLProperty attributes on a manager instance to capture dependency metadata.

        When GraphQLProperty descriptors are accessed they may record dependency information on the instance; this function forces those properties to be read so their dependency tracking is populated. If `property_names` is provided, only those property names that exist on the Interface are evaluated; otherwise all GraphQLProperties from the Interface are evaluated.

        Parameters:
            instance (GeneralManager): The manager instance whose GraphQLProperty attributes should be evaluated.
            property_names (Iterable[str] | None): Optional iterable of property names to evaluate. Names not present in the Interface's GraphQLProperties are ignored.
        """
        interface_cls = getattr(instance.__class__, "Interface", None)
        if interface_cls is None:
            return
        available_properties = interface_cls.get_graph_ql_properties()
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
        Convert dependency tracker records into resolved (manager class, identification dict) pairs.

        Parses records whose operation is "identification", looks up the corresponding GeneralManager class by name in the registry, and parses the identifier into a dict. Records that do not meet these criteria are skipped.

        Parameters:
            dependency_records (Iterable[Dependency]): Iterable of dependency records as (manager_name, operation, identifier).

        Returns:
            list[tuple[type[GeneralManager], dict[str, Any]]]: List of (manager_class, identification_dict) tuples for each successfully resolved record.
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
        Determine which GraphQLProperty names are selected under the subscription payload's `item` field.

        Parameters:
            info (GraphQLResolveInfo): Resolve info containing the parsed selection set and fragments.
            manager_class (type[GeneralManager]): GeneralManager subclass whose Interface defines available GraphQLProperty names.

        Returns:
            property_names (set[str]): Set of GraphQLProperty names referenced under the `item` selection in the subscription request; empty set if none or if the manager has no Interface.
        """
        interface_cls = getattr(manager_class, "Interface", None)
        if interface_cls is None:
            return set()
        available_properties = set(interface_cls.get_graph_ql_properties().keys())
        if not available_properties:
            return set()

        property_names: set[str] = set()

        def collect_from_selection(selection_set: SelectionSetNode | None) -> None:
            """
            Recursively collect selected GraphQL property names from a SelectionSetNode into the enclosing `property_names` set.

            Processes each selection in the provided selection_set:
            - Adds a field's name to `property_names` when the name is present in the surrounding `available_properties` set.
            - For fragment spreads, resolves the fragment via `info.fragments` (from the enclosing scope) and recurses into its selection set.
            - For inline fragments, recurses into the fragment's selection set.

            Parameters:
                selection_set (SelectionSetNode | None): GraphQL selection set to traverse; no action is taken if None.
            """
            if selection_set is None:
                return
            for selection in selection_set.selections:
                if isinstance(selection, FieldNode):
                    name = selection.name.value
                    normalized = cls._normalize_graphql_name(name)
                    if normalized in available_properties:
                        property_names.add(normalized)
                elif isinstance(selection, FragmentSpreadNode):
                    fragment = info.fragments.get(selection.name.value)
                    if fragment is not None:
                        collect_from_selection(fragment.selection_set)
                elif isinstance(selection, InlineFragmentNode):
                    collect_from_selection(selection.selection_set)

        def inspect_selection_set(selection_set: SelectionSetNode | None) -> None:
            """
            Traverse a GraphQL SelectionSet and collect subselections of any field named "item".

            Parameters:
                selection_set (SelectionSetNode | None): The AST selection set to inspect. If None, the function returns without action.

            Description:
                - Visits FieldNode, FragmentSpreadNode, and InlineFragmentNode entries.
                - For a FieldNode named "item", delegates its subselection to collect_from_selection.
                - For other FieldNode entries and InlineFragmentNode entries, recurses into their subselections.
                - For FragmentSpreadNode entries, attempts to resolve the referenced fragment (via the surrounding `info.fragments` if available) and inspects that fragment's subselection.
            """
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
        Builds a list of dependency pairs (manager class, identification) for subscription wiring from an instance and optional dependency records.

        Given a manager class and its instantiated item, returns deduplicated dependency definitions derived from:
        - any Dependency records produced by a dependency tracker, and
        - the manager Interface's input fields that reference other GeneralManager types and are populated on the instance.

        Parameters:
            manager_class (type[GeneralManager]): The manager type whose subscription dependencies are being resolved.
            instance (GeneralManager): The instantiated manager item whose inputs and identification are inspected.
            dependency_records (Iterable[Dependency] | None): Optional dependency-tracker records to include.

        Returns:
            list[tuple[type[GeneralManager], dict[str, Any]]]: A list of (dependent_manager_class, identification) pairs.
            Each identification is a dict of identification fields. The list excludes the (manager_class, instance.identification) pair and contains no duplicates.
        """
        dependencies: list[tuple[type[GeneralManager], dict[str, Any]]] = []
        seen: set[tuple[str, str]] = set()
        if dependency_records:
            for (
                dependency_class,
                dependency_identification,
            ) in cls._dependencies_from_tracker(dependency_records):
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
        Create a GeneralManager instance for the given identification and optionally prime its GraphQL properties to capture dependency records.

        Parameters:
            manager_class (type[GeneralManager]): Manager class to instantiate.
            identification (dict[str, Any]): Mapping of identification field names to values used to construct the instance.
            collect_dependencies (bool): If True, prime GraphQL properties while tracking and return the captured Dependency records.
            property_names (Iterable[str] | None): Specific GraphQLProperty names to prime; if None, all relevant properties are primed.

        Returns:
            tuple[GeneralManager, set[Dependency]]: The instantiated manager and a set of captured Dependency objects (empty if collect_dependencies is False).
        """
        if collect_dependencies:
            with DependencyTracker() as captured_dependencies:
                instance = manager_class(**identification)
                GraphQL._prime_graphql_properties(instance, property_names)
            return instance, captured_dependencies

        instance = manager_class(**identification)
        return instance, set()

    @classmethod
    def _add_subscription_field(
        cls,
        graphene_type: type[graphene.ObjectType],
        generalManagerClass: Type[GeneralManager],
    ) -> None:
        """
        Register a GraphQL subscription field that publishes change events for the given manager type.

        Creates (or reuses) a SubscriptionEvent payload GraphQL type and adds three entries to the class subscription registry:
        - a field exposing the subscription with identification arguments,
        - an async subscribe function that yields an initial "snapshot" event and subsequent change events for the identified instance and its dependencies,
        - and a resolve function that returns the delivered payload.

        Parameters:
            graphene_type (type[graphene.ObjectType]): GraphQL ObjectType representing the manager's item type used as the payload `item` field.
            generalManagerClass (Type[GeneralManager]): The GeneralManager subclass whose changes the subscription will publish.

        Notes:
        - The subscribe function requires an available channel layer and subscribes the caller to channel groups derived from the instance identification and its resolved dependencies.
        - The subscribe coroutine yields SubscriptionEvent objects with fields `item` (the current instance or None if it cannot be instantiated) and `action` (a string such as `"snapshot"` or other change actions).
        - On termination the subscription cleans up listener tasks and unsubscribes from channel groups.
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
            cls._subscription_payload_registry[generalManagerClass.__name__] = (
                payload_type
            )

        identification_args = cls._build_identification_arguments(generalManagerClass)
        subscription_field = graphene.Field(payload_type, **identification_args)

        async def subscribe(
            _root: Any,
            info: GraphQLResolveInfo,
            **identification: Any,
        ) -> AsyncIterator[SubscriptionEvent]:
            """
            Stream subscription events for a specific manager instance identified by the provided arguments.

            Yields an initial `SubscriptionEvent` with `action` set to `"snapshot"` containing the current manager instance, then yields `SubscriptionEvent`s for each subsequent action. For update events, `item` will be the re-instantiated manager instance or `None` if instantiation fails. The subscriber is registered on the manager's channel groups (including dependent managers' groups) and the channel subscriptions and background listener are cleaned up when the iterator is closed or cancelled.

            Parameters:
                identification: Identification fields required to locate the manager instance (maps to the manager's identification signature).

            Returns:
                AsyncIterator[SubscriptionEvent]: An asynchronous iterator that first yields a snapshot event and then yields update events; each event's `item` is the manager instance or `None` if it could not be instantiated.
            """
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
                channel_layer = cast(
                    BaseChannelLayer, cls._get_channel_layer(strict=True)
                )
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
                """
                Yield subscription events for a manager instance, starting with an initial snapshot followed by subsequent updates.

                Returns:
                    AsyncIterator[SubscriptionEvent]: An asynchronous iterator that first yields a `SubscriptionEvent` with `action` set to `"snapshot"` and `item` containing the current manager instance (or `None` if instantiation failed). Subsequent yields provide `SubscriptionEvent` values for each received action, where `action` is the action string and `item` is the (re-)instantiated manager or `None` if instantiation failed.

                Notes:
                    When the iterator is closed or exits, the background listener task is cancelled and the subscription's channel group memberships are discarded.
                """
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
                        except HANDLED_MANAGER_ERRORS:
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
            """
            Passes a subscription payload through unchanged.

            Parameters:
                payload (SubscriptionEvent): The subscription event payload to deliver to the client.
                info (GraphQLResolveInfo): GraphQL resolver info (unused).

            Returns:
                SubscriptionEvent: The same payload instance provided as input.
            """
            return payload

        cls._subscription_fields[field_name] = subscription_field
        cls._subscription_fields[f"subscribe_{field_name}"] = subscribe
        cls._subscription_fields[f"resolve_{field_name}"] = resolve

    @classmethod
    def create_write_fields(cls, interface_cls: InterfaceBase) -> dict[str, Any]:
        """
        Create Graphene input fields for writable attributes defined by an Interface.

        Skips system fields ("changed_by", "created_at", "updated_at") and attributes marked as derived. For attributes whose type is a GeneralManager, produces an ID field or a list of ID fields for names ending with "_list". Each generated field is annotated with an `editable` attribute reflecting the interface metadata. Always includes an optional `history_comment` string field marked editable.

        Parameters:
            interface_cls (InterfaceBase): Interface providing attribute metadata (type, required, default, editable, derived) used to build the input fields.

        Returns:
            dict[str, Any]: Mapping from attribute name to a Graphene input field instance.
        """
        fields: dict[str, Any] = {}

        for name, info in interface_cls.get_attribute_types().items():
            if name in ["changed_by", "created_at", "updated_at"]:
                continue
            if info["is_derived"]:
                continue

            typ = info["type"]
            req = info["is_required"]
            default = info["default"]

            fld: Any
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
                base_cls = cls._map_field_to_graphene_base_type(typ)
                fld = base_cls(
                    required=req,
                    default_value=default,
                )

            # mark for generate* code to know what is editable
            cast(Any, fld).editable = info["is_editable"]
            fields[name] = fld

        # history_comment is always optional without a default value
        history_field = graphene.String()
        cast(Any, history_field).editable = True
        fields["history_comment"] = history_field

        return fields

    @classmethod
    def generate_create_mutation_class(
        cls,
        generalManagerClass: type[GeneralManager],
        default_return_values: dict[str, Any],
    ) -> type[graphene.Mutation] | None:
        """
        Generate a Graphene Mutation class that creates instances of the given GeneralManager subclass.

        Parameters:
            generalManagerClass (type[GeneralManager]): The GeneralManager subclass to expose a create mutation for.
            default_return_values (dict[str, Any]): Base mutation return fields to include on the generated class.

        Returns:
            type[graphene.Mutation] | None: A Mutation class named `Create<ManagerName>` that implements creation for the manager (exposes appropriate Arguments and a `mutate` implementation), or `None` if the manager class does not define an `Interface`.
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
            Create a new instance of the manager using the provided input fields.

            Parameters:
                info (GraphQLResolveInfo): GraphQL resolve info whose context user ID is used as the creator_id.
                kwargs (Any): Input fields for the new instance; fields equal to `NOT_PROVIDED` are ignored.

            Returns:
                result (dict): A dictionary containing `success` (`True` if creation succeeded, `False` otherwise). On success the dictionary also includes the created instance under a key matching the manager class name.
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
            except HANDLED_MANAGER_ERRORS as error:
                raise GraphQL._handle_graph_ql_error(error) from error

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
                        for field_name, field in cls.create_write_fields(
                            interface_cls
                        ).items()
                        if field_name not in generalManagerClass.Interface.input_fields
                    },
                ),
                "mutate": create_mutation,
            },
        )

    @classmethod
    def generate_update_mutation_class(
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
            Update a GeneralManager instance identified by its `id` with the provided field values.

            Parameters:
                info (GraphQLResolveInfo): GraphQL resolver context (contains the requesting user and request data).
                **kwargs: Field values to update. Must include `id` to identify the target instance; other keys are treated as fields to update.

            Returns:
                dict: Contains `success` (`True` if the update succeeded, `False` otherwise`) and, on success, the updated instance under a key matching the manager class name.
            """
            manager_id = kwargs.pop("id", None)
            if manager_id is None:
                raise GraphQL._handle_graph_ql_error(MissingManagerIdentifierError())
            try:
                instance = generalManagerClass(id=manager_id).update(
                    creator_id=info.context.user.id, **kwargs
                )
            except HANDLED_MANAGER_ERRORS as error:
                raise GraphQL._handle_graph_ql_error(error) from error

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
                            for field_name, field in cls.create_write_fields(
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
    def generate_delete_mutation_class(
        cls,
        generalManagerClass: type[GeneralManager],
        default_return_values: dict[str, Any],
    ) -> type[graphene.Mutation] | None:
        """
        Generate a Graphene Mutation subclass that deletes a GeneralManager instance identified by ID.

        The created mutation exposes the manager's input fields defined by its Interface (filtered to the Interface's input_fields), requires an `id` to identify the instance, invokes the manager's `delete(...)` method with the request user as `creator_id`, and returns a payload containing a `success` flag and the manager-named field (the manager-named field will be `None` when the underlying delete does not return an instance).

        Returns:
            The generated Graphene Mutation subclass for the delete operation, or `None` if the provided manager class has no `Interface`.

        Raises:
            GraphQLError: produced via GraphQL._handle_graph_ql_error when the `id` is missing or when handled manager errors occur.
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
            Deletes the identified GeneralManager instance.

            Parameters:
                id (str|int): Identifier of the manager instance to delete; passed via kwargs.

            Returns:
                dict: Mapping with keys:
                    - `success`: `True` when deletion completed without raising a handled error.
                    - `<ManagerName>`: The value returned by the underlying delete operation for the manager (often `None`).

            Raises:
                GraphQLError: If `id` is missing or a handled manager error (e.g., PermissionError, ValidationError, ValueError, LookupError, TypeError, AttributeError, RuntimeError) occurs; errors are converted via GraphQL._handle_graph_ql_error.
            """
            manager_id = kwargs.pop("id", None)
            if manager_id is None:
                raise GraphQL._handle_graph_ql_error(MissingManagerIdentifierError())
            try:
                instance = generalManagerClass(id=manager_id).delete(
                    creator_id=info.context.user.id
                )
            except HANDLED_MANAGER_ERRORS as error:
                raise GraphQL._handle_graph_ql_error(error) from error

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
                        for field_name, field in cls.create_write_fields(
                            interface_cls
                        ).items()
                        if field_name in generalManagerClass.Interface.input_fields
                    },
                ),
                "mutate": delete_mutation,
            },
        )

    @staticmethod
    def _handle_graph_ql_error(error: Exception) -> GraphQLError:
        """
        Convert an exception into a GraphQL error with an appropriate extensions['code'].

        Maps:
            PermissionError -> "PERMISSION_DENIED"
            ValueError, ValidationError, TypeError -> "BAD_USER_INPUT"
            other exceptions -> "INTERNAL_SERVER_ERROR"

        Parameters:
            error (Exception): The original exception to convert.

        Returns:
            GraphQLError: GraphQL error containing the original message and an `extensions['code']` indicating the error category.
        """
        message = str(error)
        error_name = type(error).__name__
        if isinstance(error, PermissionError):
            logger.info(
                "graphql permission error",
                context={
                    "error": error_name,
                    "message": message,
                },
            )
            return GraphQLError(
                message,
                extensions={
                    "code": "PERMISSION_DENIED",
                },
            )
        elif isinstance(error, (ValueError, ValidationError, TypeError)):
            logger.warning(
                "graphql user error",
                context={
                    "error": error_name,
                    "message": message,
                },
            )
            return GraphQLError(
                message,
                extensions={
                    "code": "BAD_USER_INPUT",
                },
            )
        else:
            logger.error(
                "graphql internal error",
                context={
                    "error": error_name,
                    "message": message,
                },
                exc_info=error,
            )
            return GraphQLError(
                message,
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
        Send a "gm.subscription.event" message to the channel group corresponding to a changed GeneralManager instance.

        If the provided instance is a registered GeneralManager and a channel layer is configured, publish a message containing the given action to the channel group derived from the manager class and the instance's identification. If the instance is None, the manager type is not registered, or no channel layer is available, the function returns without side effects.

        Parameters:
            sender (type[GeneralManager] | GeneralManager): The signal sender; either a GeneralManager subclass or an instance.
            instance (GeneralManager | None): The GeneralManager instance that changed.
            action (str): A string describing the change action (e.g., "created", "updated", "deleted").
        """
        if instance is None or not isinstance(instance, GeneralManager):
            return

        if isinstance(sender, type) and issubclass(sender, GeneralManager):
            manager_class: type[GeneralManager] = sender
        else:
            manager_class = instance.__class__

        if manager_class.__name__ not in cls.manager_registry:
            logger.debug(
                "skipping subscription event for unregistered manager",
                context={
                    "manager": manager_class.__name__,
                    "action": action,
                },
            )
            return

        channel_layer = cls._get_channel_layer()
        if channel_layer is None:
            logger.warning(
                "channel layer unavailable for subscription event",
                context={
                    "manager": manager_class.__name__,
                    "action": action,
                },
            )
            return

        group_name = cls._group_name(manager_class, instance.identification)
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "gm.subscription.event",
                "action": action,
            },
        )
        logger.debug(
            "dispatched subscription event",
            context={
                "manager": manager_class.__name__,
                "action": action,
                "group": group_name,
            },
        )


post_data_change.connect(GraphQL._handle_data_change, weak=False)
