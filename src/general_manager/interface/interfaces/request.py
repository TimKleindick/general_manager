"""Interface implementation for request-backed GeneralManager classes."""

from __future__ import annotations

from typing import Any, ClassVar, Mapping

from general_manager.bucket.base_bucket import Bucket
from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.bundles.request import REQUEST_CORE_CAPABILITIES
from general_manager.interface.capabilities.base import CapabilityName
from general_manager.interface.capabilities.configuration import CapabilityConfigEntry
from general_manager.interface.requests import (
    MissingRequestPayloadFieldError,
    RequestField,
    RequestFilter,
    RequestQueryOperation,
    RequestQueryPlan,
    RequestQueryResult,
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
    default_query_operation: ClassVar[str] = "list"
    _request_payload_cache: Mapping[str, Any] | None = None

    configured_capabilities: ClassVar[tuple[CapabilityConfigEntry, ...]] = (
        REQUEST_CORE_CAPABILITIES,
    )
    lifecycle_capability_name: ClassVar[CapabilityName | None] = "request_lifecycle"

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
            filters=cls.filters,
            metadata=operation.metadata,
        )

    @classmethod
    def query_operation(cls, operation_name: str, **kwargs: Any) -> Bucket[Any]:
        """Build a bucket for a named collection operation declared on the interface."""

        handler = cls.require_capability("query")
        if hasattr(handler, "for_operation"):
            return handler.for_operation(cls, operation_name, **kwargs)
        raise NotImplementedError

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

        raise NotImplementedError(
            f"{cls.__name__} must implement execute_request_plan()."
        )
