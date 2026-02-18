"""Policy layer and allowlist registry for the GeneralManager MCP gateway."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from django.conf import settings

from general_manager.api.graphql import GraphQL
from general_manager.logging import get_logger
from general_manager.mcp.contract import (
    AggregateOperator,
    FilterSpec,
    GatewayOperation,
    MetricSpec,
    QueryRequest,
    SortSpec,
)


logger = get_logger("mcp.policy")


class MCPPolicyError(PermissionError):
    """Raised when a request violates MCP gateway policy."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class DomainPolicy:
    """Allowlist configuration for one exposed domain."""

    domain: str
    manager_name: str
    readable_fields: set[str] = field(default_factory=set)
    filterable_fields: set[str] = field(default_factory=set)
    sortable_fields: set[str] = field(default_factory=set)
    aggregate_fields: set[str] = field(default_factory=set)

    @property
    def manager_class(self) -> type | None:
        return GraphQL.manager_registry.get(self.manager_name)


@dataclass(slots=True)
class GatewayPolicyConfig:
    """Resolved policy configuration from settings."""

    enabled: bool
    domains: dict[str, DomainPolicy]


def _domain_from_mapping(domain: str, data: Mapping[str, Any]) -> DomainPolicy:
    manager_name = data.get("manager", domain)
    readable_fields = set(data.get("readable_fields", []))
    filterable_fields = set(data.get("filterable_fields", readable_fields))
    sortable_fields = set(data.get("sortable_fields", readable_fields))
    aggregate_fields = set(data.get("aggregate_fields", []))

    if not isinstance(manager_name, str) or not manager_name:
        raise MCPPolicyError(
            "INVALID_DOMAIN_CONFIG", f"Invalid manager for domain '{domain}'."
        )

    for label, values in (
        ("readable_fields", readable_fields),
        ("filterable_fields", filterable_fields),
        ("sortable_fields", sortable_fields),
        ("aggregate_fields", aggregate_fields),
    ):
        if any(not isinstance(item, str) or not item for item in values):
            raise MCPPolicyError(
                "INVALID_DOMAIN_CONFIG",
                f"Domain '{domain}' has invalid values for '{label}'.",
            )

    return DomainPolicy(
        domain=domain,
        manager_name=manager_name,
        readable_fields=readable_fields,
        filterable_fields=filterable_fields,
        sortable_fields=sortable_fields,
        aggregate_fields=aggregate_fields,
    )


def load_policy_config() -> GatewayPolicyConfig:
    """Load gateway policy configuration from Django settings."""
    gm_config = getattr(settings, "GENERAL_MANAGER", {})
    gateway_config = {}
    if isinstance(gm_config, Mapping):
        gateway_config = gm_config.get("MCP_GATEWAY", {})
    if not isinstance(gateway_config, Mapping):
        gateway_config = {}

    enabled = bool(gateway_config.get("ENABLED", False))
    raw_domains = gateway_config.get("DOMAINS", {})
    domains: dict[str, DomainPolicy] = {}
    if isinstance(raw_domains, Mapping):
        for domain, payload in raw_domains.items():
            if not isinstance(domain, str) or not isinstance(payload, Mapping):
                continue
            domains[domain] = _domain_from_mapping(domain, payload)

    return GatewayPolicyConfig(enabled=enabled, domains=domains)


class PolicyEngine:
    """Validates structured requests against domain allowlists."""

    def __init__(self, config: GatewayPolicyConfig | None = None) -> None:
        self.config = config or load_policy_config()

    def ensure_gateway_enabled(self) -> None:
        if not self.config.enabled:
            raise MCPPolicyError(
                "GATEWAY_DISABLED",
                "GeneralManager MCP gateway is disabled in settings.",
            )

    def list_domains(self) -> list[DomainPolicy]:
        self.ensure_gateway_enabled()
        return list(self.config.domains.values())

    def get_domain_policy(self, domain: str) -> DomainPolicy:
        self.ensure_gateway_enabled()
        policy = self.config.domains.get(domain)
        if policy is None:
            raise MCPPolicyError("UNKNOWN_DOMAIN", f"Domain '{domain}' is not exposed.")
        if policy.manager_class is None:
            raise MCPPolicyError(
                "UNKNOWN_MANAGER",
                f"Domain '{domain}' maps to unknown manager '{policy.manager_name}'.",
            )
        return policy

    def validate_operation(
        self,
        request: QueryRequest,
    ) -> DomainPolicy:
        policy = self.get_domain_policy(request.domain)

        if request.operation not in {
            GatewayOperation.QUERY,
            GatewayOperation.AGGREGATE,
            GatewayOperation.EXPLAIN,
            GatewayOperation.SCHEMA,
            GatewayOperation.DISCOVER,
        }:
            raise MCPPolicyError(
                "UNSUPPORTED_OPERATION",
                f"Operation '{request.operation.value}' is not supported.",
            )

        self._validate_select(policy, request.select)
        self._validate_filters(policy, request.filters)
        self._validate_sort(policy, request.sort)
        self._validate_group_by(policy, request.group_by)
        self._validate_metrics(policy, request.metrics)
        return policy

    @staticmethod
    def _validate_select(policy: DomainPolicy, select: list[str]) -> None:
        if not select:
            return
        blocked = sorted(
            field for field in select if field not in policy.readable_fields
        )
        if blocked:
            raise MCPPolicyError(
                "FIELD_NOT_ALLOWED",
                f"Fields not allowed for domain '{policy.domain}': {', '.join(blocked)}",
            )

    @staticmethod
    def _validate_filters(policy: DomainPolicy, filters: list[FilterSpec]) -> None:
        blocked = sorted(
            {
                item.field
                for item in filters
                if item.field not in policy.filterable_fields
            }
        )
        if blocked:
            raise MCPPolicyError(
                "FILTER_FIELD_NOT_ALLOWED",
                (
                    f"Filter fields not allowed for domain '{policy.domain}': "
                    f"{', '.join(blocked)}"
                ),
            )

    @staticmethod
    def _validate_sort(policy: DomainPolicy, sort_items: list[SortSpec]) -> None:
        blocked = sorted(
            {
                item.field
                for item in sort_items
                if item.field not in policy.sortable_fields
            }
        )
        if blocked:
            raise MCPPolicyError(
                "SORT_FIELD_NOT_ALLOWED",
                (
                    f"Sort fields not allowed for domain '{policy.domain}': "
                    f"{', '.join(blocked)}"
                ),
            )

    @staticmethod
    def _validate_group_by(policy: DomainPolicy, group_by: list[str]) -> None:
        blocked = sorted(
            field for field in group_by if field not in policy.readable_fields
        )
        if blocked:
            raise MCPPolicyError(
                "GROUP_FIELD_NOT_ALLOWED",
                (
                    f"Group-by fields not allowed for domain '{policy.domain}': "
                    f"{', '.join(blocked)}"
                ),
            )

    @staticmethod
    def _validate_metrics(policy: DomainPolicy, metrics: list[MetricSpec]) -> None:
        for metric in metrics:
            if metric.op is AggregateOperator.COUNT:
                continue
            if metric.field not in policy.aggregate_fields:
                raise MCPPolicyError(
                    "AGGREGATE_FIELD_NOT_ALLOWED",
                    (
                        f"Aggregate field '{metric.field}' is not allowed "
                        f"for domain '{policy.domain}'."
                    ),
                )

    def describe_domain(self, domain: str) -> dict[str, Any]:
        policy = self.get_domain_policy(domain)
        manager_class = policy.manager_class
        if manager_class is None:
            raise MCPPolicyError(
                "UNKNOWN_MANAGER",
                f"Domain '{domain}' maps to unknown manager '{policy.manager_name}'.",
            )
        return {
            "domain": policy.domain,
            "manager": policy.manager_name,
            "readable_fields": sorted(policy.readable_fields),
            "filterable_fields": sorted(policy.filterable_fields),
            "sortable_fields": sorted(policy.sortable_fields),
            "aggregate_fields": sorted(policy.aggregate_fields),
            "operations": [
                GatewayOperation.QUERY.value,
                GatewayOperation.AGGREGATE.value,
                GatewayOperation.SCHEMA.value,
                GatewayOperation.DISCOVER.value,
                GatewayOperation.EXPLAIN.value,
            ],
        }

    def discover_domains(self) -> list[dict[str, Any]]:
        domains: list[dict[str, Any]] = []
        for policy in self.list_domains():
            manager_class = policy.manager_class
            if manager_class is None:
                logger.warning(
                    "skipping domain with unresolved manager",
                    context={"domain": policy.domain, "manager": policy.manager_name},
                )
                continue
            domains.append(
                {
                    "domain": policy.domain,
                    "manager": policy.manager_name,
                    "readable_fields": sorted(policy.readable_fields),
                }
            )
        return sorted(domains, key=lambda item: item["domain"])
