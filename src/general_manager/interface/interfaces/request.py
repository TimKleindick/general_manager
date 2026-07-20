"""Interface implementation for request-backed GeneralManager classes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Protocol, TYPE_CHECKING, cast

from general_manager.bucket.base_bucket import Bucket
from general_manager.as_of import ensure_as_of_read_supported
from general_manager.manager.input import Input
from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.bundles.request import (
    REQUEST_CORE_CAPABILITIES,
)
from general_manager.interface.capabilities.base import CapabilityName
from general_manager.interface.capabilities.configuration import (
    CapabilityConfigEntry,
    InterfaceCapabilityConfig,
    iter_capability_entries,
)
from general_manager.interface.capabilities.request import (
    RequestCreateCapability,
    RequestDeleteCapability,
    RequestUpdateCapability,
)
from general_manager.interface.requests import (
    MissingRequestPayloadFieldError,
    MissingRequestTransportError,
    RequestAuthProvider,
    RequestConfigurationError,
    RequestField,
    RequestFilter,
    RequestMutationOperation,
    RequestPayload,
    RequestQueryOperation,
    RequestQueryPlan,
    RequestQueryResult,
    RequestRetryPolicy,
    RequestSchemaError,
    RequestSerializer,
    RequestTransport,
    RequestTransportConfig,
    UnknownRequestOperationError,
    default_request_response_normalizer,
    resolve_request_value,
)

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager


class _RequestQueryOperationHandler(Protocol):
    """Capability protocol used by `RequestInterface.query_operation()`."""

    def for_operation(
        self,
        interface_cls: type[RequestInterface],
        operation_name: str,
        **kwargs: object,
    ) -> Bucket[GeneralManager]:
        """Build a bucket for a named request query operation."""
        ...


class RequestInterface(InterfaceBase):
    """Base interface for request-backed resources with declarative operations.

    Subclasses declare `RequestField` attributes on the interface and request
    filters, query operations, mutation operations, transports, auth providers,
    retry policies, serializers, and rules on `Meta`. Request fields are
    collected across the subclass MRO so derived interfaces can inherit or
    override field declarations. Legacy top-level request configuration raises
    `RequestConfigurationError` during subclass creation. Declared mutation
    operations automatically add the corresponding request mutation capabilities.

    At subclass creation, `Meta.filters`, `Meta.query_operations`,
    `Meta.default_query_operation`, `Meta.transport`, `Meta.transport_config`,
    `Meta.auth_provider`, `Meta.retry_policy`, `Meta.create_operation`,
    `Meta.update_operation`, `Meta.delete_operation`, `Meta.create_serializer`,
    `Meta.update_serializer`, `Meta.response_serializer`, and `Meta.rules` are
    copied onto same-named interface class attributes. Omitted values use the
    class defaults declared below.

    Request queries return lazy request buckets through the configured query
    capability. Instance field reads resolve values from the cached response
    payload and raise `MissingRequestPayloadFieldError` when a required declared
    field is absent.
    """

    _interface_type: ClassVar[str] = "request"
    input_fields: ClassVar[dict[str, Input[type[object]]]] = {}
    identification_fields: ClassVar[tuple[str, ...]] = ("id",)
    fields: ClassVar[Mapping[str, RequestField]] = {}
    filters: ClassVar[Mapping[str, RequestFilter]] = {}
    query_operations: ClassVar[Mapping[str, RequestQueryOperation]] = {}
    create_operation: ClassVar[RequestMutationOperation | None] = None
    update_operation: ClassVar[RequestMutationOperation | None] = None
    delete_operation: ClassVar[RequestMutationOperation | None] = None
    default_query_operation: ClassVar[str] = "list"
    transport: ClassVar[RequestTransport | None] = None
    transport_config: ClassVar[RequestTransportConfig | None] = None
    auth_provider: ClassVar[RequestAuthProvider | None] = None
    retry_policy: ClassVar[RequestRetryPolicy | None] = None
    create_serializer: ClassVar[object | None] = None
    update_serializer: ClassVar[object | None] = None
    response_serializer: ClassVar[RequestSerializer | None] = None
    rules: ClassVar[list[object]] = []
    _request_payload_cache: RequestPayload | None = None

    configured_capabilities: ClassVar[tuple[CapabilityConfigEntry, ...]] = (
        REQUEST_CORE_CAPABILITIES,
    )
    lifecycle_capability_name: ClassVar[CapabilityName | None] = "request_lifecycle"

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Reject historical construction before parsing request inputs."""
        ensure_as_of_read_supported(type(self))
        super().__init__(*args, **kwargs)

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if cls is RequestInterface:
            return
        legacy_request_keys = (
            "fields",
            "filters",
            "query_operations",
            "default_query_operation",
            "transport",
            "transport_config",
        )
        for legacy_key in legacy_request_keys:
            if legacy_key in vars(cls):
                raise RequestConfigurationError.legacy_declaration(
                    cls.__name__,
                    legacy_key,
                )
        meta_class = getattr(cls, "Meta", None)
        fields: dict[str, RequestField] = {}
        for base in reversed(cls.__mro__):
            for key, value in vars(base).items():
                if isinstance(value, RequestField):
                    fields[key] = value
        cls.fields = fields
        cls.filters = dict(getattr(meta_class, "filters", {}))
        cls.query_operations = dict(getattr(meta_class, "query_operations", {}))
        cls.default_query_operation = getattr(
            meta_class,
            "default_query_operation",
            cls.default_query_operation,
        )
        cls.transport = getattr(meta_class, "transport", None)
        cls.transport_config = getattr(meta_class, "transport_config", None)
        cls.auth_provider = getattr(meta_class, "auth_provider", None)
        cls.retry_policy = getattr(meta_class, "retry_policy", None)
        cls.create_operation = getattr(meta_class, "create_operation", None)
        cls.update_operation = getattr(meta_class, "update_operation", None)
        cls.delete_operation = getattr(meta_class, "delete_operation", None)
        cls.create_serializer = getattr(meta_class, "create_serializer", None)
        cls.update_serializer = getattr(meta_class, "update_serializer", None)
        cls.response_serializer = getattr(meta_class, "response_serializer", None)
        cls.rules = list(getattr(meta_class, "rules", []))
        cls._sync_configured_capabilities()

    @classmethod
    def _sync_configured_capabilities(cls) -> None:
        base_capabilities = tuple(getattr(cls, "configured_capabilities", tuple()))
        mutation_capabilities: list[InterfaceCapabilityConfig] = []
        if cls.create_operation is not None:
            mutation_capabilities.append(
                InterfaceCapabilityConfig(RequestCreateCapability)
            )
        if cls.update_operation is not None:
            mutation_capabilities.append(
                InterfaceCapabilityConfig(RequestUpdateCapability)
            )
        if cls.delete_operation is not None:
            mutation_capabilities.append(
                InterfaceCapabilityConfig(RequestDeleteCapability)
            )
        existing_handlers = {
            config.handler for config in iter_capability_entries(base_capabilities)
        }
        cls.configured_capabilities = base_capabilities + tuple(
            capability
            for capability in mutation_capabilities
            if capability.handler not in existing_handlers
        )
        cls.capability_overrides = dict(getattr(cls, "capability_overrides", {}))
        for name, override in cls._build_configured_capability_overrides().items():
            cls.capability_overrides[name] = override
        cls._configured_capabilities_applied = False

    @classmethod
    def get_query_operation(
        cls,
        operation_name: str | None = None,
    ) -> RequestQueryOperation:
        """Return a named query operation, falling back to interface-level filters.

        `None` and empty names resolve to `default_query_operation`. When that
        default operation is not explicitly declared, this method returns a
        synthetic operation with `path=""` and the interface-level filters.
        Declared operations whose own `filters` value is `None` inherit
        interface-level filters; declared operations with a filter mapping keep
        that operation-specific mapping.

        Raises:
            UnknownRequestOperationError: If a non-default operation name is not
                declared.
        """

        resolved_name = operation_name or cls.default_query_operation
        operation = cls.query_operations.get(resolved_name)
        if operation is None:
            if resolved_name == cls.default_query_operation:
                return RequestQueryOperation(
                    name=resolved_name,
                    path="",
                    filters=cls.filters,
                )
            raise UnknownRequestOperationError(resolved_name)
        if operation.filters is not None:
            return operation
        return RequestQueryOperation(
            name=operation.name,
            method=operation.method,
            path=operation.path,
            collection=operation.collection,
            filters=cls.filters,
            metadata=operation.metadata,
            static_query_params=operation.static_query_params,
            static_headers=operation.static_headers,
            static_body=operation.static_body,
            timeout=operation.timeout,
        )

    @classmethod
    def query_operation(
        cls,
        operation_name: str,
        **kwargs: object,
    ) -> Bucket[GeneralManager]:
        """Build a bucket for a named collection operation declared on the interface.

        Args:
            operation_name: Name of a configured `RequestQueryOperation`.
            **kwargs: Lookup values compiled by the request query capability.

        Returns:
            A lazy request bucket for the interface's parent manager.

        Raises:
            UnknownRequestOperationError: If the named operation is not declared.
            NotImplementedError: If the configured query capability does not
                implement named request operations.
            RequestConfigurationError: If request planning fails for the
                operation, filters, or transport setup.
            Exception: Request planning errors from the active query capability,
                including unknown filters, invalid filter values, unsupported
                excludes, invalid request-fragment locations, and conflicting
                fragment keys, propagate unchanged.
        """

        ensure_as_of_read_supported(cls)
        handler = cls.require_capability("query")
        if hasattr(handler, "for_operation"):
            operation_handler = cast(_RequestQueryOperationHandler, handler)
            return operation_handler.for_operation(cls, operation_name, **kwargs)
        raise NotImplementedError

    def set_request_payload_cache(self, payload: RequestPayload | None) -> None:
        """Cache the raw response payload used to populate request-backed fields.

        Passing `None` clears the cache. The method does not validate payload
        shape; individual field reads perform path resolution and required-field
        checks.
        """

        self._request_payload_cache = payload

    @classmethod
    def get_mutation_operation(cls, action: str) -> RequestMutationOperation:
        """Return the configured mutation operation for an action.

        Args:
            action: One of `"create"`, `"update"`, or `"delete"`.

        Raises:
            NotImplementedError: If the interface does not declare that mutation
                operation.
        """

        if action == "create":
            operation = cls.create_operation
        elif action == "update":
            operation = cls.update_operation
        elif action == "delete":
            operation = cls.delete_operation
        else:
            operation = None
        if operation is None:
            raise NotImplementedError(
                f"{cls.__name__} does not declare a '{action}' request operation."
            )
        return operation

    @classmethod
    def extract_identification(cls, payload: RequestPayload) -> dict[str, object]:
        """Extract manager identification values from a query payload.

        Returns values for every field named in `identification_fields`, resolving
        declared request-field source paths where present.

        Raises:
            MissingRequestPayloadFieldError: If a required identification value is
                absent from the payload.
        """

        identification: dict[str, object] = {}
        for field_name in cls.identification_fields:
            identification[field_name] = cls.resolve_payload_value(payload, field_name)
        return identification

    @classmethod
    def resolve_payload_value(
        cls,
        payload: RequestPayload,
        field_name: str,
    ) -> object:
        """Resolve a declared request field from a payload mapping.

        Undeclared fields are resolved by their own name. Declared fields use the
        `RequestField` source path, return the field default when optional data is
        missing, and apply the field normalizer when one is configured.

        Raises:
            MissingRequestPayloadFieldError: If a required value is missing.
        """

        field = cls.fields.get(field_name)
        if field is None:
            path: tuple[str, ...] = (field_name,)
            try:
                return resolve_request_value(payload, path)
            except KeyError as error:
                raise MissingRequestPayloadFieldError(field_name, path) from error

        path = field.value_path(field_name)
        try:
            value = resolve_request_value(payload, path)
        except KeyError as error:
            if field.is_required:
                raise MissingRequestPayloadFieldError(field_name, path) from error
            return field.default
        if field.normalizer is not None:
            return field.normalizer(value)
        return value

    @classmethod
    def execute_request_plan(
        cls,
        plan: RequestQueryPlan,
    ) -> RequestQueryResult:
        """Execute a compiled request plan and return normalized payload items.

        The configured transport may return either a `RequestQueryResult` or a
        decoded request response accepted by `default_request_response_normalizer`.
        A callable `response_serializer` is applied to each normalized item and
        must return mapping-shaped payloads.
        Plans with `create`, `update`, or `delete` actions use the corresponding
        mutation operation; every other action string, including unknown or empty
        actions, uses the plan's `operation_name` to resolve a query operation.
        The plan's `path_params` are forwarded to the transport as request
        identification.
        Auth, retry, metrics, trace, and transport behavior is owned by the
        configured transport object; errors from those hooks propagate according
        to that transport implementation.

        Raises:
            MissingRequestTransportError: If no transport is configured.
            RequestSchemaError: If the response serializer returns non-mapping
                items or the default normalizer rejects the transport payload.
            NotImplementedError: If a create, update, or delete plan targets an
                undeclared mutation operation.
            UnknownRequestOperationError: If a non-mutation plan targets an
                undeclared query operation.
        """

        transport = cls.transport
        if transport is None:
            raise MissingRequestTransportError(cls.__name__)
        operation: RequestQueryOperation | RequestMutationOperation
        if plan.action in {"create", "update", "delete"}:
            operation = cls.get_mutation_operation(plan.action)
        else:
            operation = cls.get_query_operation(plan.operation_name)
        result = transport.execute(
            interface_cls=cls,
            operation=operation,
            plan=plan,
            identification=dict(plan.path_params),
        )
        if not isinstance(result, RequestQueryResult):
            result = default_request_response_normalizer(
                result,
                interface_cls=cls,
                operation=operation,
                plan=plan,
            )
        serializer = cls.response_serializer
        if not callable(serializer):
            return result
        normalized_items = tuple(serializer(item) for item in result.items)
        if not all(isinstance(item, Mapping) for item in normalized_items):
            raise RequestSchemaError.serializer_must_return_mappings(cls.__name__)
        return RequestQueryResult(
            items=cast(tuple[RequestPayload, ...], normalized_items),
            total_count=result.total_count,
            metadata=result.metadata,
        )
