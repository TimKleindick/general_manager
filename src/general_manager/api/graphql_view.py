"""GraphQL view wrapper with optional metrics instrumentation."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    MUTATION_ERRORS_FLAG: str

    def set_rollback() -> None: ...

    class GraphQLView:
        batch: bool

        @classmethod
        def as_view(cls, **initkwargs: object) -> object: ...

        def get_middleware(self, request: object) -> list[object] | None: ...

        def get_graphql_params(
            self,
            request: object,
            data: object,
        ) -> tuple[str | None, object, str | None, object]: ...

        def execute_graphql_request(
            self,
            request: object,
            data: object,
            query: str | None,
            variables: object,
            operation_name: str | None,
            show_graphiql: bool,
        ) -> _GraphQLExecutionResult | None: ...

        def format_error(self, error: Exception) -> Mapping[str, object]: ...

        def json_encode(
            self,
            request: object,
            response: Mapping[str, object],
            *,
            pretty: bool = False,
        ) -> object: ...

else:
    from graphene_django.constants import MUTATION_ERRORS_FLAG
    from graphene_django.utils.utils import set_rollback
    from graphene_django.views import GraphQLView

from general_manager.cache.run_context import ensure_calculation_run_context
from general_manager.metrics.graphql import (
    GraphQLRequestStatus,
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


class _GraphQLExecutionResult(Protocol):
    """GraphQL execution result attributes used by the view wrapper."""

    data: object
    errors: Sequence[Exception] | None


class GeneralManagerGraphQLView(GraphQLView):
    """
    Graphene-Django view wrapper with GeneralManager metrics instrumentation.

    The wrapper preserves Graphene-Django response behavior while adding
    optional resolver timing middleware and request/error metrics. Metrics
    failures are logged at debug level and do not alter the GraphQL response.
    """

    def get_middleware(self, request: object) -> list[object] | None:
        """
        Return Graphene middleware with resolver timing added when enabled.

        The base Graphene middleware value is returned unchanged when resolver
        timing metrics are disabled. If the base value is `None` and timing is
        enabled, a one-item list containing `GraphQLResolverTimingMiddleware` is
        returned. Otherwise the timing middleware is appended after existing
        middleware so normal project middleware runs first. Existing timing
        middleware instances are not duplicated, making repeated calls
        idempotent when the base middleware stack is stable. `request` is passed
        through to Graphene-Django without inspection by this wrapper.
        """
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

    def get_response(
        self,
        request: object,
        data: object,
        show_graphiql: bool = False,
    ) -> tuple[object | None, int]:
        """
        Execute one GraphQL request and return `(encoded_response, status_code)`.

        The method mirrors Graphene-Django response shaping: no execution result
        returns `(None, 200)`, request-level GraphQL errors without a path return
        status `400`, and other successful executions return status `200`.
        Batched responses include `id` and `status` fields from Graphene's
        request metadata. `request`, `data`, query parsing, `operationName`/
        operation-name handling, variable coercion, malformed query handling,
        GraphiQL behavior, concrete encoded response type, and concrete batch
        response item shape are delegated to Graphene-Django. This wrapper only
        guarantees the outer `(encoded_response, status_code)` return shape and
        the GeneralManager metrics/rollback side effects documented here.

        Side effects:
            Ensures the calculation run context is active during execution,
            marks Django rollback when Graphene-Django sets
            `MUTATION_ERRORS_FLAG` on the request or when request-level GraphQL
            errors are present, and records request/error metrics when metrics
            are enabled. Metrics are recorded for GraphiQL requests when
            Graphene returns an execution result.

        Partial GraphQL errors count as metrics status `error`; executions
        without errors count as `success`. Metrics backend failures, label
        normalization failures, and error-code extraction failures are swallowed
        and logged by `_record_metrics`. Exceptions from Graphene request
        parsing, execution, formatting, encoding, rollback handling, or the
        calculation run context propagate.
        """
        query, variables, operation_name, request_id = self.get_graphql_params(
            request, data
        )

        start = time.perf_counter()
        with ensure_calculation_run_context():
            execution_result = self.execute_graphql_request(
                request, data, query, variables, operation_name, show_graphiql
            )
        duration = time.perf_counter() - start

        if getattr(request, MUTATION_ERRORS_FLAG, False) is True:
            set_rollback()

        status_code = 200
        if execution_result:
            response: dict[str, object] = {}

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
        execution_result: _GraphQLExecutionResult,
    ) -> None:
        """
        Record GraphQL request/error metrics without affecting responses.

        The helper is called only when `graphql_metrics_enabled()` is true.
        Operation names, operation types, and error codes are normalized by the
        metrics module. Missing or invalid query text produces operation type
        `unknown`; query strings and variables are never used directly as labels.
        """
        backend = get_graphql_metrics_backend()
        try:
            op_name = normalize_operation_name(operation_name)
            op_type = resolve_operation_type(query, operation_name)
            status: GraphQLRequestStatus = (
                "error" if execution_result.errors else "success"
            )
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
