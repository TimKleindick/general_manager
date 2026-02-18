"""Core service layer for GeneralManager MCP gateway operations."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any

from general_manager.logging import get_logger
from general_manager.mcp.contract import (
    AggregateOperator,
    GatewayOperation,
    MetricSpec,
    QueryContext,
    QueryRequest,
    QueryResponse,
)
from general_manager.mcp.graphql_executor import (
    GraphQLTemplateExecutor,
    MCPGraphQLExecutionError,
)
from general_manager.mcp.policy import MCPPolicyError, PolicyEngine
from general_manager.permission.audit import (
    PermissionAuditEvent,
    audit_logging_enabled,
    emit_permission_audit_event,
)


logger = get_logger("mcp.service")


@dataclass(slots=True)
class GatewayService:
    """Executes validated MCP requests against GraphQL templates."""

    policy_engine: PolicyEngine
    executor: GraphQLTemplateExecutor

    @classmethod
    def from_settings(cls) -> "GatewayService":
        return cls(policy_engine=PolicyEngine(), executor=GraphQLTemplateExecutor())

    def discover_data_domains(self, context: QueryContext) -> QueryResponse:
        domains = self.policy_engine.discover_domains()
        self._emit_audit(
            context,
            domain="*",
            operation=GatewayOperation.DISCOVER,
            granted=True,
            row_count=len(domains),
        )
        return QueryResponse(
            data={"domains": domains},
            provenance={"domain": None, "compiled_graphql_template": None},
            errors=[],
            follow_up_suggestions=[
                "Call describe_domain_schema for a specific domain."
            ],
        )

    def describe_domain_schema(
        self, domain: str, context: QueryContext
    ) -> QueryResponse:
        description = self.policy_engine.describe_domain(domain)
        self._emit_audit(
            context,
            domain=domain,
            operation=GatewayOperation.SCHEMA,
            granted=True,
            row_count=1,
        )
        return QueryResponse(
            data={"schema": description},
            provenance={"domain": domain, "compiled_graphql_template": None},
            errors=[],
            follow_up_suggestions=[
                "Use query_domain_data with a small page_size first."
            ],
        )

    def run_query(self, request: QueryRequest, context: QueryContext) -> QueryResponse:
        started = perf_counter()
        try:
            policy = self.policy_engine.validate_operation(request)
            selected_fields = self._resolve_selected_fields(
                request, policy.readable_fields
            )
            exec_result = self.executor.execute(
                request, policy, context, selected_fields
            )

            data: dict[str, Any] = {
                "rows": exec_result.rows,
                "aggregates": {},
                "page_info": exec_result.page_info,
            }
            if request.operation is GatewayOperation.AGGREGATE:
                data["aggregates"] = self._compute_aggregates(
                    exec_result.rows, request.metrics
                )

            duration_ms = round((perf_counter() - started) * 1000, 2)
            logger.info(
                "mcp query executed",
                context={
                    "domain": request.domain,
                    "operation": request.operation.value,
                    "request_id": context.request_id,
                    "user_id": getattr(context.user, "id", None),
                    "row_count": len(exec_result.rows),
                    "duration_ms": duration_ms,
                },
            )

            self._emit_audit(
                context,
                domain=request.domain,
                operation=request.operation,
                granted=True,
                row_count=len(exec_result.rows),
            )

            return QueryResponse(
                data=data,
                provenance={
                    "domain": request.domain,
                    "compiled_graphql_template": exec_result.compiled.template_name,
                    "effective_filters": exec_result.compiled.effective_filter,
                    "effective_excludes": exec_result.compiled.effective_exclude,
                    "applied_limits": {
                        "page": request.page,
                        "page_size": request.page_size,
                    },
                    "duration_ms": duration_ms,
                },
                errors=[],
                follow_up_suggestions=self._suggestions_for(request),
            )
        except (MCPPolicyError, MCPGraphQLExecutionError) as exc:
            self._emit_audit(
                context,
                domain=request.domain,
                operation=request.operation,
                granted=False,
                row_count=0,
                error_code=getattr(exc, "code", "GATEWAY_ERROR"),
            )
            return QueryResponse(
                data={"rows": [], "aggregates": {}, "page_info": {}},
                provenance={
                    "domain": request.domain,
                    "compiled_graphql_template": None,
                    "effective_filters": {},
                    "applied_limits": {
                        "page": request.page,
                        "page_size": request.page_size,
                    },
                },
                errors=[
                    {
                        "code": getattr(exc, "code", "GATEWAY_ERROR"),
                        "message": str(exc),
                    }
                ],
            )

    def explain_query_plan(
        self, request: QueryRequest, context: QueryContext
    ) -> QueryResponse:
        policy = self.policy_engine.validate_operation(request)
        selected_fields = self._resolve_selected_fields(request, policy.readable_fields)
        compiled = self.executor.compile_query(request, policy, selected_fields)
        self._emit_audit(
            context,
            domain=request.domain,
            operation=GatewayOperation.EXPLAIN,
            granted=True,
            row_count=0,
        )
        return QueryResponse(
            data={
                "plan": {
                    "domain": request.domain,
                    "manager": policy.manager_name,
                    "template": compiled.template_name,
                    "graphql": compiled.query,
                    "effective_filter": compiled.effective_filter,
                    "effective_exclude": compiled.effective_exclude,
                    "selected_fields": selected_fields,
                }
            },
            provenance={
                "domain": request.domain,
                "compiled_graphql_template": compiled.template_name,
            },
            errors=[],
            follow_up_suggestions=["Run query_domain_data to execute this plan."],
        )

    @staticmethod
    def _resolve_selected_fields(
        request: QueryRequest, readable_fields: set[str]
    ) -> list[str]:
        if request.select:
            return list(request.select)
        return sorted(readable_fields)

    @staticmethod
    def _coerce_numeric(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None

    def _compute_aggregates(
        self,
        rows: list[dict[str, Any]],
        metrics: list[MetricSpec],
    ) -> dict[str, Any]:
        output: dict[str, Any] = {}
        if not metrics:
            output["count"] = len(rows)
            return output

        for metric in metrics:
            key = metric.alias or f"{metric.op.value}_{metric.field}"
            values = [
                self._coerce_numeric(row.get(metric.field))
                for row in rows
                if isinstance(row, dict)
            ]
            normalized = [value for value in values if value is not None]

            if metric.op is AggregateOperator.COUNT:
                output[key] = len(rows)
            elif metric.op is AggregateOperator.SUM:
                output[key] = sum(normalized)
            elif metric.op is AggregateOperator.AVG:
                output[key] = (
                    (sum(normalized) / len(normalized)) if normalized else None
                )
            elif metric.op is AggregateOperator.MIN:
                output[key] = min(normalized) if normalized else None
            elif metric.op is AggregateOperator.MAX:
                output[key] = max(normalized) if normalized else None

        return output

    @staticmethod
    def _suggestions_for(request: QueryRequest) -> list[str]:
        if request.operation is GatewayOperation.AGGREGATE:
            return ["Add group_by fields for segmented metrics."]
        return [
            "Add filters to narrow the result set.",
            "Use aggregate_domain_metrics for summary statistics.",
        ]

    @staticmethod
    def _emit_audit(
        context: QueryContext,
        *,
        domain: str,
        operation: GatewayOperation,
        granted: bool,
        row_count: int,
        error_code: str | None = None,
    ) -> None:
        if not audit_logging_enabled():
            return
        emit_permission_audit_event(
            PermissionAuditEvent(
                action="read",
                attributes=(domain,),
                granted=granted,
                user=context.user,
                manager="MCPGateway",
                permissions=(operation.value,),
                metadata={
                    "request_id": context.request_id,
                    "tenant": context.tenant,
                    "row_count": row_count,
                    "error_code": error_code,
                },
            )
        )
