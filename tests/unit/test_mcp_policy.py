from general_manager.api.graphql import GraphQL
from general_manager.mcp.contract import QueryRequest
from general_manager.mcp.policy import (
    DomainPolicy,
    GatewayPolicyConfig,
    MCPPolicyError,
    PolicyEngine,
)


class DummyManager:
    pass


def _policy_engine() -> PolicyEngine:
    GraphQL.manager_registry["Project"] = DummyManager  # type: ignore[assignment]
    return PolicyEngine(
        GatewayPolicyConfig(
            enabled=True,
            domains={
                "Project": DomainPolicy(
                    domain="Project",
                    manager_name="Project",
                    readable_fields={"id", "name", "status", "budget"},
                    filterable_fields={"status", "name"},
                    sortable_fields={"name"},
                    aggregate_fields={"budget"},
                )
            },
        )
    )


def test_policy_allows_valid_query() -> None:
    engine = _policy_engine()
    request = QueryRequest.from_payload(
        {
            "domain": "Project",
            "operation": "query",
            "select": ["id", "name"],
            "filters": [{"field": "status", "op": "eq", "value": "active"}],
            "sort": [{"field": "name", "direction": "asc"}],
        }
    )

    policy = engine.validate_operation(request)

    assert policy.domain == "Project"


def test_policy_rejects_non_allowlisted_field() -> None:
    engine = _policy_engine()
    request = QueryRequest.from_payload(
        {
            "domain": "Project",
            "operation": "query",
            "select": ["secret"],
        }
    )

    try:
        engine.validate_operation(request)
    except MCPPolicyError as exc:
        assert exc.code == "FIELD_NOT_ALLOWED"
    else:
        raise AssertionError


def test_policy_rejects_unknown_domain() -> None:
    engine = _policy_engine()
    request = QueryRequest.from_payload(
        {
            "domain": "Unknown",
            "operation": "query",
        }
    )

    try:
        engine.validate_operation(request)
    except MCPPolicyError as exc:
        assert exc.code == "UNKNOWN_DOMAIN"
    else:
        raise AssertionError
