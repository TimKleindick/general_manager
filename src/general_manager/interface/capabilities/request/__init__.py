"""Capabilities for request-backed interfaces."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, TYPE_CHECKING, cast

from general_manager.bucket.request_bucket import RequestBucket
from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.capabilities.core.utils import with_observability
from general_manager.interface.requests import (
    RequestExcludeNotSupportedError,
    RequestFieldsRequiredError,
    RequestFilter,
    RequestFilterBinding,
    RequestLocalFallbackRequiredError,
    RequestLocalPredicate,
    RequestPlanConflictError,
    RequestPlanFragment,
    RequestQueryOperation,
    RequestQueryPlan,
    RequestQueryResult,
    RequestSingleResponseRequiredError,
    RequestLocation,
    UnknownRequestFilterError,
    UnknownRequestOperationError,
    UnsupportedRequestLocationError,
)
from general_manager.manager.input import Input

from ..base import CapabilityName
from ..builtin import BaseCapability, ValidationCapability

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.interfaces.request import RequestInterface


class RequestValidationCapability(ValidationCapability):
    """Validate request-interface declarations during capability binding."""

    def setup(self, interface_cls: type[InterfaceBase]) -> None:
        super().setup(interface_cls)
        request_interface_cls = cast(type["RequestInterface"], interface_cls)
        if not request_interface_cls.fields:
            raise RequestFieldsRequiredError(request_interface_cls.__name__)
        for filter_key in request_interface_cls.filters:
            # Query-operation-specific filters are validated when the operation is resolved.
            _ = filter_key


class RequestReadCapability(BaseCapability):
    """Expose declarative request fields as manager attributes."""

    name: ClassVar[CapabilityName] = "read"

    def get_data(self, interface_instance: "RequestInterface") -> Mapping[str, Any]:
        cached_payload = getattr(interface_instance, "_request_payload_cache", None)
        if cached_payload is not None:
            return cast(Mapping[str, Any], cached_payload)

        interface_cls = type(interface_instance)
        try:
            operation = interface_cls.get_query_operation("detail")
        except UnknownRequestOperationError as error:
            raise NotImplementedError(
                f"{interface_cls.__name__} must declare a 'detail' request operation "
                "to resolve request-backed attributes lazily."
            ) from error

        payload_snapshot = {
            "operation": operation.name,
            "identification": dict(interface_instance.identification),
        }

        def _perform() -> Mapping[str, Any]:
            request_plan = RequestQueryPlan(
                operation_name=operation.name,
                action="detail",
                method=operation.method,
                path=operation.path,
                path_params=dict(interface_instance.identification),
                metadata=operation.metadata,
            )
            result = interface_cls.execute_request_plan(request_plan)
            if len(result.items) != 1:
                raise RequestSingleResponseRequiredError(
                    interface_cls.__name__,
                    len(result.items),
                )
            payload = result.items[0]
            interface_instance._request_payload_cache = payload
            return payload

        return with_observability(
            target=interface_cls,
            operation="request.read.detail",
            payload=payload_snapshot,
            func=_perform,
        )

    def get_attribute_types(
        self,
        interface_cls: type["RequestInterface"],
    ) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "type": field.field_type,
                "default": field.default,
                "is_editable": field.is_editable,
                "is_required": field.is_required,
                "is_derived": field.is_derived,
            }
            for name, field in interface_cls.fields.items()
        }

    def get_attributes(
        self,
        interface_cls: type["RequestInterface"],
    ) -> dict[str, Any]:
        def _resolve_field(
            interface_instance: "RequestInterface", field_name: str
        ) -> Any:
            payload = cast(Mapping[str, Any], interface_instance.get_data())
            return interface_cls.resolve_payload_value(payload, field_name)

        return {
            name: lambda interface_instance, name=name: _resolve_field(
                interface_instance, name
            )
            for name in interface_cls.fields.keys()
        }

    def get_field_type(
        self,
        interface_cls: type["RequestInterface"],
        field_name: str,
    ) -> type[Any]:
        field = interface_cls.fields.get(field_name)
        if field is None:
            raise KeyError(field_name)
        return field.field_type


class RequestLifecycleCapability(BaseCapability):
    """Attach request interfaces to their parent manager classes."""

    name: ClassVar[CapabilityName] = "request_lifecycle"

    def pre_create(
        self,
        *,
        name: str,
        attrs: dict[str, Any],
        interface: type["RequestInterface"],
    ) -> tuple[dict[str, Any], type["RequestInterface"], None]:
        payload_snapshot = {
            "interface": interface.__name__,
            "name": name,
        }

        def _perform() -> tuple[dict[str, Any], type["RequestInterface"], None]:
            input_fields: dict[str, Input[Any]] = {}
            for key, value in vars(interface).items():
                if key.startswith("__"):
                    continue
                if isinstance(value, Input):
                    input_fields[key] = value
            attrs["_interface_type"] = interface._interface_type
            interface_cls = type(
                interface.__name__, (interface,), {"input_fields": input_fields}
            )
            attrs["Interface"] = interface_cls
            return attrs, interface_cls, None

        return with_observability(
            target=interface,
            operation="request.pre_create",
            payload=payload_snapshot,
            func=_perform,
        )

    def post_create(
        self,
        *,
        new_class: type,
        interface_class: type["RequestInterface"],
        model: None = None,
    ) -> None:
        payload_snapshot = {"interface": interface_class.__name__}

        def _perform() -> None:
            interface_class._parent_class = new_class  # type: ignore[attr-defined]

        with_observability(
            target=interface_class,
            operation="request.post_create",
            payload=payload_snapshot,
            func=_perform,
        )


class RequestQueryCapability(BaseCapability):
    """Compile declarative request filters into request plans and buckets."""

    name: ClassVar[CapabilityName] = "query"

    def filter(
        self,
        interface_cls: type["RequestInterface"],
        **kwargs: Any,
    ) -> RequestBucket:
        return self.build_bucket(
            interface_cls, filters=self._normalize_lookup_map(kwargs)
        )

    def exclude(
        self,
        interface_cls: type["RequestInterface"],
        **kwargs: Any,
    ) -> RequestBucket:
        return self.build_bucket(
            interface_cls, excludes=self._normalize_lookup_map(kwargs)
        )

    def all(self, interface_cls: type["RequestInterface"]) -> RequestBucket:
        return self.build_bucket(interface_cls)

    def validate_lookups(
        self,
        interface_cls: type["RequestInterface"],
        *,
        operation_name: str | None = None,
        filters: Mapping[str, tuple[Any, ...]] | None = None,
        excludes: Mapping[str, tuple[Any, ...]] | None = None,
    ) -> None:
        self._build_request_plan(
            interface_cls,
            operation_name=operation_name,
            filters=self._copy_lookup_map(filters),
            excludes=self._copy_lookup_map(excludes),
        )

    def for_operation(
        self,
        interface_cls: type["RequestInterface"],
        operation_name: str,
        **kwargs: Any,
    ) -> RequestBucket:
        return self.build_bucket(
            interface_cls,
            operation_name=operation_name,
            filters=self._normalize_lookup_map(kwargs),
        )

    def build_bucket(
        self,
        interface_cls: type["RequestInterface"],
        *,
        operation_name: str | None = None,
        filters: Mapping[str, tuple[Any, ...]] | None = None,
        excludes: Mapping[str, tuple[Any, ...]] | None = None,
    ) -> RequestBucket:
        filter_map = self._copy_lookup_map(filters)
        exclude_map = self._copy_lookup_map(excludes)
        request_plan = self._build_request_plan(
            interface_cls,
            operation_name=operation_name,
            filters=filter_map,
            excludes=exclude_map,
        )
        return RequestBucket(
            interface_cls._parent_class,
            interface_cls,
            operation_name=request_plan.operation_name,
            request_plan=request_plan,
            filters=filter_map,
            excludes=exclude_map,
        )

    def execute_plan(
        self,
        interface_cls: type["RequestInterface"],
        request_plan: RequestQueryPlan,
    ) -> RequestQueryResult:
        payload_snapshot = {
            "operation": request_plan.operation_name,
            "method": request_plan.method,
            "path": request_plan.path,
            "query_params": dict(request_plan.query_params),
            "path_params": dict(request_plan.path_params),
            "header_keys": sorted(request_plan.headers.keys()),
            "body_keys": sorted(request_plan.body.keys()) if request_plan.body else [],
            "local_predicates": [
                predicate.lookup_key for predicate in request_plan.local_predicates
            ],
        }

        def _perform() -> RequestQueryResult:
            result = interface_cls.execute_request_plan(request_plan)
            if isinstance(result, RequestQueryResult):
                return result
            return RequestQueryResult(items=tuple(result.items))  # type: ignore[arg-type]

        return with_observability(
            target=interface_cls,
            operation="request.query.execute",
            payload=payload_snapshot,
            func=_perform,
        )

    @staticmethod
    def _normalize_lookup_map(
        kwargs: Mapping[str, Any],
    ) -> dict[str, tuple[Any, ...]]:
        return {key: (value,) for key, value in kwargs.items()}

    @staticmethod
    def _copy_lookup_map(
        values: Mapping[str, tuple[Any, ...]] | None,
    ) -> dict[str, tuple[Any, ...]]:
        if not values:
            return {}
        return {key: tuple(items) for key, items in values.items()}

    def _build_request_plan(
        self,
        interface_cls: type["RequestInterface"],
        *,
        operation_name: str | None,
        filters: Mapping[str, tuple[Any, ...]],
        excludes: Mapping[str, tuple[Any, ...]],
    ) -> RequestQueryPlan:
        operation = interface_cls.get_query_operation(operation_name)
        query_params: dict[str, Any] = {}
        headers: dict[str, Any] = {}
        path_params: dict[str, Any] = {}
        body: dict[str, Any] = {}
        local_predicates: list[RequestLocalPredicate] = []

        for action, lookup_map in (("filter", filters), ("exclude", excludes)):
            for lookup_key, values in lookup_map.items():
                spec = self._get_filter_spec(interface_cls, operation, lookup_key)
                for value in values:
                    fragment = self._compile_fragment(
                        spec=spec,
                        lookup_key=lookup_key,
                        value=value,
                        action=cast(Any, action),
                        operation_name=operation.name,
                    )
                    self._merge_fragment(query_params, fragment.query_params, "query")
                    self._merge_fragment(headers, fragment.headers, "headers")
                    self._merge_fragment(path_params, fragment.path_params, "path")
                    self._merge_fragment(body, fragment.body, "body")
                    local_predicates.extend(fragment.local_predicates)

        return RequestQueryPlan(
            operation_name=operation.name,
            action="all" if not filters and not excludes else "filter",
            method=operation.method,
            path=operation.path,
            query_params=query_params,
            headers=headers,
            path_params=path_params,
            body=body,
            local_predicates=tuple(local_predicates),
            filters=filters,
            excludes=excludes,
            metadata=operation.metadata,
        )

    @staticmethod
    def _get_filter_spec(
        interface_cls: type["RequestInterface"],
        operation: RequestQueryOperation,
        lookup_key: str,
    ) -> RequestFilter:
        if operation.filters:
            spec = operation.filters.get(lookup_key)
            if spec is None:
                raise UnknownRequestFilterError(lookup_key, operation.name)
        else:
            spec = interface_cls.filters.get(lookup_key)
        if spec is None:
            raise UnknownRequestFilterError(lookup_key, operation.name)
        if not spec.applies_to_operation(operation.name):
            raise UnknownRequestFilterError(lookup_key, operation.name)
        return spec

    def _compile_fragment(
        self,
        *,
        spec: RequestFilter,
        lookup_key: str,
        value: Any,
        action: str,
        operation_name: str,
    ) -> RequestPlanFragment:
        spec.validate_value(lookup_key, value)
        binding = RequestFilterBinding(
            lookup_key=lookup_key,
            value=value,
            action=cast(Any, action),
            operation_name=operation_name,
            spec=spec,
        )
        if spec.compiler is not None:
            return spec.compiler(binding)

        if not spec.remote:
            if spec.local_fallback:
                return RequestPlanFragment(
                    local_predicates=(
                        RequestLocalPredicate(lookup_key, value, cast(Any, action)),
                    )
                )
            if action == "filter":
                raise RequestLocalFallbackRequiredError(lookup_key)
            raise RequestExcludeNotSupportedError(lookup_key, operation_name)

        if action == "exclude" and not spec.allow_exclude:
            if spec.local_fallback:
                return RequestPlanFragment(
                    local_predicates=(
                        RequestLocalPredicate(lookup_key, value, cast(Any, action)),
                    )
                )
            raise RequestExcludeNotSupportedError(lookup_key, operation_name)

        remote_value = spec.serializer(value) if spec.serializer is not None else value
        param = self._resolve_param_name(spec, lookup_key, action)
        return self._build_remote_fragment(
            location=spec.location,
            key=param,
            value=remote_value,
        )

    @staticmethod
    def _resolve_param_name(
        spec: RequestFilter,
        lookup_key: str,
        action: str,
    ) -> str:
        if action == "exclude" and spec.exclude_param:
            return spec.exclude_param
        return spec.param or lookup_key

    @staticmethod
    def _build_remote_fragment(
        *,
        location: RequestLocation,
        key: str,
        value: Any,
    ) -> RequestPlanFragment:
        if location == "query":
            return RequestPlanFragment(query_params={key: value})
        if location == "headers":
            return RequestPlanFragment(headers={key: value})
        if location == "path":
            return RequestPlanFragment(path_params={key: value})
        if location == "body":
            return RequestPlanFragment(body={key: value})
        raise UnsupportedRequestLocationError(location)

    @staticmethod
    def _merge_fragment(
        target: dict[str, Any],
        updates: Mapping[str, Any],
        location: str,
    ) -> None:
        for key, value in updates.items():
            if key in target and target[key] != value:
                raise RequestPlanConflictError(
                    location=cast(Any, location),
                    key=key,
                )
            target[key] = value


__all__ = [
    "RequestLifecycleCapability",
    "RequestQueryCapability",
    "RequestReadCapability",
    "RequestValidationCapability",
]
