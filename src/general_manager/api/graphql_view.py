"""GraphQL view wrapper with optional metrics instrumentation."""

from __future__ import annotations

import time
import inspect
from collections.abc import Iterable, Mapping, Sequence
from contextlib import nullcontext
from typing import TYPE_CHECKING, Protocol, cast

from asgiref.sync import async_to_sync
from graphql import (
    ExecutionResult,
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    GraphQLError,
    GraphQLSchema,
    InlineFragmentNode,
    OperationType,
    get_operation_ast,
    parse,
)
from graphql.language.ast import SelectionNode

if TYPE_CHECKING:
    MUTATION_ERRORS_FLAG: str

    def set_rollback() -> None: ...

    class _GrapheneSchema(Protocol):
        graphql_schema: GraphQLSchema

    class GraphQLView:
        batch: bool
        schema: _GrapheneSchema

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
from general_manager.as_of import HistoricalContextConflictError, as_of
from general_manager.api.graphql_as_of import extract_as_of_search_date
from general_manager.api.graphql_errors import (
    PublicGraphQLError,
    historical_graphql_error,
)
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
_ASYNC_MUTATION_MESSAGE = (
    "Async mutations are not supported by the synchronous GraphQL endpoint."
)


class _GraphQLExecutionResult(Protocol):
    """GraphQL execution result attributes used by the view wrapper."""

    @property
    def data(self) -> object: ...

    @property
    def errors(self) -> Sequence[Exception] | None: ...


async def _await_execution_result(result: object) -> object:
    """Await an async GraphQL execution while its request contexts are active."""
    return await result  # type: ignore[misc]


def _close_awaitable(result: object) -> None:
    """Close an unstarted mutation awaitable so its body cannot run later."""
    visited: set[int] = set()

    def close_nested(value: object) -> None:
        identity = id(value)
        if identity in visited:
            return
        visited.add(identity)

        if inspect.iscoroutine(value):
            frame = value.cr_frame
            if frame is not None:
                for nested in frame.f_locals.values():
                    close_nested(nested)
            value.close()
            return
        if type(value) is dict:
            for nested in cast(dict[object, object], value).values():
                close_nested(nested)
            return
        if type(value) in {list, tuple, set}:
            for nested in cast(Iterable[object], value):
                close_nested(nested)
            return
        if inspect.isawaitable(value):
            close = getattr(value, "close", None)
            if callable(close):
                close()
                return
            cancel = getattr(value, "cancel", None)
            if callable(cancel):
                cancel()

    close_nested(result)


def _has_declared_async_mutation_resolver(
    schema: GraphQLSchema,
    query: object,
    operation_name: str | None,
) -> bool:
    """Return whether a selected mutation declares an async root resolver."""
    if not isinstance(query, str):
        return False
    try:
        document = parse(query)
    except GraphQLError:
        return False
    operation = get_operation_ast(document, operation_name)
    mutation_type = schema.mutation_type
    if (
        operation is None
        or operation.operation is not OperationType.MUTATION
        or mutation_type is None
    ):
        return False

    fragments = {
        definition.name.value: definition
        for definition in document.definitions
        if isinstance(definition, FragmentDefinitionNode)
    }
    visited_fragments: set[str] = set()

    def selections_are_async(selections: Sequence[SelectionNode]) -> bool:
        for selection in selections:
            if isinstance(selection, FieldNode):
                field = mutation_type.fields.get(selection.name.value)
                resolver = None if field is None else field.resolve
                if resolver is not None and inspect.iscoroutinefunction(
                    inspect.unwrap(resolver)
                ):
                    return True
            elif isinstance(selection, InlineFragmentNode):
                if selections_are_async(selection.selection_set.selections):
                    return True
            elif isinstance(selection, FragmentSpreadNode):
                fragment_name = selection.name.value
                if fragment_name in visited_fragments:
                    continue
                visited_fragments.add(fragment_name)
                fragment = fragments.get(fragment_name)
                if fragment is not None and selections_are_async(
                    fragment.selection_set.selections
                ):
                    return True
        return False

    return selections_are_async(operation.selection_set.selections)


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
        execution_result: _GraphQLExecutionResult | None
        try:
            search_date = extract_as_of_search_date(
                query=query,
                variables=variables,
                operation_name=operation_name,
                schema=self.schema.graphql_schema,
            )
        except PublicGraphQLError as error:
            execution_result = ExecutionResult(data=None, errors=[error])
        except GraphQLError:
            # Preserve Graphene-Django's normal syntax-error execution path.
            with ensure_calculation_run_context():
                execution_result = self.execute_graphql_request(
                    request, data, query, variables, operation_name, show_graphiql
                )
                execution_result = self._complete_execution_result(
                    execution_result,
                    query=query,
                    operation_name=operation_name,
                )
        else:
            execution_context = (
                nullcontext() if search_date is None else as_of(search_date)
            )
            try:
                if _has_declared_async_mutation_resolver(
                    self.schema.graphql_schema,
                    query,
                    operation_name,
                ):
                    execution_result = ExecutionResult(
                        data=None,
                        errors=[
                            PublicGraphQLError(
                                _ASYNC_MUTATION_MESSAGE,
                                code="GRAPHQL_VALIDATION_FAILED",
                            )
                        ],
                    )
                else:
                    with execution_context, ensure_calculation_run_context():
                        execution_result = self.execute_graphql_request(
                            request,
                            data,
                            query,
                            variables,
                            operation_name,
                            show_graphiql,
                        )
                        execution_result = self._complete_execution_result(
                            execution_result,
                            query=query,
                            operation_name=operation_name,
                        )
            except HistoricalContextConflictError as error:
                public_error = historical_graphql_error(error)
                assert public_error is not None
                execution_result = ExecutionResult(data=None, errors=[public_error])
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

    @staticmethod
    def _complete_execution_result(
        execution_result: object,
        *,
        query: str | None,
        operation_name: str | None,
    ) -> _GraphQLExecutionResult | None:
        """Await async queries and reject async mutations before their body runs."""
        if not inspect.isawaitable(execution_result):
            return cast(_GraphQLExecutionResult | None, execution_result)
        if resolve_operation_type(query, operation_name) == "mutation":
            _close_awaitable(execution_result)
            return ExecutionResult(
                data=None,
                errors=[
                    PublicGraphQLError(
                        _ASYNC_MUTATION_MESSAGE,
                        code="GRAPHQL_VALIDATION_FAILED",
                    )
                ],
            )
        return cast(
            _GraphQLExecutionResult | None,
            async_to_sync(_await_execution_result)(execution_result),
        )

    def format_error(self, error: Exception) -> Mapping[str, object]:
        """Format historical failures with stable public codes."""
        if isinstance(error, GraphQLError) and isinstance(
            error.original_error, Exception
        ):
            public_error = historical_graphql_error(error.original_error)
            if public_error is not None:
                logger.error(
                    "graphql historical error",
                    context={
                        "error": type(error.original_error).__name__,
                        "message": str(error.original_error),
                    },
                    exc_info=error.original_error,
                )
                return GraphQLError(
                    public_error.message,
                    nodes=error.nodes,
                    source=error.source,
                    positions=error.positions,
                    path=error.path,
                    extensions=public_error.extensions,
                ).formatted
        return super().format_error(error)

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
                        code=self._metrics_error_code(error),
                    )
        except Exception as exc:  # pragma: no cover - safety net  # noqa: BLE001
            logger.debug(
                "graphql metrics recording failed",
                context={"error": type(exc).__name__, "message": str(exc)},
            )

    @staticmethod
    def _metrics_error_code(error: Exception) -> str:
        """Return a stable code for direct and wrapped historical errors."""
        if isinstance(error, GraphQLError) and isinstance(
            error.original_error, Exception
        ):
            public_error = historical_graphql_error(error.original_error)
            if public_error is not None:
                return extract_error_code(public_error)
        return extract_error_code(error)

    # Inherit GraphQLView.dispatch without changes.
