"""Template-based GraphQL execution backend for the MCP gateway."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from graphql import GraphQLError

from general_manager.api.graphql import GraphQL
from general_manager.logging import get_logger
from general_manager.mcp.contract import FilterOperator, QueryContext, QueryRequest
from general_manager.mcp.policy import DomainPolicy
from general_manager.utils.format_string import snake_to_camel


logger = get_logger("mcp.graphql_executor")


class MCPGraphQLExecutionError(RuntimeError):
    """Raised when GraphQL execution fails."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class CompiledQuery:
    """Compiled GraphQL template details."""

    template_name: str
    query: str
    effective_filter: dict[str, Any]
    effective_exclude: dict[str, Any]


@dataclass(slots=True)
class GraphQLExecutionResult:
    """Normalized result from GraphQL execution."""

    rows: list[dict[str, Any]]
    page_info: dict[str, Any]
    compiled: CompiledQuery


class _ContextValue:
    """Minimal GraphQL context object that carries the authenticated user."""

    def __init__(self, user: Any) -> None:
        self.user = user


def _to_graphql_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_to_graphql_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        entries = []
        for key, item in value.items():
            key_name = str(key)
            entries.append(f"{key_name}: {_to_graphql_literal(item)}")
        return "{" + ", ".join(entries) + "}"
    return _to_graphql_literal(str(value))


def _lookup_key(field: str, operator: FilterOperator) -> str:
    base = snake_to_camel(field)
    if operator is FilterOperator.EQ:
        return base
    if operator is FilterOperator.NE:
        return base
    if operator is FilterOperator.CONTAINS:
        return f"{base}_Contains"
    if operator is FilterOperator.STARTS_WITH:
        return f"{base}_Startswith"
    if operator is FilterOperator.ENDS_WITH:
        return f"{base}_Endswith"
    if operator is FilterOperator.IN:
        return f"{base}_In"
    if operator is FilterOperator.GT:
        return f"{base}_Gt"
    if operator is FilterOperator.GTE:
        return f"{base}_Gte"
    if operator is FilterOperator.LT:
        return f"{base}_Lt"
    if operator is FilterOperator.LTE:
        return f"{base}_Lte"
    if operator is FilterOperator.IS_NULL:
        return f"{base}_Isnull"
    return base


class GraphQLTemplateExecutor:
    """Compiles structured gateway requests into fixed GraphQL templates."""

    def compile_query(
        self,
        request: QueryRequest,
        policy: DomainPolicy,
        selected_fields: list[str],
    ) -> CompiledQuery:
        manager = policy.manager_name
        list_field_name = snake_to_camel(f"{manager.lower()}_list")

        gql_fields = " ".join(snake_to_camel(field) for field in selected_fields)

        filter_dict: dict[str, Any] = {}
        exclude_dict: dict[str, Any] = {}
        for clause in request.filters:
            key = _lookup_key(clause.field, clause.op)
            if clause.op is FilterOperator.NE:
                exclude_dict[key] = clause.value
            else:
                filter_dict[key] = clause.value

        args: list[str] = [f"page: {request.page}", f"pageSize: {request.page_size}"]
        if filter_dict:
            args.append(f"filter: {_to_graphql_literal(filter_dict)}")
        if exclude_dict:
            args.append(f"exclude: {_to_graphql_literal(exclude_dict)}")
        if request.sort:
            primary_sort = request.sort[0]
            args.append(f"sortBy: {primary_sort.field}")
            if primary_sort.direction.value == "desc":
                args.append("reverse: true")
        if request.group_by:
            args.append(
                "groupBy: "
                + _to_graphql_literal(
                    [snake_to_camel(field) for field in request.group_by]
                )
            )

        args_text = ", ".join(args)
        template_name = f"{request.domain.lower()}_list"
        query = (
            "query GatewayQuery { "
            f"{list_field_name}({args_text}) "
            "{ items { "
            f"{gql_fields}"
            " } pageInfo { totalCount currentPage totalPages pageSize } }"
            " }"
        )

        return CompiledQuery(
            template_name=template_name,
            query=query,
            effective_filter=filter_dict,
            effective_exclude=exclude_dict,
        )

    def execute(
        self,
        request: QueryRequest,
        policy: DomainPolicy,
        context: QueryContext,
        selected_fields: list[str],
    ) -> GraphQLExecutionResult:
        schema = GraphQL.get_schema()
        if schema is None:
            raise MCPGraphQLExecutionError(
                "SCHEMA_UNAVAILABLE",
                "GraphQL schema is not configured. Enable AUTOCREATE_GRAPHQL.",
            )

        compiled = self.compile_query(request, policy, selected_fields)
        logger.debug(
            "compiled gateway graphql query",
            context={
                "domain": request.domain,
                "operation": request.operation.value,
                "template": compiled.template_name,
            },
        )

        result = schema.execute(
            compiled.query,
            context_value=_ContextValue(context.user),
        )

        if result.errors:
            text = "; ".join(self._format_error(err) for err in result.errors)
            raise MCPGraphQLExecutionError("GRAPHQL_ERROR", text)

        data = result.data or {}
        manager = policy.manager_name
        list_key = snake_to_camel(f"{manager.lower()}_list")
        payload = data.get(list_key)
        if not isinstance(payload, dict):
            raise MCPGraphQLExecutionError(
                "GRAPHQL_PAYLOAD_INVALID",
                f"GraphQL payload missing list field '{list_key}'.",
            )

        items = payload.get("items", [])
        if not isinstance(items, list):
            raise MCPGraphQLExecutionError(
                "GRAPHQL_PAYLOAD_INVALID", "GraphQL payload items must be a list."
            )
        rows = [item for item in items if isinstance(item, dict)]

        page_info_raw = payload.get("pageInfo") or {}
        page_info: dict[str, Any]
        if isinstance(page_info_raw, dict):
            page_info = {
                "total_count": page_info_raw.get("totalCount"),
                "current_page": page_info_raw.get("currentPage"),
                "total_pages": page_info_raw.get("totalPages"),
                "page_size": page_info_raw.get("pageSize"),
            }
        else:
            page_info = {
                "total_count": len(rows),
                "current_page": request.page,
                "total_pages": 1,
                "page_size": request.page_size,
            }

        return GraphQLExecutionResult(rows=rows, page_info=page_info, compiled=compiled)

    @staticmethod
    def _format_error(error: GraphQLError) -> str:
        if hasattr(error, "message"):
            return str(error.message)
        return str(error)
