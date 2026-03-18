"""Interface implementation for request-backed GeneralManager classes."""

from __future__ import annotations

from typing import Any, ClassVar, Mapping, cast

from general_manager.bucket.base_bucket import Bucket
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
    RequestQueryOperation,
    RequestQueryPlan,
    RequestQueryResult,
    RequestRetryPolicy,
    RequestSchemaError,
    RequestTransport,
    RequestTransportConfig,
    UnknownRequestOperationError,
    resolve_request_value,
)


class RequestInterface(InterfaceBase):
    """Base interface for request-backed resources with declarative query filters."""

    _interface_type: ClassVar[str] = "request"
    input_fields: ClassVar[dict[str, Any]] = {}
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
    create_serializer: ClassVar[Any | None] = None
    update_serializer: ClassVar[Any | None] = None
    response_serializer: ClassVar[Any | None] = None
    rules: ClassVar[list[Any]] = []
    _request_payload_cache: Mapping[str, Any] | None = None

    configured_capabilities: ClassVar[tuple[CapabilityConfigEntry, ...]] = (
        REQUEST_CORE_CAPABILITIES,
    )
    lifecycle_capability_name: ClassVar[CapabilityName | None] = "request_lifecycle"

    def __init_subclass__(cls, **kwargs: Any) -> None:
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
        cls.fields = {
            key: value
            for key, value in vars(cls).items()
            if isinstance(value, RequestField)
        }
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
        """Return a named query operation, falling back to interface-level filters."""

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
        if operation.filters:
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
    def query_operation(cls, operation_name: str, **kwargs: Any) -> Bucket[Any]:
        """Build a bucket for a named collection operation declared on the interface."""

        handler = cls.require_capability("query")
        if hasattr(handler, "for_operation"):
            return handler.for_operation(cls, operation_name, **kwargs)
        raise NotImplementedError

    def set_request_payload_cache(self, payload: Mapping[str, Any] | None) -> None:
        """Cache the raw response payload used to populate request-backed fields."""

        self._request_payload_cache = payload

    @classmethod
    def get_mutation_operation(cls, action: str) -> RequestMutationOperation:
        operation = {
            "create": cls.create_operation,
            "update": cls.update_operation,
            "delete": cls.delete_operation,
        }.get(action)
        if operation is None:
            raise NotImplementedError(
                f"{cls.__name__} does not declare a '{action}' request operation."
            )
        return operation

    @classmethod
    def extract_identification(cls, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Extract manager identification values from a query payload."""

        identification: dict[str, Any] = {}
        for field_name in cls.identification_fields:
            identification[field_name] = cls.resolve_payload_value(payload, field_name)
        return identification

    @classmethod
    def resolve_payload_value(
        cls,
        payload: Mapping[str, Any],
        field_name: str,
    ) -> Any:
        """Resolve a declared request field from a payload mapping."""

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
        """Execute a compiled request plan and return normalized payload items."""

        transport = cls.transport
        if transport is None:
            raise MissingRequestTransportError(cls.__name__)
        operation: RequestQueryOperation | RequestMutationOperation
        if plan.action in {"create", "update", "delete"}:
            operation = cls.get_mutation_operation(plan.action)
        else:
            operation = cls.get_query_operation(plan.operation_name)
        result = cast(
            RequestQueryResult,
            transport.execute(
                interface_cls=cls,
                operation=operation,
                plan=plan,
                identification=dict(plan.path_params),
            ),
        )
        serializer = cls.response_serializer
        if not callable(serializer):
            return result
        normalized_items = tuple(serializer(item) for item in result.items)
        if not all(isinstance(item, Mapping) for item in normalized_items):
            raise RequestSchemaError.serializer_must_return_mappings(cls.__name__)
        return RequestQueryResult(
            items=cast(tuple[Mapping[str, Any], ...], normalized_items),
            total_count=result.total_count,
            metadata=result.metadata,
        )
