from general_manager.mcp.contract import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    GatewayOperation,
    MCPGatewayValidationError,
    QueryRequest,
    SortDirection,
)


def test_query_request_parses_and_clamps_page_size() -> None:
    payload = {
        "domain": "Project",
        "operation": "query",
        "select": ["id", "name"],
        "page": 1,
        "page_size": MAX_PAGE_SIZE + 100,
    }

    parsed = QueryRequest.from_payload(payload)

    assert parsed.domain == "Project"
    assert parsed.operation is GatewayOperation.QUERY
    assert parsed.page_size == MAX_PAGE_SIZE


def test_query_request_uses_defaults() -> None:
    payload = {
        "domain": "Project",
        "operation": "aggregate",
    }

    parsed = QueryRequest.from_payload(payload)

    assert parsed.page == 1
    assert parsed.page_size == DEFAULT_PAGE_SIZE
    assert parsed.select == []


def test_query_request_rejects_invalid_payload() -> None:
    payload = {
        "domain": "Project",
        "operation": "query",
        "filters": {"field": "status", "op": "eq", "value": "active"},
    }

    try:
        QueryRequest.from_payload(payload)
    except MCPGatewayValidationError as exc:
        assert "filters must be a list" in str(exc)
    else:
        raise AssertionError


def test_query_request_accepts_sort_order_alias() -> None:
    payload = {
        "domain": "Project",
        "operation": "query",
        "sort": [{"field": "total_volume", "order": "desc"}],
    }

    parsed = QueryRequest.from_payload(payload)

    assert parsed.sort[0].direction is SortDirection.DESC


def test_query_request_accepts_uppercase_sort_direction() -> None:
    payload = {
        "domain": "Project",
        "operation": "query",
        "sort": [{"field": "name", "direction": "DESC"}],
    }

    parsed = QueryRequest.from_payload(payload)

    assert parsed.sort[0].direction is SortDirection.DESC


def test_query_request_accepts_operator_aliases() -> None:
    payload = {
        "domain": "Project",
        "operation": "query",
        "filters": [{"field": "derivative_count", "operator": ">", "value": 5}],
    }

    parsed = QueryRequest.from_payload(payload)

    assert parsed.filters[0].op.value == "gt"


def test_query_request_accepts_metric_aggregate_function_alias() -> None:
    payload = {
        "domain": "Project",
        "operation": "aggregate",
        "metrics": [{"field": "id", "aggregate_function": "count"}],
    }

    parsed = QueryRequest.from_payload(payload)

    assert parsed.metrics[0].op.value == "count"
