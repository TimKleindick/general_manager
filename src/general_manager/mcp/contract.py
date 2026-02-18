"""Contract and validation helpers for the GeneralManager MCP gateway."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MCPGatewayValidationError(ValueError):
    """Raised when a gateway request payload is invalid."""

    _FILTER_OBJECT_REQUIRED = "Each filter must be an object."
    _FILTER_FIELD_REQUIRED = "Filter field must be a non-empty string."
    _FILTER_IN_LIST_REQUIRED = "Filter 'in' requires a list value."
    _SORT_OBJECT_REQUIRED = "Each sort item must be an object."
    _SORT_FIELD_REQUIRED = "Sort field must be a non-empty string."
    _METRIC_OBJECT_REQUIRED = "Each metric must be an object."
    _METRIC_FIELD_REQUIRED = "Metric field must be a non-empty string."
    _METRIC_ALIAS_REQUIRED = "Metric alias must be a non-empty string."
    _REQUEST_OBJECT_REQUIRED = "Request payload must be a JSON object."
    _DOMAIN_REQUIRED = "domain must be a non-empty string."
    _OPERATION_INVALID = (
        "operation must be one of: query, aggregate, schema, discover, explain."
    )
    _SELECT_INVALID = "select must be a list of non-empty strings."
    _FILTERS_LIST_REQUIRED = "filters must be a list."
    _SORT_LIST_REQUIRED = "sort must be a list."
    _GROUP_BY_INVALID = "group_by must be a list of non-empty strings."
    _METRICS_LIST_REQUIRED = "metrics must be a list."
    _PAGE_INVALID = "page must be an integer >= 1."
    _PAGE_SIZE_INVALID = "page_size must be an integer >= 1."
    _DOMAIN_ARGUMENT_REQUIRED = "domain is required."

    @classmethod
    def filter_object_required(cls) -> "MCPGatewayValidationError":
        return cls(cls._FILTER_OBJECT_REQUIRED)

    @classmethod
    def filter_field_required(cls) -> "MCPGatewayValidationError":
        return cls(cls._FILTER_FIELD_REQUIRED)

    @classmethod
    def unsupported_filter_operator(
        cls, op_value: object
    ) -> "MCPGatewayValidationError":
        return cls(f"Unsupported filter operator: {op_value}")

    @classmethod
    def filter_in_requires_list(cls) -> "MCPGatewayValidationError":
        return cls(cls._FILTER_IN_LIST_REQUIRED)

    @classmethod
    def sort_object_required(cls) -> "MCPGatewayValidationError":
        return cls(cls._SORT_OBJECT_REQUIRED)

    @classmethod
    def sort_field_required(cls) -> "MCPGatewayValidationError":
        return cls(cls._SORT_FIELD_REQUIRED)

    @classmethod
    def unsupported_sort_direction(
        cls, direction_value: object
    ) -> "MCPGatewayValidationError":
        return cls(f"Unsupported sort direction: {direction_value}")

    @classmethod
    def metric_object_required(cls) -> "MCPGatewayValidationError":
        return cls(cls._METRIC_OBJECT_REQUIRED)

    @classmethod
    def metric_field_required(cls) -> "MCPGatewayValidationError":
        return cls(cls._METRIC_FIELD_REQUIRED)

    @classmethod
    def unsupported_aggregate_operator(
        cls, op_value: object
    ) -> "MCPGatewayValidationError":
        return cls(f"Unsupported aggregate operator: {op_value}")

    @classmethod
    def metric_alias_required(cls) -> "MCPGatewayValidationError":
        return cls(cls._METRIC_ALIAS_REQUIRED)

    @classmethod
    def request_object_required(cls) -> "MCPGatewayValidationError":
        return cls(cls._REQUEST_OBJECT_REQUIRED)

    @classmethod
    def domain_required(cls) -> "MCPGatewayValidationError":
        return cls(cls._DOMAIN_REQUIRED)

    @classmethod
    def operation_invalid(cls) -> "MCPGatewayValidationError":
        return cls(cls._OPERATION_INVALID)

    @classmethod
    def select_invalid(cls) -> "MCPGatewayValidationError":
        return cls(cls._SELECT_INVALID)

    @classmethod
    def filters_list_required(cls) -> "MCPGatewayValidationError":
        return cls(cls._FILTERS_LIST_REQUIRED)

    @classmethod
    def sort_list_required(cls) -> "MCPGatewayValidationError":
        return cls(cls._SORT_LIST_REQUIRED)

    @classmethod
    def group_by_invalid(cls) -> "MCPGatewayValidationError":
        return cls(cls._GROUP_BY_INVALID)

    @classmethod
    def metrics_list_required(cls) -> "MCPGatewayValidationError":
        return cls(cls._METRICS_LIST_REQUIRED)

    @classmethod
    def page_invalid(cls) -> "MCPGatewayValidationError":
        return cls(cls._PAGE_INVALID)

    @classmethod
    def page_size_invalid(cls) -> "MCPGatewayValidationError":
        return cls(cls._PAGE_SIZE_INVALID)

    @classmethod
    def domain_argument_required(cls) -> "MCPGatewayValidationError":
        return cls(cls._DOMAIN_ARGUMENT_REQUIRED)


class GatewayOperation(str, Enum):
    """Supported read-only gateway operations."""

    QUERY = "query"
    AGGREGATE = "aggregate"
    SCHEMA = "schema"
    DISCOVER = "discover"
    EXPLAIN = "explain"


class FilterOperator(str, Enum):
    """Supported filter operators for structured requests."""

    EQ = "eq"
    NE = "ne"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    IN = "in"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IS_NULL = "is_null"


class SortDirection(str, Enum):
    """Sort direction for list queries."""

    ASC = "asc"
    DESC = "desc"


class AggregateOperator(str, Enum):
    """Supported aggregate operations."""

    COUNT = "count"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"


DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def _normalize_filter_operator(value: object) -> object:
    if value is None:
        return value
    normalized = str(value).strip().lower()
    alias_map = {
        ">": FilterOperator.GT.value,
        ">=": FilterOperator.GTE.value,
        "<": FilterOperator.LT.value,
        "<=": FilterOperator.LTE.value,
        "equals": FilterOperator.EQ.value,
        "not_equals": FilterOperator.NE.value,
        "count_gt": FilterOperator.GT.value,
        "count_gte": FilterOperator.GTE.value,
        "count_lt": FilterOperator.LT.value,
        "count_lte": FilterOperator.LTE.value,
    }
    return alias_map.get(normalized, normalized)


def _normalize_aggregate_operator(value: object) -> object:
    if value is None:
        return value
    normalized = str(value).strip().lower()
    alias_map = {
        "avg": AggregateOperator.AVG.value,
        "average": AggregateOperator.AVG.value,
    }
    return alias_map.get(normalized, normalized)


@dataclass(slots=True)
class FilterSpec:
    """Structured filter clause."""

    field: str
    op: FilterOperator
    value: Any = None

    @classmethod
    def from_payload(cls, payload: Any) -> "FilterSpec":
        if not isinstance(payload, dict):
            raise MCPGatewayValidationError.filter_object_required()
        field_name = payload.get("field")
        if not isinstance(field_name, str) or not field_name:
            raise MCPGatewayValidationError.filter_field_required()
        op_value = _normalize_filter_operator(
            payload.get("op", payload.get("operator", FilterOperator.EQ.value))
        )
        try:
            operator = FilterOperator(str(op_value))
        except ValueError as exc:
            raise MCPGatewayValidationError.unsupported_filter_operator(
                op_value
            ) from exc

        value: Any
        if operator is FilterOperator.IS_NULL:
            value = bool(payload.get("value", True))
        else:
            value = payload.get("value")
            if operator is FilterOperator.IN and not isinstance(value, list):
                raise MCPGatewayValidationError.filter_in_requires_list()
        return cls(field=field_name, op=operator, value=value)


@dataclass(slots=True)
class SortSpec:
    """Structured sort clause."""

    field: str
    direction: SortDirection = SortDirection.ASC

    @classmethod
    def from_payload(cls, payload: Any) -> "SortSpec":
        if not isinstance(payload, dict):
            raise MCPGatewayValidationError.sort_object_required()
        field_name = payload.get("field")
        if not isinstance(field_name, str) or not field_name:
            raise MCPGatewayValidationError.sort_field_required()
        direction_value = payload.get(
            "direction",
            payload.get("order", SortDirection.ASC.value),
        )
        try:
            direction = SortDirection(str(direction_value).lower())
        except ValueError as exc:
            raise MCPGatewayValidationError.unsupported_sort_direction(
                direction_value
            ) from exc
        return cls(field=field_name, direction=direction)


@dataclass(slots=True)
class MetricSpec:
    """Aggregate metric request."""

    field: str
    op: AggregateOperator
    alias: str | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> "MetricSpec":
        if not isinstance(payload, dict):
            raise MCPGatewayValidationError.metric_object_required()
        field_name = payload.get("field")
        if not isinstance(field_name, str) or not field_name:
            raise MCPGatewayValidationError.metric_field_required()
        op_value = _normalize_aggregate_operator(
            payload.get("op", payload.get("aggregate_function"))
        )
        try:
            op = AggregateOperator(str(op_value))
        except ValueError as exc:
            raise MCPGatewayValidationError.unsupported_aggregate_operator(
                op_value
            ) from exc
        alias = payload.get("alias")
        if alias is not None and (not isinstance(alias, str) or not alias):
            raise MCPGatewayValidationError.metric_alias_required()
        return cls(field=field_name, op=op, alias=alias)


@dataclass(slots=True)
class QueryRequest:
    """Normalized gateway request for data operations."""

    domain: str
    operation: GatewayOperation
    select: list[str] = field(default_factory=list)
    filters: list[FilterSpec] = field(default_factory=list)
    sort: list[SortSpec] = field(default_factory=list)
    page: int = DEFAULT_PAGE
    page_size: int = DEFAULT_PAGE_SIZE
    group_by: list[str] = field(default_factory=list)
    metrics: list[MetricSpec] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: Any) -> "QueryRequest":
        if not isinstance(payload, dict):
            raise MCPGatewayValidationError.request_object_required()

        domain = payload.get("domain")
        if not isinstance(domain, str) or not domain:
            raise MCPGatewayValidationError.domain_required()

        try:
            operation = GatewayOperation(str(payload.get("operation")))
        except ValueError as exc:
            raise MCPGatewayValidationError.operation_invalid() from exc

        select = payload.get("select", [])
        if not isinstance(select, list) or any(
            not isinstance(item, str) or not item for item in select
        ):
            raise MCPGatewayValidationError.select_invalid()

        filters_payload = payload.get("filters", [])
        if not isinstance(filters_payload, list):
            raise MCPGatewayValidationError.filters_list_required()
        filters = [FilterSpec.from_payload(item) for item in filters_payload]

        sort_payload = payload.get("sort", [])
        if not isinstance(sort_payload, list):
            raise MCPGatewayValidationError.sort_list_required()
        sort = [SortSpec.from_payload(item) for item in sort_payload]

        group_by = payload.get("group_by", [])
        if not isinstance(group_by, list) or any(
            not isinstance(item, str) or not item for item in group_by
        ):
            raise MCPGatewayValidationError.group_by_invalid()

        metrics_payload = payload.get("metrics", [])
        if not isinstance(metrics_payload, list):
            raise MCPGatewayValidationError.metrics_list_required()
        metrics = [MetricSpec.from_payload(item) for item in metrics_payload]

        page = payload.get("page", DEFAULT_PAGE)
        if not isinstance(page, int) or page < 1:
            raise MCPGatewayValidationError.page_invalid()

        page_size = payload.get("page_size", DEFAULT_PAGE_SIZE)
        if not isinstance(page_size, int) or page_size < 1:
            raise MCPGatewayValidationError.page_size_invalid()
        page_size = min(page_size, MAX_PAGE_SIZE)

        return cls(
            domain=domain,
            operation=operation,
            select=select,
            filters=filters,
            sort=sort,
            page=page,
            page_size=page_size,
            group_by=group_by,
            metrics=metrics,
        )


@dataclass(slots=True)
class QueryContext:
    """Execution context shared across HTTP and MCP adapters."""

    user: Any
    request_id: str
    tenant: str | None = None


@dataclass(slots=True)
class QueryResponse:
    """Normalized gateway response payload."""

    data: dict[str, Any]
    provenance: dict[str, Any]
    errors: list[dict[str, Any]]
    follow_up_suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "data": self.data,
            "provenance": self.provenance,
            "errors": self.errors,
            "follow_up_suggestions": self.follow_up_suggestions,
        }
