"""Capabilities for request-backed interfaces."""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, ClassVar, TYPE_CHECKING, cast
from django.core.exceptions import ValidationError

from general_manager.bucket.request_bucket import RequestBucket
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_index import serialize_dependency_identifier
from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.capabilities.core.utils import with_observability
from general_manager.interface.requests import (
    InvalidRequestFilterConfigurationError,
    RequestConfigurationError,
    RequestExcludeNotSupportedError,
    RequestFieldsRequiredError,
    RequestField,
    RequestFilter,
    RequestFilterBinding,
    RequestLocalFallbackRequiredError,
    RequestLocalPredicate,
    RequestPlanConflictError,
    RequestPlanFragment,
    RequestQueryOperation,
    RequestQueryPlan,
    RequestQueryResult,
    RequestRetryPolicy,
    RequestSingleResponseRequiredError,
    RequestLocation,
    MissingRequestDetailOperationError,
    UnknownRequestFilterError,
    UnknownRequestFilterOperationReferenceError,
    UnknownRequestOperationError,
    UnsupportedRequestLocationError,
    validate_filter_key,
)
from general_manager.manager.input import Input
from general_manager.rule import Rule

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
        if "detail" not in request_interface_cls.query_operations:
            raise MissingRequestDetailOperationError(request_interface_cls.__name__)
        if request_interface_cls.rules and not any(
            (
                request_interface_cls.create_operation,
                request_interface_cls.update_operation,
                request_interface_cls.delete_operation,
            )
        ):
            raise RequestConfigurationError.rules_without_mutations(
                request_interface_cls.__name__
            )
        for rule in request_interface_cls.rules:
            if not isinstance(rule, Rule):
                raise RequestConfigurationError.invalid_rule_type(
                    request_interface_cls.__name__
                )
        for serializer_name in (
            "create_serializer",
            "update_serializer",
            "response_serializer",
        ):
            serializer = getattr(request_interface_cls, serializer_name, None)
            if serializer is not None and not callable(serializer):
                raise RequestConfigurationError.serializer_not_callable(
                    request_interface_cls.__name__,
                    serializer_name,
                )
        auth_provider = getattr(request_interface_cls, "auth_provider", None)
        if auth_provider is not None and not callable(
            getattr(auth_provider, "apply", None)
        ):
            raise RequestConfigurationError.invalid_auth_provider(
                request_interface_cls.__name__
            )
        retry_policy = getattr(request_interface_cls, "retry_policy", None)
        if retry_policy is not None:
            if not isinstance(retry_policy, RequestRetryPolicy):
                raise RequestConfigurationError.invalid_retry_policy(
                    request_interface_cls.__name__,
                    "must use RequestRetryPolicy",
                )
            if retry_policy.max_attempts < 1:
                raise RequestConfigurationError.invalid_retry_policy(
                    request_interface_cls.__name__,
                    "max_attempts must be at least 1",
                )
            if retry_policy.base_backoff_seconds < 0:
                raise RequestConfigurationError.invalid_retry_policy(
                    request_interface_cls.__name__,
                    "base_backoff_seconds cannot be negative",
                )
            if retry_policy.backoff_multiplier < 1:
                raise RequestConfigurationError.invalid_retry_policy(
                    request_interface_cls.__name__,
                    "backoff_multiplier must be at least 1",
                )
            if (
                retry_policy.max_backoff_seconds is not None
                and retry_policy.max_backoff_seconds < 0
            ):
                raise RequestConfigurationError.invalid_retry_policy(
                    request_interface_cls.__name__,
                    "max_backoff_seconds cannot be negative",
                )
            if not 0 <= retry_policy.jitter_ratio <= 1:
                raise RequestConfigurationError.invalid_retry_policy(
                    request_interface_cls.__name__,
                    "jitter_ratio must be between 0 and 1",
                )

        declared_operations = set(request_interface_cls.query_operations)
        for filter_key, spec in request_interface_cls.filters.items():
            validate_filter_key(filter_key)
            self._validate_filter_spec(
                filter_key,
                spec,
                declared_operations=declared_operations,
            )

        for operation_name, operation in request_interface_cls.query_operations.items():
            if not operation.name or not operation.path:
                raise InvalidRequestFilterConfigurationError(operation_name)
            duplicate_keys = set(request_interface_cls.filters).intersection(
                operation.filters
            )
            if duplicate_keys:
                raise InvalidRequestFilterConfigurationError(sorted(duplicate_keys)[0])
            for filter_key, spec in operation.filters.items():
                validate_filter_key(filter_key)
                self._validate_filter_spec(
                    filter_key,
                    spec,
                    declared_operations=declared_operations,
                )

    @staticmethod
    def _validate_filter_spec(
        filter_key: str,
        spec: RequestFilter,
        *,
        declared_operations: set[str],
    ) -> None:
        if not spec.remote and spec.compiler is None and not spec.local_fallback:
            raise InvalidRequestFilterConfigurationError(filter_key)
        if spec.exclude_param is not None and not spec.allow_exclude:
            raise InvalidRequestFilterConfigurationError(filter_key)
        unknown_operations = set(spec.operation_names).difference(declared_operations)
        if unknown_operations:
            raise UnknownRequestFilterOperationReferenceError(
                filter_key,
                unknown_operations,
            )


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
            "service": interface_cls._parent_class.__name__,
            "operation": operation.name,
            "method": operation.method,
            "path": operation.path,
            "identification_keys": sorted(interface_instance.identification.keys()),
        }

        def _perform() -> RequestQueryResult:
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
            return result

        result = with_observability(
            target=interface_cls,
            operation="request.read.detail",
            payload=payload_snapshot,
            func=_perform,
        )
        payload = result.items[0]
        interface_instance._request_payload_cache = payload
        return payload

    def get_attribute_types(
        self,
        interface_cls: type["RequestInterface"],
    ) -> dict[str, dict[str, Any]]:
        attribute_types = {
            name: {
                "type": input_field.type,
                "default": None,
                "is_editable": False,
                "is_required": input_field.required,
                "is_derived": False,
            }
            for name, input_field in interface_cls.input_fields.items()
        }
        attribute_types.update(
            {
                name: {
                    "type": field.field_type,
                    "default": field.default,
                    "is_editable": field.is_editable,
                    "is_required": field.is_required,
                    "is_derived": field.is_derived,
                }
                for name, field in interface_cls.fields.items()
            }
        )
        return attribute_types

    def get_attributes(
        self,
        interface_cls: type["RequestInterface"],
    ) -> dict[str, Any]:
        def _resolve_field(
            interface_instance: "RequestInterface", field_name: str
        ) -> Any:
            payload = cast(Mapping[str, Any], interface_instance.get_data())
            return interface_cls.resolve_payload_value(payload, field_name)

        attributes = {
            name: lambda interface_instance,
            name=name: interface_instance.identification[name]
            for name in interface_cls.input_fields.keys()
        }
        attributes.update(
            {
                name: lambda interface_instance, name=name: _resolve_field(
                    interface_instance, name
                )
                for name in interface_cls.fields.keys()
            }
        )
        return attributes

    def get_field_type(
        self,
        interface_cls: type["RequestInterface"],
        field_name: str,
    ) -> type[Any]:
        field = interface_cls.fields.get(field_name)
        if field is not None:
            return field.field_type
        input_field = interface_cls.input_fields.get(field_name)
        if input_field is not None:
            return input_field.type
        raise KeyError(field_name)


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
            request_fields: dict[str, RequestField] = {}
            for key, value in vars(interface).items():
                if key.startswith("__"):
                    continue
                if isinstance(value, Input):
                    input_fields[key] = value
                elif isinstance(value, RequestField):
                    request_fields[key] = value
            attrs["_interface_type"] = interface._interface_type
            interface_cls = cast(
                type["RequestInterface"],
                type(interface.__name__, (interface,), {}),
            )
            interface_cls.__module__ = interface.__module__
            interface_cls.__qualname__ = interface.__qualname__
            interface_cls.input_fields = input_fields
            interface_cls.fields = request_fields
            interface_cls.filters = dict(getattr(interface, "filters", {}))
            interface_cls.query_operations = dict(
                getattr(interface, "query_operations", {})
            )
            interface_cls.default_query_operation = getattr(
                interface,
                "default_query_operation",
                interface.default_query_operation,
            )
            interface_cls.transport = getattr(interface, "transport", None)
            interface_cls.transport_config = getattr(
                interface, "transport_config", None
            )
            interface_cls.auth_provider = getattr(interface, "auth_provider", None)
            interface_cls.retry_policy = getattr(interface, "retry_policy", None)
            interface_cls.create_operation = getattr(
                interface, "create_operation", None
            )
            interface_cls.update_operation = getattr(
                interface, "update_operation", None
            )
            interface_cls.delete_operation = getattr(
                interface, "delete_operation", None
            )
            interface_cls.create_serializer = getattr(
                interface, "create_serializer", None
            )
            interface_cls.update_serializer = getattr(
                interface, "update_serializer", None
            )
            interface_cls.response_serializer = getattr(
                interface, "response_serializer", None
            )
            interface_cls.rules = list(getattr(interface, "rules", []))
            interface_cls._sync_configured_capabilities()
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
        self._track_request_dependency(interface_cls, request_plan)
        return RequestBucket(
            interface_cls._parent_class,
            interface_cls,
            operation_name=request_plan.operation_name,
            request_plan=request_plan,
            filters=filter_map,
            excludes=exclude_map,
        )

    @staticmethod
    def _track_request_dependency(
        interface_cls: type["RequestInterface"],
        request_plan: RequestQueryPlan,
    ) -> None:
        DependencyTracker.track(
            interface_cls._parent_class.__name__,
            "request_query",
            serialize_dependency_identifier(
                {
                    "operation": request_plan.operation_name,
                    "filters": dict(request_plan.filters),
                    "excludes": dict(request_plan.excludes),
                }
            ),
        )

    def execute_plan(
        self,
        interface_cls: type["RequestInterface"],
        request_plan: RequestQueryPlan,
    ) -> RequestQueryResult:
        payload_snapshot = {
            "service": interface_cls._parent_class.__name__,
            "operation": request_plan.operation_name,
            "method": request_plan.method,
            "path": request_plan.path,
            "query_param_keys": sorted(request_plan.query_params.keys()),
            "path_param_keys": sorted(request_plan.path_params.keys()),
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


class RequestCreateCapability(BaseCapability):
    """Execute declared create operations for request-backed interfaces."""

    name: ClassVar[CapabilityName] = "create"

    def create(
        self,
        interface_cls: type["RequestInterface"],
        *,
        creator_id: int | None = None,
        history_comment: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del creator_id, history_comment
        operation = interface_cls.get_mutation_operation("create")
        serializer = getattr(interface_cls, "create_serializer", None)
        _apply_request_rules(interface_cls, kwargs)
        body = serializer(kwargs) if callable(serializer) else kwargs
        result = interface_cls.execute_request_plan(
            RequestQueryPlan(
                operation_name=operation.name,
                action="create",
                method=operation.method,
                path=operation.path,
                body=body,
                metadata=operation.metadata,
            )
        )
        if len(result.items) != 1:
            raise RequestSingleResponseRequiredError(
                interface_cls.__name__, len(result.items)
            )
        return interface_cls.extract_identification(result.items[0])


class RequestUpdateCapability(BaseCapability):
    """Execute declared update operations for request-backed interfaces."""

    name: ClassVar[CapabilityName] = "update"

    def update(
        self,
        interface_instance: "RequestInterface",
        *,
        creator_id: int | None = None,
        history_comment: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del creator_id, history_comment
        interface_cls = type(interface_instance)
        operation = interface_cls.get_mutation_operation("update")
        serializer = getattr(interface_cls, "update_serializer", None)
        existing_values = {
            field_name: getattr(interface_instance, field_name)
            for field_name in interface_cls.fields
        }
        _apply_request_rules(interface_cls, {**existing_values, **kwargs})
        body = serializer(kwargs) if callable(serializer) else kwargs
        result = interface_cls.execute_request_plan(
            RequestQueryPlan(
                operation_name=operation.name,
                action="update",
                method=operation.method,
                path=operation.path,
                path_params=dict(interface_instance.identification),
                body=body,
                metadata=operation.metadata,
            )
        )
        if len(result.items) != 1:
            raise RequestSingleResponseRequiredError(
                interface_cls.__name__, len(result.items)
            )
        interface_instance._request_payload_cache = result.items[0]
        return interface_cls.extract_identification(result.items[0])


class RequestDeleteCapability(BaseCapability):
    """Execute declared delete operations for request-backed interfaces."""

    name: ClassVar[CapabilityName] = "delete"

    def delete(
        self,
        interface_instance: "RequestInterface",
        *,
        creator_id: int | None = None,
        history_comment: str | None = None,
    ) -> None:
        del creator_id, history_comment
        interface_cls = type(interface_instance)
        operation = interface_cls.get_mutation_operation("delete")
        interface_cls.execute_request_plan(
            RequestQueryPlan(
                operation_name=operation.name,
                action="delete",
                method=operation.method,
                path=operation.path,
                path_params=dict(interface_instance.identification),
                metadata=operation.metadata,
            )
        )


def _apply_request_rules(
    interface_cls: type["RequestInterface"],
    candidate_values: Mapping[str, Any],
) -> None:
    rules = cast(list[Rule[Any]], getattr(interface_cls, "rules", []))
    if not rules:
        return
    candidate = SimpleNamespace(**candidate_values)
    errors: dict[str, Any] = {}
    for rule in rules:
        if rule.evaluate(candidate) is False:
            error_message = rule.get_error_message()
            if error_message:
                errors.update(error_message)
    if errors:
        raise ValidationError(errors)


__all__ = [
    "RequestCreateCapability",
    "RequestDeleteCapability",
    "RequestLifecycleCapability",
    "RequestQueryCapability",
    "RequestReadCapability",
    "RequestUpdateCapability",
    "RequestValidationCapability",
]
