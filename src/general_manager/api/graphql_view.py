"""GraphQL view wrapper with optional metrics instrumentation."""

from __future__ import annotations

import time
from typing import Any

from graphene_django.constants import MUTATION_ERRORS_FLAG
from graphene_django.utils.utils import set_rollback
from graphene_django.views import GraphQLView

from general_manager.metrics.graphql import (
    GraphQLResolverTimingMiddleware,
    extract_error_code,
    get_graphql_metrics_backend,
    graphql_metrics_enabled,
    graphql_metrics_resolver_timing_enabled,
    normalize_operation_name,
    resolve_operation_type,
)
from general_manager.logging import get_logger

logger = get_logger("graphql.metrics")


class GeneralManagerGraphQLView(GraphQLView):
    """GraphQL view that emits optional request-level metrics."""

    def get_middleware(self, request):  # type: ignore[override]
        middleware = super().get_middleware(request)
        if not graphql_metrics_resolver_timing_enabled():
            return middleware
        if middleware is None:
            return [GraphQLResolverTimingMiddleware()]
        if any(
            isinstance(entry, GraphQLResolverTimingMiddleware) for entry in middleware
        ):
            return middleware
        return [*middleware, GraphQLResolverTimingMiddleware()]

    def get_response(self, request, data, show_graphiql: bool = False):  # type: ignore[override]
        query, variables, operation_name, request_id = self.get_graphql_params(
            request, data
        )

        start = time.perf_counter()
        execution_result = self.execute_graphql_request(
            request, data, query, variables, operation_name, show_graphiql
        )
        duration = time.perf_counter() - start

        if getattr(request, MUTATION_ERRORS_FLAG, False) is True:
            set_rollback()

        status_code = 200
        if execution_result:
            response: dict[str, Any] = {}

            if execution_result.errors:
                set_rollback()
                response["errors"] = [
                    self.format_error(error) for error in execution_result.errors
                ]

            if execution_result.errors and any(
                not getattr(error, "path", None) for error in execution_result.errors
            ):
                status_code = 400
            else:
                response["data"] = execution_result.data

            if self.batch:
                response["id"] = request_id
                response["status"] = status_code

            result = self.json_encode(request, response, pretty=show_graphiql)
        else:
            result = None

        if execution_result and graphql_metrics_enabled():
            self._record_metrics(
                duration=duration,
                query=query,
                operation_name=operation_name,
                execution_result=execution_result,
            )

        return result, status_code

    def _record_metrics(
        self,
        *,
        duration: float,
        query: str | None,
        operation_name: str | None,
        execution_result: Any,
    ) -> None:
        backend = get_graphql_metrics_backend()
        try:
            op_name = normalize_operation_name(operation_name)
            op_type = resolve_operation_type(query, operation_name)
            status = "error" if execution_result.errors else "success"
            backend.record_request(
                duration=duration,
                operation_name=op_name,
                operation_type=op_type,
                status=status,
            )
            if execution_result.errors:
                for error in execution_result.errors:
                    backend.record_error(
                        operation_name=op_name,
                        code=extract_error_code(error),
                    )
        except Exception as exc:  # pragma: no cover - safety net  # noqa: BLE001
            logger.debug(
                "graphql metrics recording failed",
                context={"error": type(exc).__name__, "message": str(exc)},
            )

    # Inherit GraphQLView.dispatch without changes.
