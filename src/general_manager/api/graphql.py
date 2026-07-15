"""GraphQL schema utilities for exposing GeneralManager models via Graphene."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import re
from types import UnionType
from typing import (
    AsyncIterator,
    Callable,
    ClassVar,
    Generator,
    Iterable,
    Mapping,
    TYPE_CHECKING,
    SupportsIndex,
    SupportsInt,
    TypeAlias,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
)

import graphene
from asgiref.sync import async_to_sync
from channels.layers import BaseChannelLayer
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.utils.module_loading import import_string

from general_manager.bucket.base_bucket import Bucket
from general_manager.bucket.group_bucket import GroupBucket
from general_manager.cache.dependency_index import (
    Dependency,
)
from general_manager.cache.signals import post_data_change
from general_manager.conf import get_setting
from general_manager.interface.base_interface import InterfaceBase
from general_manager.logging import get_logger
from general_manager.manager.general_manager import GeneralManager
from general_manager.measurement.measurement import Measurement
from general_manager.permission.graphql_capabilities import (
    GraphQLPermissionCapability,
    clear_capability_context,
    get_capability_context,
    get_graphql_capabilities,
)
from general_manager.uploads.graphql_types import (
    StoredFile,
    StoredImage,
    create_stored_file_value,
)
from general_manager.utils.format_string import pascal_to_snake
from general_manager.utils.type_checks import safe_issubclass

from graphql import GraphQLError

# Imports from extracted submodules
# Note: several names are imported purely for re-export to preserve the public
# API of this module (code outside this package imports them from here).
from general_manager.api.graphql_errors import (
    BigIntScalar,  # noqa: F401  # re-exported
    SubscriptionEvent,
    InvalidMeasurementValueError,  # noqa: F401  # re-exported
    UnsupportedGraphQLFieldTypeError,  # noqa: F401  # re-exported
    InvalidGeneralManagerClassError,
    GrapheneBaseTypeClass,
    MissingChannelLayerError,  # noqa: F401  # re-exported
    MissingManagerIdentifierError,  # noqa: F401  # re-exported
    EXPECTED_MANAGER_ERRORS,  # noqa: F401  # re-exported
    SUSPICIOUS_MANAGER_ERRORS,  # noqa: F401  # re-exported
    HANDLED_MANAGER_ERRORS as HANDLED_MANAGER_ERRORS,
    ValidationFieldNameMapper,
    MeasurementType as MeasurementType,
    MeasurementScalar as MeasurementScalar,
    PageInfo,
    get_read_permission_filter,  # noqa: F401  # re-exported
    map_field_to_graphene_base_type as _map_field_to_graphene_base_type_fn,
    handle_graph_ql_error as _handle_graph_ql_error_fn,
)
from general_manager.api.graphql_mutations import (
    create_write_fields as _create_write_fields_fn,
    generate_create_mutation_class as _generate_create_mutation_class_fn,
    generate_update_mutation_class as _generate_update_mutation_class_fn,
    generate_delete_mutation_class as _generate_delete_mutation_class_fn,
)
from general_manager.api.graphql_resolvers import (
    parse_input as _parse_input_fn,
    apply_query_parameters as _apply_query_parameters_fn,
    apply_permission_filters as _apply_permission_filters_fn,
    apply_pagination as _apply_pagination_fn,
    apply_grouping as _apply_grouping_fn,
    check_read_permission as _check_read_permission_fn,
    can_read_instance as _can_read_instance_fn,
    create_measurement_resolver as _create_measurement_resolver_fn,
    create_normal_resolver as _create_normal_resolver_fn,
    create_list_resolver as _create_list_resolver_fn,
    create_resolver as _create_resolver_fn,
)
from general_manager.api.graphql_search import (
    register_search_query as _register_search_query_fn,
    create_search_union as _create_search_union_fn,
    create_search_result_type as _create_search_result_type_fn,
    parse_search_filters as _parse_search_filters_fn,
    merge_permission_filters as _merge_permission_filters_fn,
    matches_filters as _matches_filters_fn,
    passes_permission_filters as _passes_permission_filters_fn,
    get_filter_options as _get_filter_options_fn,
    create_filter_options as _create_filter_options_fn,
    normalize_filter_input as _normalize_filter_input_fn,
)
from general_manager.api.notification_batching import _queue_notification
from general_manager.api.graphql_subscriptions import (
    get_channel_layer_safe as _get_channel_layer_fn,
    group_name as _group_name_fn,
    class_group_name as _class_group_name_fn,
    refresh_group_name as _refresh_group_name_fn,
    dispatch_subscription_event as _dispatch_subscription_event_fn,
    channel_listener as _channel_listener_fn,
    channel_message_listener as _channel_message_listener_fn,
    prime_graphql_properties as _prime_graphql_properties_fn,
    dependencies_from_tracker as _dependencies_from_tracker_fn,
    subscription_property_names as _subscription_property_names_fn,
    resolve_subscription_dependencies as _resolve_subscription_dependencies_fn,
    instantiate_manager as _instantiate_manager_fn,
)
from general_manager.api.registry import GraphQLRegistry

if TYPE_CHECKING:
    from graphene import ResolveInfo as GraphQLResolveInfo
    from general_manager.permission.base_permission import PermissionConstraint

logger = get_logger("api.graphql")

IntCoercible: TypeAlias = str | bytes | bytearray | SupportsInt | SupportsIndex
SUBSCRIPTION_HYDRATION_ERRORS: tuple[type[Exception], ...] = (
    *HANDLED_MANAGER_ERRORS,
    ObjectDoesNotExist,
)


GeneralManagerT = TypeVar("GeneralManagerT", bound=GeneralManager)
GraphQLFieldMap = dict[str, object]
GraphQLFilterMapping = dict[str, object]
GraphQLFilterInput = Mapping[str, object] | str | None
GraphQLSearchFilterInput = (
    GraphQLFilterMapping | str | list[GraphQLFilterMapping] | None
)
GraphQLIdentification = dict[str, object]
GraphQLResolver = Callable[..., object]
GraphQLListGetter = Callable[[object, bool], Bucket[GeneralManager] | None]
GraphQLFilterNormalizer = Callable[
    [GraphQLFilterMapping],
    dict[str, GraphQLFilterMapping],
]
GraphQLMutationMap = dict[str, type[graphene.Mutation]]
_SUBSCRIPTION_CLEANUP_FAILURE_MESSAGE = "subscription cleanup failed"
_SUBSCRIPTION_ROLLBACK_FAILURE_MESSAGE = "subscription setup and rollback failed"
_SUBSCRIPTION_STREAM_CLEANUP_FAILURE_MESSAGE = "subscription stream and cleanup failed"


async def _cleanup_subscription_resources(
    channel_layer: BaseChannelLayer,
    channel_name: str,
    joined_groups: Iterable[str],
    listener_task: asyncio.Task[None] | None = None,
) -> None:
    """Stop a listener and attempt to discard every successfully joined group."""
    cleanup_errors: list[BaseException] = []
    if listener_task is not None:
        listener_task.cancel()
        try:
            await listener_task
        except asyncio.CancelledError:
            pass
        except BaseException as exc:  # noqa: BLE001
            cleanup_errors.append(exc)

    for group in joined_groups:
        try:
            await channel_layer.group_discard(group, channel_name)
        except BaseException as exc:  # noqa: BLE001
            cleanup_errors.append(exc)

    if len(cleanup_errors) == 1:
        raise cleanup_errors[0]
    if cleanup_errors:
        raise BaseExceptionGroup(
            _SUBSCRIPTION_CLEANUP_FAILURE_MESSAGE,
            cleanup_errors,
        )


async def _join_subscription_groups(
    channel_layer: BaseChannelLayer,
    channel_name: str,
    group_names: Iterable[str],
) -> list[str]:
    """Join groups and roll back every successful join if a later join fails."""
    joined_groups: list[str] = []
    try:
        for group in group_names:
            await channel_layer.group_add(group, channel_name)
            joined_groups.append(group)
    except BaseException as setup_error:
        try:
            await _cleanup_subscription_resources(
                channel_layer,
                channel_name,
                joined_groups,
            )
        except BaseException as cleanup_error:  # noqa: BLE001
            raise BaseExceptionGroup(
                _SUBSCRIPTION_ROLLBACK_FAILURE_MESSAGE,
                [setup_error, cleanup_error],
            ) from None
        raise
    return joined_groups


@asynccontextmanager
async def _subscription_cleanup_scope(
    channel_layer: BaseChannelLayer,
    channel_name: str,
    joined_groups: Iterable[str],
    listener_task: asyncio.Task[None],
) -> AsyncIterator[None]:
    """Clean subscription resources without masking an active stream failure."""
    try:
        yield
    except BaseException as stream_error:
        try:
            await _cleanup_subscription_resources(
                channel_layer,
                channel_name,
                joined_groups,
                listener_task,
            )
        except BaseException as cleanup_error:  # noqa: BLE001
            raise BaseExceptionGroup(
                _SUBSCRIPTION_STREAM_CLEANUP_FAILURE_MESSAGE,
                [stream_error, cleanup_error],
            ) from None
        raise
    else:
        await _cleanup_subscription_resources(
            channel_layer,
            channel_name,
            joined_groups,
            listener_task,
        )


class GraphQL:
    """Static helper that builds GraphQL types, queries, and mutations for managers."""

    _query_class: ClassVar[type[graphene.ObjectType] | None] = None
    _mutation_class: ClassVar[type[graphene.ObjectType] | None] = None
    _subscription_class: ClassVar[type[graphene.ObjectType] | None] = None
    _mutations: ClassVar[GraphQLMutationMap] = {}
    _query_fields: ClassVar[GraphQLFieldMap] = {}
    _subscription_fields: ClassVar[GraphQLFieldMap] = {}
    _page_type_registry: ClassVar[dict[str, type[graphene.ObjectType]]] = {}
    _subscription_payload_registry: ClassVar[dict[str, type[graphene.ObjectType]]] = {}
    graphql_type_registry: ClassVar[dict[str, type[graphene.ObjectType]]] = {}
    graphql_filter_type_registry: ClassVar[
        dict[str, type[graphene.InputObjectType]]
    ] = {}
    graphql_capability_type_registry: ClassVar[
        dict[str, type[graphene.ObjectType]]
    ] = {}
    manager_registry: ClassVar[dict[str, type[GeneralManager]]] = {}
    _search_union: ClassVar[type[graphene.Union] | None] = None
    _search_result_type: ClassVar[type[graphene.ObjectType] | None] = None
    _schema: ClassVar[graphene.Schema | None] = None

    @staticmethod
    def _get_channel_layer(strict: bool = False) -> BaseChannelLayer | None:
        """
        Compatibility wrapper for ``graphql_subscriptions.get_channel_layer_safe(strict)``.

        Returns the configured Channels layer or ``None``; raises
        ``MissingChannelLayerError`` when ``strict`` is true and no layer exists.
        """
        return _get_channel_layer_fn(strict)

    @classmethod
    def get_schema(cls) -> graphene.Schema | None:
        """
        Get the currently configured Graphene schema for the GraphQL registry.

        Returns:
            The active `graphene.Schema` instance, or `None` if no schema has been created.
        """
        return cls._schema

    @classmethod
    def reset_registry(cls) -> None:
        """
        Reset all class-level registries to their initial empty state.

        Useful in tests to isolate schema construction between test cases.
        """
        cls._query_class = None
        cls._mutation_class = None
        cls._subscription_class = None
        cls._mutations = {}
        cls._query_fields = {}
        cls._subscription_fields = {}
        cls._page_type_registry = {}
        cls._subscription_payload_registry = {}
        cls.graphql_type_registry = {}
        cls.graphql_filter_type_registry = {}
        cls.graphql_capability_type_registry = {}
        cls.manager_registry = {}
        cls._search_union = None
        cls._search_result_type = None
        cls._schema = None
        from general_manager.chat.schema_index import clear_schema_index_cache

        clear_schema_index_cache()

    @classmethod
    def register_file_upload_mutation(cls) -> None:
        """Register the generic begin-upload mutation when the feature is enabled."""

        from general_manager.uploads.graphql import register_file_upload_mutation

        register_file_upload_mutation(cls._mutations)

    @classmethod
    def get_registry_snapshot(cls) -> GraphQLRegistry:
        """
        Return a snapshot of the current registry state as a :class:`GraphQLRegistry` dataclass.

        Useful for inspecting registered types and mutations in tests without
        depending on the mutable class variables directly.
        """
        return GraphQLRegistry(
            query_class=cls._query_class,
            mutation_class=cls._mutation_class,
            subscription_class=cls._subscription_class,
            schema=cls._schema,
            mutations=dict(cls._mutations),
            query_fields=dict(cls._query_fields),
            subscription_fields=dict(cls._subscription_fields),
            page_type_registry=dict(cls._page_type_registry),
            subscription_payload_registry=dict(cls._subscription_payload_registry),
            graphql_type_registry=dict(cls.graphql_type_registry),
            graphql_filter_type_registry=dict(cls.graphql_filter_type_registry),
            graphql_capability_type_registry=dict(cls.graphql_capability_type_registry),
            manager_registry=dict(cls.manager_registry),
            search_union=cls._search_union,
            search_result_type=cls._search_result_type,
        )

    @staticmethod
    def _group_name(
        manager_class: type[GeneralManager], identification: GraphQLIdentification
    ) -> str:
        """
        Compatibility wrapper for ``graphql_subscriptions.group_name(manager_class, identification)``.

        Returns the deterministic instance channel group name for the manager
        class and identification mapping.
        """
        return _group_name_fn(manager_class, identification)

    @staticmethod
    def _class_group_name(manager_class: type[GeneralManager]) -> str:
        """
        Compatibility wrapper for ``graphql_subscriptions.class_group_name(manager_class)``.

        Returns the deterministic class-wide channel group name.
        """
        return _class_group_name_fn(manager_class)

    @staticmethod
    def _refresh_group_name(manager_class: type[GeneralManager]) -> str:
        """
        Compatibility wrapper for ``graphql_subscriptions.refresh_group_name(manager_class)``.

        Returns the deterministic manager-wide refresh group name.
        """
        return _refresh_group_name_fn(manager_class)

    @staticmethod
    async def _channel_listener(
        channel_layer: BaseChannelLayer,
        channel_name: str,
        queue: asyncio.Queue[str],
    ) -> None:
        """
        Compatibility wrapper for ``graphql_subscriptions.channel_listener(channel_layer, channel_name, queue)``.

        Enqueues non-null action values from ``gm.subscription.event`` messages
        and exits silently when cancelled.
        """
        await _channel_listener_fn(channel_layer, channel_name, queue)

    @staticmethod
    async def _channel_message_listener(
        channel_layer: BaseChannelLayer,
        channel_name: str,
        queue: asyncio.Queue[GraphQLFieldMap],
    ) -> None:
        """
        Compatibility wrapper for ``graphql_subscriptions.channel_message_listener(channel_layer, channel_name, queue)``.

        Enqueues complete ``gm.subscription.event`` messages with a present
        action and exits silently when cancelled.
        """
        await _channel_message_listener_fn(channel_layer, channel_name, queue)

    @classmethod
    def create_graphql_mutation(cls, generalManagerClass: type[GeneralManager]) -> None:
        """
        Register GraphQL mutation classes for a GeneralManager and store them in the class mutation registry.

        If the manager class has no `Interface`, this method returns without
        registering anything. Otherwise, it generates create/update/delete
        mutation classes when the manager's Interface advertises support for the
        corresponding operation: either by overriding the `InterfaceBase` method
        or by listing the operation in `Interface.get_capabilities()`. Each
        successfully generated mutation is stored on the class-level registry
        (`_mutations`) under the names `create<ManagerName>`,
        `update<ManagerName>`, and `delete<ManagerName>`. If a mutation factory
        returns `None`, that mutation kind is skipped and later supported kinds
        are still considered.

        Parameters:
            generalManagerClass (type[GeneralManager]): The GeneralManager subclass whose Interface determines which mutations are created and registered.
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
        capabilities = interface_cls.get_capabilities()

        def _supports(op_name: str, method_name: str) -> bool:
            """
            Determine whether the interface supports a given operation.

            Parameters:
                op_name (str): Logical name of the operation (e.g., "create", "update", "delete") to check against the interface's reported capabilities.
                method_name (str): Name of the InterfaceBase method to inspect on `interface_cls` to see if it has been overridden.

            Returns:
                bool: `True` if the method is overridden on the interface class or if `op_name` appears in the interface's capabilities, `False` otherwise.
            """
            method = getattr(interface_cls, method_name)
            base_method = getattr(InterfaceBase, method_name)
            method_overridden = base_method.__code__ != method.__code__
            return method_overridden or op_name in capabilities

        if _supports("create", "create"):
            create_name = f"create{generalManagerClass.__name__}"
            create_mutation = cls.generate_create_mutation_class(
                generalManagerClass, default_return_values
            )
            if create_mutation is not None:
                cls._mutations[create_name] = create_mutation
                logger.debug(
                    "registered graphql mutation",
                    context={
                        "manager": generalManagerClass.__name__,
                        "mutation": create_name,
                    },
                )

        if _supports("update", "update"):
            update_name = f"update{generalManagerClass.__name__}"
            update_mutation = cls.generate_update_mutation_class(
                generalManagerClass, default_return_values
            )
            if update_mutation is not None:
                cls._mutations[update_name] = update_mutation
                logger.debug(
                    "registered graphql mutation",
                    context={
                        "manager": generalManagerClass.__name__,
                        "mutation": update_name,
                    },
                )

        if _supports("delete", "delete"):
            delete_name = f"delete{generalManagerClass.__name__}"
            delete_mutation = cls.generate_delete_mutation_class(
                generalManagerClass, default_return_values
            )
            if delete_mutation is not None:
                cls._mutations[delete_name] = delete_mutation
                logger.debug(
                    "registered graphql mutation",
                    context={
                        "manager": generalManagerClass.__name__,
                        "mutation": delete_name,
                    },
                )

    @classmethod
    def create_graphql_interface(
        cls, generalManagerClass: type[GeneralManager]
    ) -> None:
        """
        Create and register a Graphene ObjectType for a GeneralManager class and expose its queries and subscription.

        If the manager class has no `Interface`, this method returns without
        registering anything. Otherwise it builds a Graphene type by mapping the
        manager's Interface attributes and GraphQLProperties to Graphene fields
        and resolvers, registers the resulting type and manager in the GraphQL
        registries, adds corresponding list/detail query fields, adds
        subscription fields, and adds a `capabilities` field when the manager
        exposes GraphQL permission capabilities.

        Parameters:
            generalManagerClass (type[GeneralManager]): The manager class whose Interface and GraphQLProperties are used to generate Graphene fields and resolvers.
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
        fields: GraphQLFieldMap = {}

        # Map Attribute Types to Graphene Fields
        for field_name, field_info in interface_cls.get_attribute_types().items():
            field_type = field_info["type"]
            fields[field_name] = cls._map_field_to_graphene_read(
                field_type,
                field_name,
                field_info,
            )
            resolver_name = f"resolve_{field_name}"
            if field_info.get("orm_field_kind") in {"file", "image"}:

                def resolve_stored_file(
                    manager_instance: GeneralManager,
                    info: GraphQLResolveInfo,
                    *,
                    _field_name: str = field_name,
                    _manager_name: str = generalManagerClass.__name__,
                ) -> object:
                    if not cls._check_read_permission(
                        manager_instance,
                        info,
                        _field_name,
                    ):
                        return None
                    return create_stored_file_value(
                        manager_instance,
                        info,
                        field_name=_field_name,
                        manager_name=_manager_name,
                    )

                fields[resolver_name] = resolve_stored_file
            else:
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
                element = type_args[0] if type_args else object
                if isinstance(element, type) and issubclass(element, GeneralManager):
                    graphene_field = graphene.List(
                        lambda elem=element: GraphQL.graphql_type_registry[
                            elem.__name__
                        ]
                    )
                else:
                    base_type = GraphQL._map_field_to_graphene_base_type(
                        element if isinstance(element, type) else str
                    )
                    graphene_field = graphene.List(base_type)
                resolved_type = element if isinstance(element, type) else str
            else:
                resolved_type = (
                    cast(type[object], type_args[0])
                    if type_args
                    else cast(type[object], raw_hint)
                )
                graphene_field = cls._map_field_to_graphene_read(
                    resolved_type, attr_name
                )

            fields[attr_name] = graphene_field
            fields[f"resolve_{attr_name}"] = cls._create_resolver(
                attr_name, resolved_type
            )

        capability_declarations = get_graphql_capabilities(generalManagerClass)
        if capability_declarations:
            capability_type = cls._get_or_create_capability_type(
                generalManagerClass,
                capability_declarations,
            )
            fields["capabilities"] = graphene.Field(capability_type, required=True)

            def resolve_capabilities(
                manager_instance: GeneralManager,
                info: GraphQLResolveInfo,
            ) -> dict[str, GeneralManager]:
                del info
                return {"instance": manager_instance}

            fields["resolve_capabilities"] = resolve_capabilities

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

    @classmethod
    def _get_or_create_capability_type(
        cls,
        generalManagerClass: type[GeneralManager],
        declarations: tuple[GraphQLPermissionCapability, ...],
    ) -> type[graphene.ObjectType]:
        """Build or return the generated GraphQL capabilities type for a manager."""
        type_name = f"{generalManagerClass.__name__}Capabilities"
        if type_name in cls.graphql_capability_type_registry:
            return cls.graphql_capability_type_registry[type_name]

        fields: GraphQLFieldMap = {}
        for declaration in declarations:
            fields[declaration.name] = graphene.Boolean(
                required=True,
                description=declaration.description,
            )

            def resolver(
                parent: dict[str, GeneralManager],
                info: GraphQLResolveInfo,
                *,
                capability: GraphQLPermissionCapability = declaration,
            ) -> bool:
                return get_capability_context(info).evaluate(
                    capability,
                    parent["instance"],
                )

            fields[f"resolve_{declaration.name}"] = resolver

        capability_type = type(type_name, (graphene.ObjectType,), fields)
        cls.graphql_capability_type_registry[type_name] = capability_type
        return capability_type

    @classmethod
    def register_current_user_capabilities(cls) -> None:
        """
        Register the optional provider-backed ``me`` GraphQL field.

        The field is added only when `GRAPHQL_GLOBAL_CAPABILITIES_PROVIDER`
        resolves to a provider class. Provider `graphql_fields` entries may be
        concrete Graphene fields or Python types that map through the base
        GraphQL scalar mapper. For each field, a provider method named
        `resolve_<field>` is called when present; otherwise the attribute is read
        from the current request user. Provider `graphql_capabilities` entries
        that are not `GraphQLPermissionCapability` instances are ignored.
        """
        provider = cls._get_current_user_capability_provider()
        if provider is None:
            return

        me_fields: GraphQLFieldMap = {}
        configured_fields = getattr(provider, "graphql_fields", {}) or {}
        for field_name, field_type in configured_fields.items():
            if isinstance(field_type, graphene.Field):
                me_fields[field_name] = field_type
            else:
                me_fields[field_name] = cls._map_field_to_graphene_base_type(
                    cast(type, field_type)
                )()

            def field_resolver(
                user: object,
                info: GraphQLResolveInfo,
                *,
                provider_instance: object = provider,
                configured_name: str = field_name,
            ) -> object:
                resolver = getattr(
                    provider_instance,
                    f"resolve_{configured_name}",
                    None,
                )
                if callable(resolver):
                    return resolver(user, info)
                return getattr(user, configured_name)

            me_fields[f"resolve_{field_name}"] = field_resolver

        declarations = tuple(
            declaration
            for declaration in (getattr(provider, "graphql_capabilities", ()) or ())
            if isinstance(declaration, GraphQLPermissionCapability)
        )
        if declarations:
            capability_type = cls._get_or_create_current_user_capability_type(
                declarations
            )
            me_fields["capabilities"] = graphene.Field(capability_type, required=True)

            def resolve_capabilities(
                user: object, info: GraphQLResolveInfo
            ) -> GraphQLFieldMap:
                del info
                return {"instance": user}

            me_fields["resolve_capabilities"] = resolve_capabilities

        me_type = type("Me", (graphene.ObjectType,), me_fields)
        cls._query_fields["me"] = graphene.Field(me_type)

        def resolve_me(_root: object, info: GraphQLResolveInfo) -> object:
            return getattr(info.context, "user", None)

        cls._query_fields["resolve_me"] = resolve_me

    @classmethod
    def _get_or_create_current_user_capability_type(
        cls,
        declarations: tuple[GraphQLPermissionCapability, ...],
    ) -> type[graphene.ObjectType]:
        type_name = "MeCapabilities"
        if type_name in cls.graphql_capability_type_registry:
            return cls.graphql_capability_type_registry[type_name]

        fields: GraphQLFieldMap = {}
        for declaration in declarations:
            fields[declaration.name] = graphene.Boolean(
                required=True,
                description=declaration.description,
            )

            def resolver(
                parent: GraphQLFieldMap,
                info: GraphQLResolveInfo,
                *,
                capability: GraphQLPermissionCapability = declaration,
            ) -> bool:
                return get_capability_context(info).evaluate(
                    capability,
                    parent["instance"],
                )

            fields[f"resolve_{declaration.name}"] = resolver

        capability_type = type(type_name, (graphene.ObjectType,), fields)
        cls.graphql_capability_type_registry[type_name] = capability_type
        return capability_type

    @staticmethod
    def _get_current_user_capability_provider() -> object | None:
        provider_path = get_setting("GRAPHQL_GLOBAL_CAPABILITIES_PROVIDER")
        if not provider_path:
            return None
        provider_factory = cast(Callable[[], object], import_string(str(provider_path)))
        return provider_factory()

    @staticmethod
    def _sort_by_options(
        generalManagerClass: type[GeneralManager],
    ) -> type[graphene.Enum] | None:
        """
        Create a Graphene Enum of sortable field names for a GeneralManager subclass.

        Parameters:
            generalManagerClass (type[GeneralManager]): The GeneralManager subclass to inspect for sortable attributes and GraphQL properties.

        Returns:
            type[graphene.Enum] | None: A Graphene Enum type whose members are the sortable field names for the manager, or `None` if no sortable fields exist.
        """
        sort_options = []
        for (
            field_name,
            field_info,
        ) in generalManagerClass.Interface.get_attribute_types().items():
            field_type = field_info["type"]
            if safe_issubclass(field_type, GeneralManager):
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

    @classmethod
    def register_search_query(cls) -> None:
        """Register the global search field. See ``graphql_search.register_search_query``."""
        cls._search_union, cls._search_result_type = _register_search_query_fn(
            cls._query_fields,
            cls.manager_registry,
            cls.graphql_type_registry,
            cls._search_union,
            cls._search_result_type,
        )

    @classmethod
    def _create_search_union(
        cls, type_map: dict[str, type[GeneralManager]]
    ) -> type[graphene.Union] | None:
        """Build the search union type. See ``graphql_search.create_search_union``."""
        if cls._search_union is not None:
            return cls._search_union
        result = _create_search_union_fn(type_map, cls.graphql_type_registry)
        if result is not None:
            cls._search_union = result
        return result

    @classmethod
    def _create_search_result_type(
        cls, union_type: type[graphene.Union]
    ) -> type[graphene.ObjectType]:
        """Build the search result type. See ``graphql_search.create_search_result_type``."""
        if cls._search_result_type is not None:
            return cls._search_result_type
        result = _create_search_result_type_fn(union_type)
        cls._search_result_type = result
        return result

    @classmethod
    def _parse_search_filters(
        cls,
        filters: GraphQLSearchFilterInput,
    ) -> GraphQLFilterMapping:
        """Normalise search filters. See ``graphql_search.parse_search_filters``."""
        return _parse_search_filters_fn(filters)

    @staticmethod
    def _merge_permission_filters(
        filters: GraphQLFilterMapping | None,
        permission_filters: list["PermissionConstraint"],
    ) -> list[GraphQLFilterMapping] | GraphQLFilterMapping | None:
        """Merge permission filters. See ``graphql_search.merge_permission_filters``."""
        return _merge_permission_filters_fn(filters, permission_filters)

    @staticmethod
    def _matches_filters(
        instance: GeneralManager,
        filters: GraphQLFilterMapping,
        *,
        empty_is_match: bool = True,
    ) -> bool:
        """Check filter conditions on *instance*. See ``graphql_search.matches_filters``."""
        return _matches_filters_fn(instance, filters, empty_is_match=empty_is_match)

    @classmethod
    def _passes_permission_filters(
        cls, instance: GeneralManager, info: GraphQLResolveInfo
    ) -> bool:
        """Check read permission for *instance*. See ``graphql_search.passes_permission_filters``."""
        return _passes_permission_filters_fn(instance, info)

    @staticmethod
    def _get_filter_options(
        attribute_type: type, attribute_name: str
    ) -> Generator[
        tuple[
            str,
            type[graphene.ObjectType]
            | graphene.Scalar
            | MeasurementScalar
            | graphene.List
            | None,
        ],
        None,
        None,
    ]:
        """Yield filter variants for *attribute_type*. See ``graphql_search.get_filter_options``."""
        return _get_filter_options_fn(
            attribute_type,
            attribute_name,
            GraphQL._map_field_to_graphene_filter_input,
        )

    @staticmethod
    def _create_filter_options(
        field_type: type[GeneralManager],
    ) -> type[graphene.InputObjectType] | None:
        """Build filter InputObjectType for *field_type*. See ``graphql_search.create_filter_options``."""
        raw_relation_depth = get_setting("GRAPHQL_FILTER_RELATION_DEPTH", 1)
        relation_depth = int(cast(IntCoercible, raw_relation_depth) or 0)
        return _create_filter_options_fn(
            field_type,
            GraphQL.graphql_filter_type_registry,
            GraphQL._map_field_to_graphene_filter_input,
            relation_depth=relation_depth,
        )

    @staticmethod
    def _map_field_to_graphene_filter_input(
        field_type: type,
        field_name: str,
        field_info: Mapping[str, object] | None = None,
    ) -> object:
        """Map file metadata to legacy string filters and delegate other fields."""
        orm_field_kind = field_info.get("orm_field_kind") if field_info else None
        if orm_field_kind in {"file", "image"}:
            return graphene.String()
        return GraphQL._map_field_to_graphene_read(
            field_type,
            field_name,
            field_info,
        )

    @staticmethod
    def _map_field_to_graphene_read(
        field_type: type,
        field_name: str,
        field_info: Mapping[str, object] | None = None,
    ) -> object:
        """
        Map a field type and name to the appropriate Graphene field for reads.

        `Measurement` types map to the `MeasurementType` object wrapper with a
        `target_unit` argument. `GeneralManager` relations map to a single
        Graphene field, while relation fields whose name ends with `_list` map to
        a paginated list field with `reverse`, `page`, `page_size`, `group_by`,
        generated relation filter/exclude inputs when available, and `sort_by`
        when sortable fields exist. Other types map through the scalar base
        mapper and may use `field_info["graphql_scalar"]` for supported scalar
        overrides such as `"bigint"`.

        Parameters:
            field_type (type): Python type declared on the interface.
            field_name (str): Attribute name being exposed.
            field_info (Mapping[str, object] | None): Optional attribute metadata
                from ``Interface.get_attribute_types()`` used to influence the
                GraphQL mapping, for example by selecting a custom scalar via
                keys such as ``graphql_scalar``. When omitted, the default
                scalar inference for ``field_type`` is used.

        Returns:
            object: Graphene field or type configured for the attribute.
        """
        if safe_issubclass(field_type, Measurement):
            return graphene.Field(MeasurementType, target_unit=graphene.String())
        elif safe_issubclass(field_type, GeneralManager):
            if field_name.endswith("_list"):
                attributes: GraphQLFieldMap = {
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
            orm_field_kind = field_info.get("orm_field_kind") if field_info else None
            if orm_field_kind == "file":
                return graphene.Field(StoredFile)
            if orm_field_kind == "image":
                return graphene.Field(StoredImage)
            return GraphQL._map_field_to_graphene_base_type(
                field_type,
                field_info,
            )()

    @staticmethod
    def _map_field_to_graphene_base_type(
        field_type: type,
        field_info: Mapping[str, object] | None = None,
    ) -> GrapheneBaseTypeClass:
        """Thin wrapper - see :func:`general_manager.api.graphql_errors.map_field_to_graphene_base_type`."""
        graphql_scalar = (
            cast(str | None, field_info.get("graphql_scalar")) if field_info else None
        )
        return _map_field_to_graphene_base_type_fn(field_type, graphql_scalar)

    @staticmethod
    def _parse_input(input_val: GraphQLFilterInput) -> GraphQLFilterMapping:
        """Normalise a filter/exclude input into a plain dict. See ``graphql_resolvers.parse_input``."""
        return _parse_input_fn(input_val)

    @staticmethod
    def _normalize_filter_input(
        field_type: type[GeneralManager],
        filter_input: GraphQLFilterMapping,
    ) -> dict[str, GraphQLFilterMapping]:
        """Flatten nested relation filters. See ``graphql_search.normalize_filter_input``."""
        return _normalize_filter_input_fn(field_type, filter_input)

    @staticmethod
    def _apply_query_parameters(
        queryset: Bucket[GeneralManager],
        filter_input: GraphQLFilterInput,
        exclude_input: GraphQLFilterInput,
        sort_by: graphene.Enum | None,
        reverse: bool,
        filter_normalizer: GraphQLFilterNormalizer | None = None,
    ) -> Bucket[GeneralManager]:
        """Apply filter/exclude/sort to *queryset*. See ``graphql_resolvers.apply_query_parameters``."""
        return _apply_query_parameters_fn(
            queryset,
            filter_input,
            exclude_input,
            sort_by,
            reverse,
            filter_normalizer=filter_normalizer,
        )

    @staticmethod
    def _apply_permission_filters(
        queryset: Bucket[GeneralManagerT],
        general_manager_class: type[GeneralManagerT],
        info: GraphQLResolveInfo,
    ) -> Bucket[GeneralManagerT]:
        """Apply permission filters to *queryset*. See ``graphql_resolvers.apply_permission_filters``."""
        return _apply_permission_filters_fn(queryset, general_manager_class, info)

    @staticmethod
    def _check_read_permission(
        instance: GeneralManager, info: GraphQLResolveInfo, field_name: str
    ) -> bool:
        """Check read permission for *field_name*. See ``graphql_resolvers.check_read_permission``."""
        return _check_read_permission_fn(instance, info, field_name)

    @staticmethod
    def _create_list_resolver(
        base_getter: GraphQLListGetter,
        fallback_manager_class: type[GeneralManager],
    ) -> GraphQLResolver:
        """Build a list-field resolver. See ``graphql_resolvers.create_list_resolver``."""
        return _create_list_resolver_fn(
            base_getter,
            fallback_manager_class,
            _normalize_filter_input_fn,
        )

    @staticmethod
    def _apply_pagination(
        queryset: Bucket[GeneralManager] | GroupBucket[GeneralManager],
        page: int | None,
        page_size: int | None,
    ) -> Bucket[GeneralManager] | GroupBucket[GeneralManager]:
        """Paginate *queryset*. See ``graphql_resolvers.apply_pagination``."""
        return _apply_pagination_fn(queryset, page, page_size)

    @staticmethod
    def _apply_grouping(
        queryset: Bucket[GeneralManager], group_by: list[str] | None
    ) -> Bucket[GeneralManager] | GroupBucket[GeneralManager]:
        """Group *queryset*. See ``graphql_resolvers.apply_grouping``."""
        return _apply_grouping_fn(queryset, group_by)

    @staticmethod
    def _create_measurement_resolver(field_name: str) -> GraphQLResolver:
        """Build a Measurement-field resolver. See ``graphql_resolvers.create_measurement_resolver``."""
        return _create_measurement_resolver_fn(field_name)

    @staticmethod
    def _create_normal_resolver(field_name: str) -> GraphQLResolver:
        """Build a scalar-field resolver. See ``graphql_resolvers.create_normal_resolver``."""
        return _create_normal_resolver_fn(field_name)

    @classmethod
    def _create_resolver(cls, field_name: str, field_type: type) -> GraphQLResolver:
        """Dispatch to the appropriate resolver factory. See ``graphql_resolvers.create_resolver``."""
        return _create_resolver_fn(field_name, field_type, _normalize_filter_input_fn)

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
        cls, generalManagerClass: type[GeneralManager]
    ) -> GraphQLFieldMap:
        """
        Build the GraphQL arguments required to uniquely identify an instance of the given manager class.

        For each input field defined on the manager's Interface: use "<name>_id" for fields that reference another GeneralManager, use "id" when present, and map other fields to their corresponding Graphene base type. Each argument's nullability mirrors `input_field.required`.

        Parameters:
            generalManagerClass: GeneralManager subclass whose Interface.input_fields are used to derive identification arguments.

        Returns:
            dict[str, object]: Mapping of argument name to a Graphene Argument suitable for identifying a single manager instance.
        """
        identification_fields: GraphQLFieldMap = {}
        for (
            input_field_name,
            input_field,
        ) in generalManagerClass.Interface.input_fields.items():
            if safe_issubclass(input_field.type, GeneralManager):
                key = f"{input_field_name}_id"
                identification_fields[key] = graphene.Argument(
                    graphene.ID, required=input_field.required
                )
            elif input_field_name == "id":
                identification_fields[input_field_name] = graphene.Argument(
                    graphene.ID, required=input_field.required
                )
            else:
                base_type = cls._map_field_to_graphene_base_type(input_field.type)
                identification_fields[input_field_name] = graphene.Argument(
                    base_type, required=input_field.required
                )
        return identification_fields

    @classmethod
    def _add_queries_to_schema(
        cls,
        graphene_type: type[graphene.ObjectType],
        generalManagerClass: type[GeneralManager],
    ) -> None:
        """
        Registers list and detail GraphQL query fields for the given manager type into the class query registry.

        Parameters:
            graphene_type (type): The Graphene ObjectType that represents the manager's GraphQL type.
            generalManagerClass (type[GeneralManager]): The GeneralManager subclass to expose via queries.

        Raises:
            TypeError: If `generalManagerClass` is not a subclass of GeneralManager.
        """
        if not issubclass(generalManagerClass, GeneralManager):
            raise InvalidGeneralManagerClassError(generalManagerClass)

        if not hasattr(cls, "_query_fields"):
            cls._query_fields = {}

        # resolver and field for the list query
        manager_field_name = pascal_to_snake(generalManagerClass.__name__)
        list_field_name = f"{manager_field_name}_list"
        attributes: GraphQLFieldMap = {
            "reverse": graphene.Boolean(),
            "page": graphene.Int(),
            "page_size": graphene.Int(),
            "group_by": graphene.List(graphene.String),
        }
        from general_manager.interface.capabilities.orm.support import (
            is_soft_delete_enabled,
        )
        from general_manager.interface.orm_interface import OrmInterfaceBase

        interface_cls = cast(
            type[OrmInterfaceBase[models.Model]],
            generalManagerClass.Interface,
        )
        if is_soft_delete_enabled(interface_cls):
            attributes["include_inactive"] = graphene.Boolean()
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

        def _all_items(
            _: object,
            include_inactive: bool,
        ) -> Bucket[GeneralManager] | None:
            """
            Return all instances for the associated GeneralManager class.

            Returns:
                All instances for the associated GeneralManager class, typically provided as a Bucket/QuerySet-like iterable.
            """
            if include_inactive:
                return generalManagerClass.filter(include_inactive=True)
            return generalManagerClass.all()

        list_resolver = cls._create_list_resolver(_all_items, generalManagerClass)
        cls._query_fields[list_field_name] = list_field
        cls._query_fields[f"resolve_{list_field_name}"] = list_resolver

        # resolver and field for the single item query
        item_field_name = manager_field_name
        identification_fields = cls._build_identification_arguments(generalManagerClass)
        item_field = graphene.Field(graphene_type, **identification_fields)

        def resolver(
            self: GeneralManager, info: GraphQLResolveInfo, **identification: object
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
        Compatibility wrapper for ``graphql_subscriptions.prime_graphql_properties(instance, property_names)``.

        Reads selected GraphQL properties on ``instance`` to trigger dependency
        tracking; unknown requested names are ignored and property exceptions
        propagate.
        """
        _prime_graphql_properties_fn(instance, property_names)

    @classmethod
    def _dependencies_from_tracker(
        cls, dependency_records: Iterable[Dependency]
    ) -> list[tuple[type[GeneralManager], GraphQLIdentification]]:
        """
        Compatibility wrapper for ``graphql_subscriptions.dependencies_from_tracker(dependency_records, manager_registry)``.

        Uses ``GraphQL.manager_registry`` and returns accepted
        ``(manager_class, identification)`` pairs from identification dependency
        records.
        """
        return _dependencies_from_tracker_fn(dependency_records, cls.manager_registry)

    @classmethod
    def _subscription_property_names(
        cls,
        info: GraphQLResolveInfo,
        manager_class: type[GeneralManager],
    ) -> set[str]:
        """
        Compatibility wrapper for ``graphql_subscriptions.subscription_property_names(info, manager_class, normalize_graphql_name)``.

        Uses ``GraphQL._normalize_graphql_name`` and returns selected
        GraphQLProperty names under the subscription payload's ``item`` field.
        """
        return _subscription_property_names_fn(
            info, manager_class, cls._normalize_graphql_name
        )

    @classmethod
    def _resolve_subscription_dependencies(
        cls,
        manager_class: type[GeneralManager],
        instance: GeneralManager,
        dependency_records: Iterable[Dependency] | None = None,
    ) -> list[tuple[type[GeneralManager], GraphQLIdentification]]:
        """
        Compatibility wrapper for ``graphql_subscriptions.resolve_subscription_dependencies(manager_class, instance, manager_registry, dependency_records)``.

        Uses ``GraphQL.manager_registry`` and returns deduplicated dependency
        channel targets for a subscription instance.
        """
        return _resolve_subscription_dependencies_fn(
            manager_class, instance, cls.manager_registry, dependency_records
        )

    @staticmethod
    def _instantiate_manager(
        manager_class: type[GeneralManager],
        identification: GraphQLIdentification,
        *,
        collect_dependencies: bool = False,
        property_names: Iterable[str] | None = None,
    ) -> tuple[GeneralManager, set[Dependency]]:
        """
        Compatibility wrapper for ``graphql_subscriptions.instantiate_manager(manager_class, identification, collect_dependencies, property_names)``.

        Constructs the manager and optionally captures dependency tracker
        records while priming selected GraphQL properties.
        """
        return _instantiate_manager_fn(
            manager_class,
            identification,
            collect_dependencies=collect_dependencies,
            property_names=property_names,
        )

    @classmethod
    def _add_subscription_field(
        cls,
        graphene_type: type[graphene.ObjectType],
        generalManagerClass: type[GeneralManager],
    ) -> None:
        """
        Register a GraphQL subscription field that publishes change events for the given manager type.

        Creates (or reuses) a SubscriptionEvent payload GraphQL type and adds three entries to the class subscription registry:
        - a field exposing the subscription with identification arguments,
        - an async subscribe function that yields an initial "snapshot" event and subsequent change events for the identified instance and its dependencies,
        - and a resolve function that returns the delivered payload.

        Parameters:
            graphene_type (type[graphene.ObjectType]): GraphQL ObjectType representing the manager's item type used as the payload `item` field.
            generalManagerClass (type[GeneralManager]): The GeneralManager subclass whose changes the subscription will publish.

        Notes:
        - The subscribe function requires an available channel layer and subscribes the caller to channel groups derived from the instance identification and its resolved dependencies.
        - The subscribe coroutine yields SubscriptionEvent objects with fields `item` (the current instance or None if it cannot be instantiated) and `action` (a string such as `"snapshot"` or other change actions).
        - On termination the subscription cleans up listener tasks and unsubscribes from channel groups.
        """
        manager_field_name = pascal_to_snake(generalManagerClass.__name__)
        field_name = f"on_{manager_field_name}_change"
        class_field_name = f"on_{manager_field_name}_class_change"
        if (
            field_name in cls._subscription_fields
            and class_field_name in cls._subscription_fields
        ):
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
        class_subscription_field = graphene.Field(payload_type)

        async def subscribe(
            _root: object,
            info: GraphQLResolveInfo,
            **identification: object,
        ) -> AsyncIterator[SubscriptionEvent]:
            """
            Stream subscription events for a specific manager instance identified by the provided arguments.

            Yields an initial `SubscriptionEvent` with `action` set to `"snapshot"` containing the current manager instance, then yields `SubscriptionEvent`s for each subsequent action. For subsequent events, `item` will be the re-instantiated manager instance or `None` if instantiation fails. The subscriber is registered on the manager's channel groups (including dependent managers' groups) and the channel subscriptions and background listener are cleaned up when the iterator is closed or cancelled.

            Parameters:
                identification: Identification fields required to locate the manager instance (maps to the manager's identification signature).

            Returns:
                AsyncIterator[SubscriptionEvent]: An asynchronous iterator that first yields a snapshot event and then yields update events; each event's `item` is the manager instance or `None` if it could not be instantiated.
            """
            identification_copy = deepcopy(identification)
            property_names = cls._subscription_property_names(info, generalManagerClass)
            try:
                instance, dependency_records = await asyncio.to_thread(
                    cls._instantiate_manager,
                    generalManagerClass,
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
                    generalManagerClass,
                    instance.identification,
                ),
                cls._refresh_group_name(generalManagerClass),
            }
            dependencies = cls._resolve_subscription_dependencies(
                generalManagerClass,
                instance,
                dependency_records,
            )
            for dependency_class, dependency_identification in dependencies:
                group_names.add(
                    cls._group_name(dependency_class, dependency_identification)
                )
                group_names.add(cls._refresh_group_name(dependency_class))

            joined_groups = await _join_subscription_groups(
                channel_layer,
                channel_name,
                group_names,
            )

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
                async with _subscription_cleanup_scope(
                    channel_layer,
                    channel_name,
                    joined_groups,
                    listener_task,
                ):
                    clear_capability_context(info)
                    yield SubscriptionEvent(item=instance, action="snapshot")
                    while True:
                        action = await queue.get()
                        try:
                            item, _ = await asyncio.to_thread(
                                cls._instantiate_manager,
                                generalManagerClass,
                                identification_copy,
                                property_names=property_names,
                            )
                        except SUBSCRIPTION_HYDRATION_ERRORS:
                            item = None
                        clear_capability_context(info)
                        yield SubscriptionEvent(item=item, action=action)

            return event_stream()

        def resolve(
            payload: SubscriptionEvent,
            info: GraphQLResolveInfo,
            **_: object,
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

        async def subscribe_class(
            _root: object,
            info: GraphQLResolveInfo,
            **_: object,
        ) -> AsyncIterator[SubscriptionEvent]:
            """
            Stream future authorized change events for any instance of a manager class.

            Unlike single-entity subscriptions, class-wide subscriptions do not
            yield an initial snapshot. Aggregate refresh events carry no item;
            ordinary row events are hydrated and checked against the request
            user's object-level read permission before they are yielded.
            """
            try:
                channel_layer = cast(
                    BaseChannelLayer, cls._get_channel_layer(strict=True)
                )
            except RuntimeError as exc:
                raise GraphQLError(str(exc)) from exc
            channel_name = cast(str, await channel_layer.new_channel())
            queue: asyncio.Queue[GraphQLFieldMap] = asyncio.Queue()
            group_names = {
                cls._class_group_name(generalManagerClass),
                cls._refresh_group_name(generalManagerClass),
            }
            joined_groups = await _join_subscription_groups(
                channel_layer,
                channel_name,
                group_names,
            )
            listener_task = asyncio.create_task(
                cls._channel_message_listener(channel_layer, channel_name, queue)
            )

            async def event_stream() -> AsyncIterator[SubscriptionEvent]:
                async with _subscription_cleanup_scope(
                    channel_layer,
                    channel_name,
                    joined_groups,
                    listener_task,
                ):
                    while True:
                        message = await queue.get()
                        if message.get("manager") != generalManagerClass.__name__:
                            continue
                        action = message.get("action")
                        if not isinstance(action, str):
                            continue
                        if action == "refresh":
                            clear_capability_context(info)
                            yield SubscriptionEvent(item=None, action=action)
                            continue
                        identification = message.get("identification")
                        if not isinstance(identification, dict):
                            continue
                        identification_copy = deepcopy(identification)
                        try:
                            item, _ = await asyncio.to_thread(
                                cls._instantiate_manager,
                                generalManagerClass,
                                identification_copy,
                            )
                        except SUBSCRIPTION_HYDRATION_ERRORS:
                            continue
                        if not _can_read_instance_fn(item, info):
                            continue
                        clear_capability_context(info)
                        yield SubscriptionEvent(item=item, action=action)

            return event_stream()

        if field_name not in cls._subscription_fields:
            cls._subscription_fields[field_name] = subscription_field
            cls._subscription_fields[f"subscribe_{field_name}"] = subscribe
            cls._subscription_fields[f"resolve_{field_name}"] = resolve
        if class_field_name not in cls._subscription_fields:
            cls._subscription_fields[class_field_name] = class_subscription_field
            cls._subscription_fields[f"subscribe_{class_field_name}"] = subscribe_class
            cls._subscription_fields[f"resolve_{class_field_name}"] = resolve

    @classmethod
    def create_write_fields(
        cls,
        interface_cls: type[InterfaceBase],
        *,
        require_fields: bool = True,
    ) -> GraphQLFieldMap:
        """Thin wrapper - see :func:`general_manager.api.graphql_mutations.create_write_fields`."""
        return cast(
            GraphQLFieldMap,
            _create_write_fields_fn(interface_cls, require_fields=require_fields),
        )

    @classmethod
    def generate_create_mutation_class(
        cls,
        generalManagerClass: type[GeneralManager],
        default_return_values: GraphQLFieldMap,
    ) -> type[graphene.Mutation] | None:
        """Thin wrapper - see :func:`general_manager.api.graphql_mutations.generate_create_mutation_class`."""
        return _generate_create_mutation_class_fn(
            generalManagerClass, default_return_values
        )

    @classmethod
    def generate_update_mutation_class(
        cls,
        generalManagerClass: type[GeneralManager],
        default_return_values: GraphQLFieldMap,
    ) -> type[graphene.Mutation] | None:
        """Thin wrapper - see :func:`general_manager.api.graphql_mutations.generate_update_mutation_class`."""
        return _generate_update_mutation_class_fn(
            generalManagerClass, default_return_values
        )

    @classmethod
    def generate_delete_mutation_class(
        cls,
        generalManagerClass: type[GeneralManager],
        default_return_values: GraphQLFieldMap,
    ) -> type[graphene.Mutation] | None:
        """Thin wrapper - see :func:`general_manager.api.graphql_mutations.generate_delete_mutation_class`."""
        return _generate_delete_mutation_class_fn(
            generalManagerClass, default_return_values
        )

    @staticmethod
    def _handle_graph_ql_error(
        error: Exception,
        *,
        field_name_mapper: ValidationFieldNameMapper | None = None,
    ) -> GraphQLError:
        """Thin wrapper - see :func:`general_manager.api.graphql_errors.handle_graph_ql_error`."""
        return _handle_graph_ql_error_fn(error, field_name_mapper=field_name_mapper)

    @classmethod
    def _handle_data_change(
        cls,
        sender: type[GeneralManager] | GeneralManager,
        instance: GeneralManager | None,
        action: str,
        previous_instance: GeneralManager | None = None,
        identification: GraphQLIdentification | None = None,
        **_: object,
    ) -> None:
        """
        Send a "gm.subscription.event" message to the channel group corresponding to a changed GeneralManager instance.

        If the provided instance is a registered GeneralManager and a channel
        layer is configured, queue one manager-wide refresh while notification
        batching is active. Outside a batch, publish the row-level message to
        the instance and class-wide groups through one async bridge.
        Identification comes from the provided `identification` mapping when
        supplied, otherwise from the changed instance, and is deep-copied
        before dispatch. If the instance is `None`, the manager type is not
        registered, or no channel layer is available, the function returns
        without dispatching. Ordinary group dispatch failures are logged and
        do not stop attempts for remaining groups.

        Parameters:
            sender (type[GeneralManager] | GeneralManager): The signal sender; either a GeneralManager subclass or an instance.
            instance (GeneralManager | None): The GeneralManager instance that changed.
            action (str): A string describing the change action (e.g., "created", "updated", "deleted").
        """
        event_instance = instance if instance is not None else previous_instance
        if event_instance is None or not isinstance(event_instance, GeneralManager):
            return

        if isinstance(sender, type) and issubclass(sender, GeneralManager):
            manager_class: type[GeneralManager] = sender
        else:
            manager_class = event_instance.__class__

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

        event_identification = (
            deepcopy(identification)
            if identification is not None
            else deepcopy(event_instance.identification)
        )
        group_name = cls._group_name(manager_class, event_identification)
        class_group_name = cls._class_group_name(manager_class)
        message: dict[str, object] = {
            "type": "gm.subscription.event",
            "action": action,
            "manager": manager_class.__name__,
            "identification": event_identification,
        }
        refresh_group_name = cls._refresh_group_name(manager_class)
        refresh_message: dict[str, object] = {
            "type": "gm.subscription.event",
            "action": "refresh",
            "manager": manager_class.__name__,
        }
        if _queue_notification(
            key=("graphql", refresh_group_name),
            group_send=channel_layer.group_send,
            group=refresh_group_name,
            message=refresh_message,
        ):
            return

        target_groups = (group_name, class_group_name)
        try:
            dispatched = async_to_sync(_dispatch_subscription_event_fn)(
                channel_layer,
                target_groups,
                message,
            )
        except MemoryError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "failed to dispatch subscription event",
                context={
                    "manager": manager_class.__name__,
                    "action": action,
                    "target_groups": list(target_groups),
                    "message": message,
                },
                exc_info=exc,
            )
            return
        if dispatched > 0:
            logger.debug(
                "dispatched subscription event",
                context={
                    "manager": manager_class.__name__,
                    "action": action,
                    "target_groups": list(target_groups),
                    "successful_group_count": dispatched,
                },
            )


post_data_change.connect(GraphQL._handle_data_change, weak=False)
