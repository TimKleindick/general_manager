# type: ignore

import json
import asyncio
import subprocess
import sys
import unittest
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from datetime import UTC, date, datetime
from inspect import signature
from threading import Barrier
from types import SimpleNamespace
import graphene
from django.test import TestCase, override_settings
from django.test import RequestFactory
from django.db.models import NOT_PROVIDED
from unittest.mock import MagicMock, patch
from django.contrib.auth.models import AnonymousUser
from typing import Any, ClassVar, get_args

from general_manager import bootstrap as gm_bootstrap
from general_manager.api.graphql import (
    BigIntScalar,
    MeasurementType,
    GraphQL,
    get_read_permission_filter,
)
from general_manager.api.graphql_mutations import (
    _graphql_mutation_field_name,
    _normalize_mutation_kwargs_for_manager,
)
from general_manager.api.graphql_view import (
    GeneralManagerGraphQLView,
    _close_awaitable,
    _has_declared_async_mutation_resolver,
)
from general_manager.api.graphql_as_of import (
    build_as_of_directive,
    extract_as_of_search_date,
)
from general_manager.as_of import (
    HistoricalContextConflictError,
    HistoricalMutationError,
    HistoricalReadNotSupportedError,
    InvalidSearchDateError,
    as_of,
    current_as_of_date,
)
from general_manager.bucket.base_bucket import Bucket
from general_manager.measurement.measurement import Measurement
from general_manager.manager.general_manager import GeneralManager, GeneralManagerMeta
from general_manager.manager.input import Input
from general_manager.api.property import (
    GraphQLProperty,
    GraphQLPropertyCache,
    GraphQLPropertyReturnAnnotationError,
    GraphQLPropertyTimeoutConfigurationError,
    GraphQLPropertyWarmUpConfigurationError,
    graph_ql_property,
)
from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.orm_interface import OrmInterfaceBase  # noqa: F401
from general_manager.permission.base_permission import ReadPermissionPlan
from graphql import (
    DirectiveLocation,
    GraphQLError,
    GraphQLDirective,
    GraphQLField,
    GraphQLObjectType,
    GraphQLScalarType,
    GraphQLSchema,
    GraphQLString,
    parse,
    specified_directives,
)
from graphql.language import StringValueNode
from graphql.validation import ASTValidationRule


class GraphQLAsOfExecutionTests(unittest.TestCase):
    def test_schema_scalar_coercion_handles_literals_variables_and_defaults(
        self,
    ) -> None:
        schema = self._coercion_schema()
        expected = datetime(2000, 1, 1, tzinfo=UTC)
        cases = (
            ('query Q @asOf(date: "epoch") { ping }', None),
            (
                "query Q($date: DateTime!) @asOf(date: $date) { ping }",
                {"date": "epoch"},
            ),
            (
                'query Q($date: DateTime! = "epoch") @asOf(date: $date) { ping }',
                {},
            ),
        )

        for query, variables in cases:
            with self.subTest(query=query):
                self.assertEqual(
                    extract_as_of_search_date(
                        query=query,
                        variables=variables,
                        operation_name="Q",
                        schema=schema,
                    ),
                    expected,
                )

    def test_schema_coercion_rejects_null_undefined_invalid_and_wrong_type(
        self,
    ) -> None:
        schema = self._coercion_schema()
        cases = (
            (
                "query Q($date: DateTime!) @asOf(date: $date) { ping }",
                {"date": None},
                "BAD_USER_INPUT",
            ),
            (
                "query Q($date: DateTime!) @asOf(date: $date) { ping }",
                {},
                "BAD_USER_INPUT",
            ),
            (
                'query Q @asOf(date: "invalid") { ping }',
                None,
                "BAD_USER_INPUT",
            ),
            (
                "query Q($date: String!) @asOf(date: $date) { ping }",
                {"date": "epoch"},
                "BAD_USER_INPUT",
            ),
        )

        for query, variables, code in cases:
            with self.subTest(query=query), self.assertRaises(GraphQLError) as raised:
                extract_as_of_search_date(
                    query=query,
                    variables=variables,
                    operation_name="Q",
                    schema=schema,
                )
            if code is not None:
                self.assertEqual(raised.exception.extensions["code"], code)

    def test_wrong_declared_variable_type_never_enters_context(self) -> None:
        view = self._view()
        with patch("general_manager.api.graphql_view.as_of") as as_of_mock:
            payload, status = view.get_response(
                SimpleNamespace(GET={}, method="POST"),
                {
                    "query": "query Q($date: String!) @asOf(date: $date) { ping }",
                    "variables": {"date": "2022-01-01"},
                },
            )

        self.assertEqual(status, 400)
        self.assertIn("errors", payload)
        as_of_mock.assert_not_called()

    def test_unrelated_invalid_variables_remain_normal_graphene_errors(self) -> None:
        class Query(graphene.ObjectType):
            echo = graphene.Int(value=graphene.Int(required=True))

            @staticmethod
            def resolve_echo(_root: object, _info: object, value: int) -> int:
                return value

        schema = graphene.Schema(query=Query, types=(graphene.DateTime,))
        gm_bootstrap._attach_as_of_directive(schema)
        view = GeneralManagerGraphQLView(schema=schema)
        view.json_encode = lambda _request, payload, **_kwargs: payload
        cases = (
            (
                (
                    'query Q($count: Int!) @asOf(date: "2022-01-01T00:00:00Z") '
                    "{ echo(value: $count) }"
                ),
                {},
            ),
            (
                (
                    "query Q($date: DateTime!, $count: Int!) "
                    "@asOf(date: $date) { echo(value: $count) }"
                ),
                {"date": "2022-01-01T00:00:00Z", "count": "wrong"},
            ),
        )

        for query, variables in cases:
            with self.subTest(query=query):
                payload, status = view.get_response(
                    SimpleNamespace(GET={}, method="POST"),
                    {"query": query, "variables": variables},
                )
            self.assertEqual(status, 400)
            error = payload["errors"][0]
            self.assertIn("$count", error["message"])
            self.assertNotEqual(
                error.get("extensions", {}).get("code"),
                "BAD_USER_INPUT",
            )

    def test_invalid_date_error_does_not_reflect_unbounded_input(self) -> None:
        invalid = "secret-" * 10_000
        with self.assertRaises(GraphQLError) as raised:
            extract_as_of_search_date(
                query="query Q($date: DateTime!) @asOf(date: $date) { ping }",
                variables={"date": invalid},
                operation_name="Q",
                schema=self._coercion_schema(),
            )

        self.assertEqual(raised.exception.message, "@asOf date is invalid.")
        self.assertNotIn("secret", raised.exception.message)

    def test_extracts_selected_operation_variable_and_ignores_other_operation(
        self,
    ) -> None:
        query = """
            query Current { ping }
            query Historical($date: DateTime!) @asOf(date: $date) { ping }
        """

        self.assertEqual(
            extract_as_of_search_date(
                query=query,
                variables={"date": "2022-01-01T00:00:00Z"},
                operation_name="Historical",
            ),
            datetime(2022, 1, 1, tzinfo=UTC),
        )
        self.assertIsNone(
            extract_as_of_search_date(
                query=query,
                variables={"date": "2022-01-01T00:00:00Z"},
                operation_name="Current",
            )
        )

    def test_extracts_literal_and_absent_directive(self) -> None:
        self.assertEqual(
            extract_as_of_search_date(
                query='query Historical @asOf(date: "2022-01-01T00:00:00Z") { ping }',
                variables=None,
                operation_name="Historical",
            ),
            datetime(2022, 1, 1, tzinfo=UTC),
        )
        self.assertIsNone(
            extract_as_of_search_date(
                query="query Current { ping }",
                variables=None,
                operation_name="Current",
            )
        )

    def test_unselected_operation_has_no_historical_date(self) -> None:
        self.assertIsNone(
            extract_as_of_search_date(
                query='query Historical @asOf(date: "2022-01-01") { ping }',
                variables=None,
                operation_name="Missing",
            )
        )

    def test_as_of_requires_the_schema_directive_definition(self) -> None:
        schema = GraphQLSchema(
            query=GraphQLObjectType(
                "Query",
                {"ping": GraphQLField(GraphQLString)},
            )
        )

        with self.assertRaises(GraphQLError) as raised:
            extract_as_of_search_date(
                query='query Q @asOf(date: "2022-01-01") { ping }',
                variables=None,
                operation_name="Q",
                schema=schema,
            )

        self.assertEqual(
            raised.exception.extensions["code"],
            "GRAPHQL_VALIDATION_FAILED",
        )

    def test_as_of_rejects_schema_definition_without_date_argument(self) -> None:
        schema = GraphQLSchema(
            query=GraphQLObjectType(
                "Query",
                {"ping": GraphQLField(GraphQLString)},
            ),
            directives=[
                *specified_directives,
                GraphQLDirective(name="asOf", locations=[DirectiveLocation.QUERY]),
            ],
        )

        with self.assertRaises(GraphQLError) as raised:
            extract_as_of_search_date(
                query='query Q @asOf(date: "2022-01-01") { ping }',
                variables=None,
                operation_name="Q",
                schema=schema,
            )

        self.assertEqual(raised.exception.extensions["code"], "BAD_USER_INPUT")

    def test_unrelated_schema_validation_error_is_preserved(self) -> None:
        with self.assertRaises(GraphQLError) as raised:
            extract_as_of_search_date(
                query='query Q @asOf(date: "epoch") { missing }',
                variables=None,
                operation_name="Q",
                schema=self._coercion_schema(),
            )

        self.assertIn("missing", raised.exception.message)
        self.assertNotEqual(
            raised.exception.extensions.get("code"),
            "BAD_USER_INPUT",
        )

    def test_rejects_bad_as_of_inputs_with_stable_codes(self) -> None:
        cases = (
            (
                "query Q @asOf { ping }",
                None,
                "GRAPHQL_VALIDATION_FAILED",
            ),
            (
                'query Q @asOf(date: "2022-01-01", date: "2023-01-01") { ping }',
                None,
                "GRAPHQL_VALIDATION_FAILED",
            ),
            (
                "query Q($date: DateTime!) @asOf(date: $date) { ping }",
                {},
                "BAD_USER_INPUT",
            ),
            (
                'query Q @asOf(date: "not-a-date") { ping }',
                None,
                "BAD_USER_INPUT",
            ),
            (
                'query Q @asOf(date: "2022-01-01") @asOf(date: "2023-01-01") { ping }',
                None,
                "HISTORICAL_CONTEXT_CONFLICT",
            ),
            (
                'mutation Q @asOf(date: "2022-01-01") { ping }',
                None,
                "GRAPHQL_VALIDATION_FAILED",
            ),
            (
                'subscription Q @asOf(date: "2022-01-01") { ping }',
                None,
                "GRAPHQL_VALIDATION_FAILED",
            ),
        )

        for query, variables, code in cases:
            with self.subTest(query=query), self.assertRaises(GraphQLError) as raised:
                extract_as_of_search_date(
                    query=query,
                    variables=variables,
                    operation_name="Q",
                )
            self.assertEqual(raised.exception.extensions["code"], code)

    def test_view_nests_as_of_outside_calculation_context(self) -> None:
        events: list[str] = []

        @contextmanager
        def recording_as_of(_date: object):
            events.append("as_of enter")
            try:
                yield
            finally:
                events.append("as_of exit")

        @contextmanager
        def recording_calculation():
            events.append("calculation enter")
            try:
                yield
            finally:
                events.append("calculation exit")

        view = self._view()
        with (
            patch("general_manager.api.graphql_view.as_of", recording_as_of),
            patch(
                "general_manager.api.graphql_view.ensure_calculation_run_context",
                recording_calculation,
            ),
            patch.object(
                view,
                "execute_graphql_request",
                side_effect=lambda *_args: events.append("execute"),
            ),
        ):
            view.get_response(
                SimpleNamespace(GET={}, method="POST"),
                {"query": 'query Q @asOf(date: "2022-01-01") { ping }'},
            )

        self.assertEqual(
            events,
            [
                "as_of enter",
                "calculation enter",
                "execute",
                "calculation exit",
                "as_of exit",
            ],
        )

    def test_view_does_not_execute_invalid_as_of_or_enter_plain_query(self) -> None:
        view = self._view()
        request = SimpleNamespace(GET={}, method="POST")
        with (
            patch("general_manager.api.graphql_view.as_of") as as_of_mock,
            patch.object(view, "execute_graphql_request") as execute,
        ):
            view.get_response(request, {"query": "query Q @asOf { ping }"})
            execute.assert_not_called()
            as_of_mock.assert_not_called()

        with patch("general_manager.api.graphql_view.as_of") as as_of_mock:
            view.get_response(request, {"query": "query Q { ping }"})
            as_of_mock.assert_not_called()

    def test_view_rejects_directive_on_mutation_and_subscription_before_execution(
        self,
    ) -> None:
        view = self._view()
        request = SimpleNamespace(GET={}, method="POST")
        for operation in ("mutation", "subscription"):
            with (
                self.subTest(operation=operation),
                patch.object(view, "execute_graphql_request") as execute,
            ):
                payload, status = view.get_response(
                    request,
                    {"query": (f'{operation} Q @asOf(date: "2022-01-01") {{ ping }}')},
                )
            self.assertEqual(status, 400)
            self.assertEqual(
                payload["errors"][0]["extensions"]["code"],
                "GRAPHQL_VALIDATION_FAILED",
            )
            execute.assert_not_called()

    def test_batch_items_are_isolated_and_cleanup_after_resolver_error(self) -> None:
        view = self._view()
        request = SimpleNamespace(GET={}, method="POST")
        seen: list[datetime | None] = []
        call_count = 0

        def execute(*_args: object) -> object:
            nonlocal call_count
            call_count += 1
            seen.append(current_as_of_date())
            if call_count == 2:
                return type(
                    "Result",
                    (),
                    {
                        "data": {"ping": None},
                        "errors": [GraphQLError("resolver failed", path=["ping"])],
                    },
                )()
            return type("Result", (), {"data": {"ping": "pong"}, "errors": None})()

        with patch.object(view, "execute_graphql_request", execute):
            for date_value in ("2022-01-01T00:00:00Z", "2023-01-01T00:00:00Z"):
                view.get_response(
                    request,
                    {
                        "query": (
                            "query Q($date: DateTime!) @asOf(date: $date) { ping }"
                        ),
                        "variables": {"date": date_value},
                    },
                )
                self.assertIsNone(current_as_of_date())

        self.assertEqual(
            seen,
            [
                datetime(2022, 1, 1, tzinfo=UTC),
                datetime(2023, 1, 1, tzinfo=UTC),
            ],
        )

    def test_context_conflict_is_a_public_error_before_execution(self) -> None:
        view = self._view()
        request = SimpleNamespace(GET={}, method="POST")
        with (
            as_of("2021-01-01"),
            patch.object(view, "execute_graphql_request") as execute,
        ):
            payload, status = view.get_response(
                request,
                {"query": 'query Q @asOf(date: "2022-01-01") { ping }'},
            )

        self.assertEqual(status, 400)
        self.assertEqual(
            payload["errors"][0]["extensions"]["code"],
            "HISTORICAL_CONTEXT_CONFLICT",
        )
        execute.assert_not_called()

    def test_concurrent_requests_keep_independent_dates(self) -> None:
        view = self._view()
        barrier = Barrier(2)

        def execute(*_args: object) -> object:
            barrier.wait()
            active = current_as_of_date()
            return type("Result", (), {"data": {"active": active}, "errors": None})()

        def run(date_value: str) -> datetime | None:
            payload, _status = view.get_response(
                SimpleNamespace(GET={}, method="POST"),
                {
                    "query": "query Q($date: DateTime!) @asOf(date: $date) { ping }",
                    "variables": {"date": date_value},
                },
            )
            return payload["data"]["active"]

        with (
            patch.object(view, "execute_graphql_request", execute),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            results = list(
                executor.map(
                    run,
                    ["2022-01-01T00:00:00Z", "2023-01-01T00:00:00Z"],
                )
            )

        self.assertCountEqual(
            results,
            [
                datetime(2022, 1, 1, tzinfo=UTC),
                datetime(2023, 1, 1, tzinfo=UTC),
            ],
        )
        self.assertIsNone(current_as_of_date())

    def test_context_is_restored_before_response_formatting_failure(self) -> None:
        view = self._view()
        execution_result = type(
            "Result", (), {"data": {"ping": "pong"}, "errors": None}
        )()
        with (
            patch.object(
                view, "execute_graphql_request", return_value=execution_result
            ),
            patch.object(
                view, "json_encode", side_effect=RuntimeError("encoding failed")
            ),
            self.assertRaisesRegex(RuntimeError, "encoding failed"),
        ):
            view.get_response(
                SimpleNamespace(GET={}, method="POST"),
                {"query": 'query Q @asOf(date: "2022-01-01") { ping }'},
            )

        self.assertIsNone(current_as_of_date())

    def test_async_execution_keeps_context_until_awaited_and_cleans_up(self) -> None:
        view = self._view()
        seen: list[datetime | None] = []

        async def execute(*_args: object) -> object:
            await asyncio.sleep(0)
            seen.append(current_as_of_date())
            return type("Result", (), {"data": {"ping": "pong"}, "errors": None})()

        with patch.object(view, "execute_graphql_request", execute):
            view.get_response(
                SimpleNamespace(GET={}, method="POST"),
                {"query": 'query Q @asOf(date: "2022-01-01T00:00:00Z") { ping }'},
            )

        self.assertEqual(seen, [datetime(2022, 1, 1, tzinfo=UTC)])
        self.assertIsNone(current_as_of_date())

    def test_syntax_fallback_awaits_async_execution_and_cleans_up(self) -> None:
        view = self._view()
        seen: list[datetime | None] = []

        async def execute(*_args: object) -> object:
            await asyncio.sleep(0)
            seen.append(current_as_of_date())
            return type("Result", (), {"data": None, "errors": [GraphQLError("bad")]})()

        with patch.object(view, "execute_graphql_request", execute):
            payload, status = view.get_response(
                SimpleNamespace(GET={}, method="POST"),
                {"query": "query"},
            )

        self.assertEqual(status, 400)
        self.assertEqual(payload["errors"][0]["message"], "bad")
        self.assertEqual(seen, [None])
        self.assertIsNone(current_as_of_date())

    def test_non_string_query_uses_controlled_http_error_response(self) -> None:
        view = self._view()
        del view.json_encode
        request = RequestFactory().post(
            "/graphql",
            data=json.dumps({"query": 123}),
            content_type="application/json",
        )

        response = view.dispatch(request)

        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.content)
        self.assertIn("errors", payload)

    def test_http_query_executes_resolver_inside_as_of_context(self) -> None:
        class Query(graphene.ObjectType):
            active = graphene.String()

            @staticmethod
            def resolve_active(_root: object, _info: object) -> str | None:
                current = current_as_of_date()
                return None if current is None else current.isoformat()

        schema = graphene.Schema(query=Query, types=(graphene.DateTime,))
        gm_bootstrap._attach_as_of_directive(schema)
        view = GeneralManagerGraphQLView(schema=schema)
        request = RequestFactory().post(
            "/graphql",
            data=json.dumps(
                {
                    "query": (
                        "query Q($date: DateTime!) @asOf(date: $date) { active }"
                    ),
                    "variables": {"date": "2022-01-01T00:00:00Z"},
                }
            ),
            content_type="application/json",
        )

        response = view.dispatch(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            json.loads(response.content)["data"]["active"],
            "2022-01-01T00:00:00+00:00",
        )
        self.assertIsNone(current_as_of_date())

    def test_async_mutation_is_rejected_instead_of_escaping_atomic_scope(self) -> None:
        events: list[str] = []

        class AsyncMutation(graphene.Mutation):
            Output = graphene.String

            @staticmethod
            async def mutate(_root: object, _info: object) -> str:
                events.append("mutation body")
                return "changed"

        class Mutation(graphene.ObjectType):
            async_mutation = AsyncMutation.Field()

        class Query(graphene.ObjectType):
            ping = graphene.String()

        view = GeneralManagerGraphQLView(
            schema=graphene.Schema(query=Query, mutation=Mutation)
        )
        request = SimpleNamespace(GET={}, method="POST")
        view.json_encode = lambda _request, payload, **_kwargs: payload

        with (
            patch("graphene_django.views.graphene_settings.ATOMIC_MUTATIONS", True),
            patch("graphene_django.views.transaction.atomic") as atomic_mock,
        ):
            payload, status = view.get_response(
                request,
                {"query": "mutation Q { asyncMutation }"},
            )

        self.assertEqual(status, 400)
        self.assertEqual(
            payload["errors"][0]["extensions"]["code"],
            "GRAPHQL_VALIDATION_FAILED",
        )
        self.assertEqual(events, [])
        atomic_mock.assert_not_called()

    def test_nested_mutation_awaitables_are_closed_without_running_them(self) -> None:
        events: list[str] = []

        class ClosableAwaitable:
            def __init__(self) -> None:
                self.closed = False

            def __await__(self):
                yield

            def close(self) -> None:
                self.closed = True

        class CancellableAwaitable:
            def __init__(self) -> None:
                self.cancelled = False

            def __await__(self):
                yield

            def cancel(self) -> None:
                self.cancelled = True

        async def nested_coroutine() -> None:
            events.append("nested body")

        async def outer_coroutine(payload: object) -> None:
            events.append(f"outer body: {payload!r}")

        closable = ClosableAwaitable()
        cancellable = CancellableAwaitable()
        nested = nested_coroutine()
        payload: dict[str, object] = {
            "nested": [nested, (closable,), {cancellable}],
        }
        payload["cycle"] = payload
        outer = outer_coroutine(payload)

        _close_awaitable(outer)

        self.assertEqual(events, [])
        self.assertIsNone(outer.cr_frame)
        self.assertIsNone(nested.cr_frame)
        self.assertTrue(closable.closed)
        self.assertTrue(cancellable.cancelled)

    def test_declared_async_mutation_is_found_through_fragment_graph(self) -> None:
        async def resolve_async(_root: object, _info: object) -> str:
            return "changed"

        query_type = GraphQLObjectType(
            "Query",
            {"ping": GraphQLField(GraphQLString)},
        )
        mutation_type = GraphQLObjectType(
            "Mutation",
            {
                "asyncField": GraphQLField(
                    GraphQLString,
                    resolve=resolve_async,
                )
            },
        )
        schema = GraphQLSchema(query=query_type, mutation=mutation_type)
        valid_query = """
            mutation Q {
                ... on Mutation { ...AsyncPart }
            }
            fragment AsyncPart on Mutation {
                asyncField
            }
        """
        invalid_query = """
            mutation Q { ...Loop }
            fragment Loop on Mutation { asyncField ...Loop }
        """

        self.assertTrue(
            _has_declared_async_mutation_resolver(schema, valid_query, "Q", {})
        )
        self.assertFalse(
            _has_declared_async_mutation_resolver(schema, invalid_query, "Q", {})
        )
        self.assertFalse(
            _has_declared_async_mutation_resolver(schema, "mutation {", None, {})
        )

    def test_async_mutation_preflight_defers_standard_validation_errors(self) -> None:
        events: list[str] = []

        class AsyncMutation(graphene.Mutation):
            Output = graphene.String

            @staticmethod
            async def mutate(_root: object, _info: object) -> str:
                events.append("mutation body")
                return "changed"

        class Mutation(graphene.ObjectType):
            async_mutation = AsyncMutation.Field()

        class Query(graphene.ObjectType):
            ping = graphene.String()

        view = GeneralManagerGraphQLView(
            schema=graphene.Schema(query=Query, mutation=Mutation)
        )
        view.json_encode = lambda _request, payload, **_kwargs: payload
        cases = (
            ("mutation Q { asyncMutation unknownField }", "Cannot query field"),
            ("mutation Q { asyncMutation ...Missing }", "Unknown fragment"),
            (
                "mutation Q { ...Loop } "
                "fragment Loop on Mutation { asyncMutation ...Loop }",
                "Cannot spread fragment",
            ),
            (
                "mutation Q($run: Boolean!, $run: Boolean!) { "
                "asyncMutation @include(if: $run) }",
                "There can be only one variable named",
            ),
        )

        for query, expected_message in cases:
            with self.subTest(query=query):
                payload, status = view.get_response(
                    SimpleNamespace(GET={}, method="POST"),
                    {"query": query, "variables": {"run": True}},
                )
            self.assertEqual(status, 400)
            self.assertIn(expected_message, payload["errors"][0]["message"])
            self.assertNotEqual(
                payload["errors"][0].get("extensions", {}).get("code"),
                "GRAPHQL_VALIDATION_FAILED",
            )
        self.assertEqual(events, [])

    def test_async_mutation_preflight_defers_custom_validation_rules(self) -> None:
        events: list[str] = []

        class RejectMutationRule(ASTValidationRule):
            def enter_operation_definition(self, node: object, *_args: object) -> None:
                self.report_error(GraphQLError("custom validation rejection", [node]))

        class AsyncMutation(graphene.Mutation):
            Output = graphene.String

            @staticmethod
            async def mutate(_root: object, _info: object) -> str:
                events.append("mutation body")
                return "changed"

        class Mutation(graphene.ObjectType):
            async_mutation = AsyncMutation.Field()

        class Query(graphene.ObjectType):
            ping = graphene.String()

        view = GeneralManagerGraphQLView(
            schema=graphene.Schema(query=Query, mutation=Mutation),
            validation_rules=(RejectMutationRule,),
        )
        view.json_encode = lambda _request, payload, **_kwargs: payload

        payload, status = view.get_response(
            SimpleNamespace(GET={}, method="POST"),
            {"query": "mutation Q { asyncMutation }"},
        )

        self.assertEqual(status, 400)
        self.assertEqual(payload["errors"][0]["message"], "custom validation rejection")
        self.assertNotEqual(
            payload["errors"][0].get("extensions", {}).get("code"),
            "GRAPHQL_VALIDATION_FAILED",
        )
        self.assertEqual(events, [])

    def test_non_mapping_mutation_variables_use_normal_graphql_error(self) -> None:
        class AsyncMutation(graphene.Mutation):
            Output = graphene.String

            @staticmethod
            async def mutate(_root: object, _info: object) -> str:
                return "changed"

        class Mutation(graphene.ObjectType):
            async_mutation = AsyncMutation.Field()

        class Query(graphene.ObjectType):
            ping = graphene.String()

        view = GeneralManagerGraphQLView(
            schema=graphene.Schema(query=Query, mutation=Mutation)
        )
        view.json_encode = lambda _request, payload, **_kwargs: payload
        query = "mutation Q($run: Boolean! = true) { asyncMutation @include(if: $run) }"

        payload, status = view.get_response(
            SimpleNamespace(GET={}, method="POST"),
            {"query": query, "variables": []},
        )

        self.assertEqual(status, 400)
        self.assertNotEqual(
            payload["errors"][0].get("extensions", {}).get("code"),
            "GRAPHQL_VALIDATION_FAILED",
        )
        self.assertFalse(
            _has_declared_async_mutation_resolver(
                view.schema.graphql_schema,
                query,
                "Q",
                [],
            )
        )

    def test_async_mutation_preflight_honors_field_and_fragment_inclusion(self) -> None:
        async def resolve_async(_root: object, _info: object) -> str:
            return "changed"

        schema = GraphQLSchema(
            query=GraphQLObjectType(
                "Query",
                {"ping": GraphQLField(GraphQLString)},
            ),
            mutation=GraphQLObjectType(
                "Mutation",
                {"asyncField": GraphQLField(GraphQLString, resolve=resolve_async)},
            ),
        )
        cases = (
            (
                "mutation Q { alias: asyncField @skip(if: true) }",
                {},
                False,
            ),
            (
                "mutation Q($run: Boolean!) { asyncField @include(if: $run) }",
                {"run": False},
                False,
            ),
            (
                "mutation Q($run: Boolean!) { asyncField @include(if: $run) }",
                {"run": True},
                True,
            ),
            (
                "mutation Q { ... on Mutation @skip(if: true) { asyncField } }",
                {},
                False,
            ),
            (
                (
                    "mutation Q { ...AsyncPart @include(if: false) } "
                    "fragment AsyncPart on Mutation { asyncField }"
                ),
                {},
                False,
            ),
        )

        for query, variables, expected in cases:
            with self.subTest(query=query):
                self.assertEqual(
                    _has_declared_async_mutation_resolver(
                        schema,
                        query,
                        "Q",
                        variables,
                    ),
                    expected,
                )

    def test_http_async_mutation_inclusion_matches_graphql_execution(self) -> None:
        events: list[str] = []

        class AsyncMutation(graphene.Mutation):
            Output = graphene.String

            @staticmethod
            async def mutate(_root: object, _info: object) -> str:
                events.append("mutation body")
                return "changed"

        class Mutation(graphene.ObjectType):
            async_mutation = AsyncMutation.Field()

        class Query(graphene.ObjectType):
            ping = graphene.String()

        view = GeneralManagerGraphQLView(
            schema=graphene.Schema(query=Query, mutation=Mutation)
        )
        view.json_encode = lambda _request, payload, **_kwargs: payload
        cases = (
            (
                "mutation Q { alias: asyncMutation @skip(if: true) }",
                {},
                200,
            ),
            (
                "mutation Q($run: Boolean!) { asyncMutation @include(if: $run) }",
                {"run": False},
                200,
            ),
            (
                "mutation Q($run: Boolean!) { asyncMutation @include(if: $run) }",
                {"run": True},
                400,
            ),
            (
                ("mutation Q { ... on Mutation @skip(if: true) { asyncMutation } }"),
                {},
                200,
            ),
            (
                (
                    "mutation Q { ...AsyncPart @include(if: false) } "
                    "fragment AsyncPart on Mutation { asyncMutation }"
                ),
                {},
                200,
            ),
        )

        for query, variables, expected_status in cases:
            with self.subTest(query=query, variables=variables):
                payload, status = view.get_response(
                    SimpleNamespace(GET={}, method="POST"),
                    {"query": query, "variables": variables},
                )
            self.assertEqual(status, expected_status)
            if expected_status == 200:
                self.assertEqual(payload["data"], {})
            else:
                self.assertEqual(
                    payload["errors"][0]["extensions"]["code"],
                    "GRAPHQL_VALIDATION_FAILED",
                )
        self.assertEqual(events, [])

    def test_invalid_inclusion_variables_remain_normal_graphql_errors(self) -> None:
        class AsyncMutation(graphene.Mutation):
            Output = graphene.String

            @staticmethod
            async def mutate(_root: object, _info: object) -> str:
                return "changed"

        class Mutation(graphene.ObjectType):
            async_mutation = AsyncMutation.Field()

        class Query(graphene.ObjectType):
            ping = graphene.String()

        view = GeneralManagerGraphQLView(
            schema=graphene.Schema(query=Query, mutation=Mutation)
        )
        view.json_encode = lambda _request, payload, **_kwargs: payload
        cases = (
            (
                "mutation Q($run: Boolean!) { asyncMutation @include(if: $run) }",
                {},
            ),
            (
                "mutation Q($run: Boolean!) { ... on Mutation "
                "@include(if: $run) { asyncMutation } }",
                {"run": "invalid"},
            ),
            (
                "mutation Q($run: Boolean!) { ...AsyncPart @include(if: $run) } "
                "fragment AsyncPart on Mutation { asyncMutation }",
                {},
            ),
        )

        for query, variables in cases:
            with self.subTest(query=query, variables=variables):
                payload, status = view.get_response(
                    SimpleNamespace(GET={}, method="POST"),
                    {"query": query, "variables": variables},
                )
            self.assertEqual(status, 400)
            error = payload["errors"][0]
            self.assertIn("$run", error["message"])
            self.assertNotEqual(
                error.get("extensions", {}).get("code"),
                "GRAPHQL_VALIDATION_FAILED",
            )

    def test_dynamic_atomic_mutation_awaitable_is_closed_and_rolled_back(self) -> None:
        events: list[str] = []

        async def dynamic_result() -> object:
            events.append("mutation body")
            return SimpleNamespace(data="changed", errors=None)

        view = self._view()
        result = dynamic_result()
        atomic = MagicMock()
        rollback = MagicMock()

        with (
            patch.object(view, "execute_graphql_request", return_value=result),
            patch(
                "general_manager.api.graphql_view.graphene_settings.ATOMIC_MUTATIONS",
                True,
            ),
            patch("general_manager.api.graphql_view.transaction.atomic", atomic),
            patch(
                "general_manager.api.graphql_view.transaction.set_rollback",
                rollback,
            ),
        ):
            completed = view._execute_and_complete(
                SimpleNamespace(GET={}, method="POST"),
                {"query": "mutation Q { ping }"},
                "mutation Q { ping }",
                None,
                "Q",
                False,
            )

        self.assertEqual(events, [])
        self.assertIsNone(result.cr_frame)
        self.assertEqual(
            completed.errors[0].extensions["code"],
            "GRAPHQL_VALIDATION_FAILED",
        )
        atomic.assert_called_once_with(using="default", savepoint=False)
        rollback.assert_called_once_with(True, using="default")

    def test_generated_mutation_preserves_historical_forbidden_error(self) -> None:
        class DummyManager:
            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @classmethod
                def get_attribute_types(cls):
                    return {}

            @classmethod
            def create(cls, **_kwargs: object) -> object:
                raise HistoricalMutationError()

        mutation_class = GraphQL.generate_create_mutation_class(
            DummyManager,
            {"success": graphene.Boolean()},
        )
        assert mutation_class is not None
        info = MagicMock()
        info.context.user = AnonymousUser()

        with self.assertRaises(GraphQLError) as raised:
            mutation_class.mutate(None, info)

        self.assertEqual(
            raised.exception.extensions["code"],
            "HISTORICAL_MUTATION_FORBIDDEN",
        )

    def test_historical_resolver_error_has_public_code_path_and_location(self) -> None:
        view = self._view()
        source = "query Q { ping }"
        node = next(iter(parse(source).definitions)).selection_set.selections[0]
        error = GraphQLError(
            "wrapped",
            nodes=[node],
            path=["ping"],
            original_error=HistoricalMutationError(),
        )

        formatted = view.format_error(error)

        self.assertEqual(
            formatted["message"], "Mutations are not allowed in historical context."
        )
        self.assertEqual(
            formatted["extensions"]["code"], "HISTORICAL_MUTATION_FORBIDDEN"
        )
        self.assertEqual(formatted["path"], ["ping"])
        self.assertEqual(formatted["locations"], [{"line": 1, "column": 11}])

    def test_all_historical_resolver_errors_have_stable_public_codes(self) -> None:
        view = self._view()
        cases = (
            (InvalidSearchDateError("bad"), "BAD_USER_INPUT"),
            (
                HistoricalContextConflictError(),
                "HISTORICAL_CONTEXT_CONFLICT",
            ),
            (HistoricalMutationError(), "HISTORICAL_MUTATION_FORBIDDEN"),
            (
                HistoricalReadNotSupportedError(),
                "HISTORICAL_READ_NOT_SUPPORTED",
            ),
        )

        for original_error, code in cases:
            with self.subTest(code=code):
                formatted = view.format_error(
                    GraphQLError("wrapped", original_error=original_error)
                )
            self.assertEqual(formatted["extensions"]["code"], code)

    def test_invalid_search_date_mapping_uses_fixed_bounded_message(self) -> None:
        invalid = InvalidSearchDateError("secret-" * 10_000)

        formatted = self._view().format_error(
            GraphQLError("wrapped", original_error=invalid)
        )

        self.assertEqual(formatted["message"], "Invalid historical search date.")
        self.assertNotIn("secret", formatted["message"])

    @staticmethod
    def _view() -> GeneralManagerGraphQLView:
        class Query(graphene.ObjectType):
            ping = graphene.String()

            @staticmethod
            def resolve_ping(_root: object, _info: object) -> str:
                return "pong"

        schema = graphene.Schema(query=Query, types=(graphene.DateTime,))
        gm_bootstrap._attach_as_of_directive(schema)
        view = GeneralManagerGraphQLView(schema=schema)
        view.json_encode = lambda _request, payload, **_kwargs: payload
        return view

    @staticmethod
    def _coercion_schema() -> GraphQLSchema:
        rejected_value_message = "custom DateTime rejected the value"
        string_required_message = "custom DateTime requires a string"

        def parse_value(value: object) -> datetime:
            if value == "epoch":
                return datetime(2000, 1, 1, tzinfo=UTC)
            raise GraphQLError(rejected_value_message)

        def parse_literal(node: object, _variables: object = None) -> datetime:
            if isinstance(node, StringValueNode):
                return parse_value(node.value)
            raise GraphQLError(string_required_message)

        date_time = GraphQLScalarType(
            "DateTime",
            serialize=lambda value: value,
            parse_value=parse_value,
            parse_literal=parse_literal,
        )
        query = GraphQLObjectType(
            "Query",
            {"ping": GraphQLField(GraphQLString)},
        )
        return GraphQLSchema(
            query=query,
            types=[date_time],
            directives=[*specified_directives, build_as_of_directive(date_time)],
        )


class GraphQLAtomicMutationTests(unittest.TestCase):
    def test_dynamic_rollback_and_sync_commit_in_isolated_database_probe(self) -> None:
        result = subprocess.run(  # noqa: S603 - trusted interpreter and test probe
            [sys.executable, "tests/utils/graphql_atomic_probe.py"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)


class GraphQLPropertyTests(TestCase):
    """Verify GraphQLProperty descriptor configuration."""

    def test_graphql_property_initialization(self):
        """GraphQLProperty requires an annotated resolver."""

        def mock_getter():
            """Unannotated resolver used to trigger validation."""
            return "test"

        with self.assertRaises(
            TypeError,
            msg="GraphQLProperty requires a return type hint for the property function.",
        ):
            GraphQLProperty(mock_getter)

    def test_graphql_property_with_type_hint(self):
        """GraphQLProperty stores the resolver return annotation."""

        def mock_getter() -> str:
            """Annotated resolver used to resolve the GraphQL type hint."""
            return "test"

        prop = GraphQLProperty(mock_getter)
        self.assertEqual(prop.graphql_type_hint, str)

    def test_graphql_property_reads_dynamic_annotation_from_original_resolver(self):
        """Generated relation annotations survive wrapper metadata changes."""

        def generated_relation(_instance):
            return None

        generated_relation.__annotations__ = {"return": GeneralManager}
        prop = GraphQLProperty(generated_relation)
        prop.fget.__annotations__ = {}

        self.assertIs(prop.graphql_type_hint, GeneralManager)

    def test_graphql_property_uses_cached_resolver_after_set_name(self):
        """Descriptor access should not redo cached resolver lookup after setup."""
        calls = []

        def mock_getter(instance) -> str:
            calls.append(instance)
            return "test"

        prop = GraphQLProperty(mock_getter)
        owner = type("Owner", (), {})
        instance = object()
        prop.__set_name__(owner, "value")

        with patch.object(
            prop,
            "_get_cached_fget",
            side_effect=AssertionError("cached resolver should be used directly"),
        ):
            self.assertEqual(prop.__get__(instance, owner), "test")

        self.assertEqual(calls, [instance])

    def test_graph_ql_property_direct_decorator_requires_return_annotation(self):
        """Public decorator validates direct usage at decoration time."""

        def mock_getter():
            """Unannotated resolver used to trigger decorator validation."""
            return "test"

        with self.assertRaises(GraphQLPropertyReturnAnnotationError):
            graph_ql_property(mock_getter)

    def test_graph_ql_property_configured_decorator_sets_metadata(self):
        """Configured public decorator stores GraphQL metadata on the descriptor."""

        def mock_getter() -> str:
            """Annotated resolver used by configured decorator validation."""
            return "test"

        prop = graph_ql_property(sortable=True, filterable=True)(mock_getter)

        self.assertIsInstance(prop, GraphQLProperty)
        self.assertTrue(prop.sortable)
        self.assertTrue(prop.filterable)
        self.assertEqual(prop.cache, "run")

    def test_graph_ql_property_configured_decorator_defers_validation_until_applied(
        self,
    ):
        """Configured decorators validate options when wrapping a function."""
        decorator = graph_ql_property(cache="timeout")

        def mock_getter() -> str:
            """Annotated resolver used to trigger timeout validation."""
            return "test"

        with self.assertRaises(GraphQLPropertyTimeoutConfigurationError):
            decorator(mock_getter)

    def test_graph_ql_property_validation_precedence(self):
        """Warm-up validation runs before timeout/cache decorator validation."""

        def mock_getter() -> str:
            """Annotated resolver used for validation precedence."""
            return "test"

        with self.assertRaises(GraphQLPropertyWarmUpConfigurationError):
            GraphQLProperty(mock_getter, cache="invalid", warm_up=True)
        with self.assertRaises(GraphQLPropertyTimeoutConfigurationError):
            GraphQLProperty(mock_getter, cache="invalid", timeout=1)

    def test_public_graphql_property_errors_are_importable_from_api_module(self):
        """Documented GraphQL property errors are part of the API module."""
        from general_manager import api

        self.assertIs(
            api.GraphQLPropertyReturnAnnotationError,
            GraphQLPropertyReturnAnnotationError,
        )
        self.assertIs(
            api.GraphQLPropertyTimeoutConfigurationError,
            GraphQLPropertyTimeoutConfigurationError,
        )
        self.assertIs(
            api.GraphQLPropertyWarmUpConfigurationError,
            GraphQLPropertyWarmUpConfigurationError,
        )

    def test_public_graphql_error_is_importable_from_api_module(self):
        """PublicGraphQLError is part of the stable API module."""
        import general_manager._types.api as type_api
        from general_manager._types.api import (
            PublicGraphQLError as typed_public_error,
        )
        from general_manager.api import PublicGraphQLError as public_error
        from general_manager.api.graphql_errors import PublicGraphQLError

        self.assertIs(public_error, PublicGraphQLError)
        self.assertIs(typed_public_error, PublicGraphQLError)
        self.assertIn("PublicGraphQLError", type_api.__all__)

    def test_graphql_property_cache_options_exclude_auto(self):
        """GraphQL property cache scopes expose only user-selectable values."""
        self.assertEqual(
            set(get_args(GraphQLPropertyCache)),
            {"dependency", "run", "timeout", "none"},
        )
        self.assertEqual(
            signature(graph_ql_property).parameters["cache"].default, "run"
        )

    def test_graphql_property_rejects_warm_up_for_run_cache(self):
        """Warm-up is rejected for request-run cache scope."""

        def getter() -> int:
            """Return a value for warm-up validation."""
            return 1

        with self.assertRaisesRegex(ValueError, "warm_up=True requires"):
            GraphQLProperty(getter, cache="run", warm_up=True)

    def test_graphql_property_rejects_warm_up_for_none_cache(self):
        """Warm-up is rejected when caching is disabled."""

        def getter() -> int:
            """Return a value for warm-up validation."""
            return 1

        with self.assertRaisesRegex(ValueError, "warm_up=True requires"):
            GraphQLProperty(getter, cache="none", warm_up=True)

    def test_graphql_property_requires_timeout_for_timeout_cache(self):
        """Timeout cache declarations must include a timeout value."""

        def getter() -> int:
            """Return a value for timeout validation."""
            return 1

        with self.assertRaisesRegex(ValueError, 'cache="timeout" requires timeout'):
            GraphQLProperty(getter, cache="timeout")

    def test_graphql_property_rejects_timeout_for_dependency_cache(self):
        """Non-timeout cache declarations reject timeout values."""

        def getter() -> int:
            """Return a value for timeout validation."""
            return 1

        with self.assertRaisesRegex(ValueError, "timeout is only supported"):
            GraphQLProperty(getter, cache="dependency", timeout=60)

    def test_graphql_property_accepts_warm_up_for_dependency_and_timeout(self):
        """Warm-up is accepted for dependency and timeout cache scopes."""

        def getter() -> int:
            """Return a value for accepted warm-up declarations."""
            return 1

        dependency_prop = GraphQLProperty(getter, cache="dependency", warm_up=True)
        timeout_prop = GraphQLProperty(
            getter,
            cache="timeout",
            timeout=60,
            warm_up=True,
        )

        self.assertTrue(dependency_prop.warm_up)
        self.assertEqual(dependency_prop.cache, "dependency")
        self.assertIsNone(dependency_prop.timeout)
        self.assertTrue(timeout_prop.warm_up)
        self.assertEqual(timeout_prop.cache, "timeout")
        self.assertEqual(timeout_prop.timeout, 60)


class MeasurementTypeTests(TestCase):
    def test_measurement_type_fields(self):
        for field in ["value", "unit"]:
            self.assertTrue(hasattr(MeasurementType, field))


class GraphQLTests(TestCase):
    def test_public_bulk_data_change_notifications_is_importable(self):
        """Bulk notification batching is exposed only through the API module."""
        import general_manager
        import general_manager._types.api as type_api
        from general_manager._types.api import (
            bulk_data_change_notifications as typed_bulk_notifications,
        )
        from general_manager.api import (
            bulk_data_change_notifications as public_bulk_notifications,
        )
        from general_manager.api.notification_batching import (
            bulk_data_change_notifications,
        )

        self.assertIs(public_bulk_notifications, bulk_data_change_notifications)
        self.assertIs(typed_bulk_notifications, bulk_data_change_notifications)
        self.assertIn("bulk_data_change_notifications", type_api.__all__)
        self.assertNotIn("bulk_data_change_notifications", general_manager.__all__)

    def setUp(self):
        self.general_manager_class = MagicMock(spec=GeneralManagerMeta)
        self.general_manager_class.__name__ = "TestManager"
        self.info = MagicMock()
        self.info.context.user = AnonymousUser()

    @patch("general_manager.interface.base_interface.InterfaceBase")
    def test_create_graphql_interface_no_interface(self, _mock_interface):
        self.general_manager_class.Interface = None
        result = GraphQL.create_graphql_interface(self.general_manager_class)
        self.assertIsNone(result)

    @patch("general_manager.interface.base_interface.InterfaceBase")
    def test_create_graphql_interface_with_interface(self, mock_interface):
        mock_interface.get_attribute_types.return_value = {"test_field": {"type": str}}
        self.general_manager_class.Interface = mock_interface
        with patch("general_manager.api.graphql.issubclass", return_value=True):
            GraphQL.create_graphql_interface(self.general_manager_class)
            self.assertIn("TestManager", GraphQL.graphql_type_registry)

    def test_map_field_to_graphene(self):
        # Base types
        self.assertIsInstance(
            GraphQL._map_field_to_graphene_read(str, "name"), graphene.String
        )
        self.assertIsInstance(
            GraphQL._map_field_to_graphene_read(int, "age"), graphene.Int
        )
        self.assertIsInstance(
            GraphQL._map_field_to_graphene_read(float, "value"), graphene.Float
        )
        self.assertIsInstance(
            GraphQL._map_field_to_graphene_read(Decimal, "decimal"), graphene.Float
        )
        self.assertIsInstance(
            GraphQL._map_field_to_graphene_read(bool, "active"), graphene.Boolean
        )
        self.assertIsInstance(
            GraphQL._map_field_to_graphene_read(date, "birth_date"), graphene.Date
        )
        field = GraphQL._map_field_to_graphene_read(Measurement, "measurement")
        self.assertIsInstance(field, graphene.Field)

    def test_map_bigint_field_to_graphene(self):
        field = GraphQL._map_field_to_graphene_read(
            int, "large_value", {"graphql_scalar": "bigint"}
        )
        self.assertIsInstance(field, BigIntScalar)

    def test_normalize_mutation_kwargs_does_not_rewrite_plain_list_field(self):
        class DummyInterface:
            @staticmethod
            def get_attribute_types():
                return {
                    "watch_list": {
                        "type": str,
                        "is_required": False,
                        "is_derived": False,
                        "default": None,
                        "is_editable": True,
                    }
                }

        class DummyManager:
            Interface = DummyInterface

        normalized = _normalize_mutation_kwargs_for_manager(
            DummyManager, {"watch_list": "daily"}
        )

        self.assertEqual(normalized, {"watch_list": "daily"})

    def test_map_field_to_graphene_handles_generic_alias_type(self):
        field = GraphQL._map_field_to_graphene_read(list[str], "labels")
        self.assertIsInstance(field, graphene.String)

    def test_map_field_to_graphene_resolves_manager_relations(self):
        class RelatedManager(GeneralManager):
            pass

        class RelatedManagerType(graphene.ObjectType):
            name = graphene.String()

        class RelatedModel:
            _general_manager_class = RelatedManager

        GraphQL.manager_registry["RelatedManager"] = RelatedManager
        GraphQL.graphql_type_registry["RelatedManager"] = RelatedManagerType

        with (
            patch.object(GraphQL, "_create_filter_options", return_value=None),
            patch.object(GraphQL, "_sort_by_options", return_value=None),
        ):
            for declared_type in (
                list[RelatedManager],
                Bucket[RelatedManager],
                RelatedModel,
                "RelatedManager",
                "Bucket[RelatedManager]",
            ):
                field = GraphQL._map_field_to_graphene_read(
                    declared_type,
                    "related_manager_list",
                    {"relation_kind": "collection"},
                )
                self.assertIsInstance(field, graphene.Field)
                self.assertEqual(
                    field.type._meta.fields["items"].type.of_type.of_type,
                    RelatedManagerType,
                )

        direct_field = GraphQL._map_field_to_graphene_read(
            RelatedManager | None,
            "related_manager",
            {"relation_kind": "direct"},
        )
        self.assertIsInstance(direct_field, graphene.Field)
        self.assertIs(direct_field.type, RelatedManagerType)

    def test_map_field_to_graphene_handles_any_type(self):
        field = GraphQL._map_field_to_graphene_read(Any, "metadata")
        self.assertIsInstance(field, graphene.String)

    def test_create_resolver_normal_case(self):
        mock_instance = MagicMock()
        mock_instance.some_field = "expected_value"
        resolver = GraphQL._create_resolver("some_field", str)
        self.assertEqual(resolver(mock_instance, self.info), "expected_value")

    def test_create_resolver_handles_generic_alias_type(self):
        mock_instance = MagicMock()
        mock_instance.labels = ["a", "b"]
        resolver = GraphQL._create_resolver("labels", list[str])
        self.assertEqual(resolver(mock_instance, self.info), ["a", "b"])

    def test_create_resolver_resolves_named_manager_relation(self):
        class RelatedManager(GeneralManager):
            pass

        previous_registry = GraphQL.manager_registry
        GraphQL.manager_registry = {"RelatedManager": RelatedManager}
        self.addCleanup(setattr, GraphQL, "manager_registry", previous_registry)
        sentinel = object()

        with patch(
            "general_manager.api.graphql_resolvers.create_list_resolver",
            return_value=sentinel,
        ) as create_list_resolver:
            resolver = GraphQL._create_resolver(
                "related_manager_list",
                "RelatedManager",
            )

        self.assertIs(resolver, sentinel)
        self.assertIs(create_list_resolver.call_args.args[1], RelatedManager)

    def test_create_resolver_handles_any_type(self):
        mock_instance = MagicMock()
        mock_instance.metadata = {"a": 1}
        resolver = GraphQL._create_resolver("metadata", Any)
        self.assertEqual(resolver(mock_instance, self.info), {"a": 1})

    def test_create_resolver_measurement_case(self):
        mock_instance = MagicMock()
        mock_measurement = Measurement(100, "cm")
        mock_instance.measurement_field = mock_measurement

        resolver = GraphQL._create_resolver("measurement_field", Measurement)
        result = resolver(mock_instance, self.info, target_unit="cm")
        self.assertEqual(result, {"value": Decimal(100), "unit": "centimeter"})

    def test_create_resolver_measurement_count_uses_public_unit(self):
        mock_instance = MagicMock()
        mock_instance.measurement_field = Measurement(Decimal("1"), "count")

        resolver = GraphQL._create_resolver("measurement_field", Measurement)

        self.assertEqual(
            resolver(mock_instance, self.info),
            {"value": Decimal("1"), "unit": "count"},
        )
        self.assertEqual(
            resolver(mock_instance, self.info, target_unit="count"),
            {"value": Decimal("1"), "unit": "count"},
        )

    def test_create_resolver_list_case(self):
        mock_instance = MagicMock()
        mock_queryset = MagicMock()
        mock_filtered = MagicMock()
        mock_queryset.filter.return_value = mock_filtered
        mock_filtered.exclude.return_value = mock_filtered
        # Assign the queryset directly
        mock_instance.abc_list = mock_queryset

        resolver = GraphQL._create_resolver("abc_list", GeneralManager)
        with (
            patch("json.loads", side_effect=json.loads),
            patch(
                "general_manager.api.graphql_resolvers.get_read_permission_filter",
                return_value=ReadPermissionPlan(
                    filters=[],
                    requires_instance_check=False,
                ),
            ),
        ):
            resolver(
                mock_instance,
                self.info,
                filter=json.dumps({"field": "value"}),
                exclude=json.dumps({"other_field": "value"}),
            )
            mock_queryset.filter.assert_called_with(field="value")
            mock_filtered.exclude.assert_called_with(other_field="value")

    @patch("general_manager.interface.base_interface.InterfaceBase")
    def test_create_graphql_interface_graphql_property(self, mock_interface):
        """
        Test that a GraphQL interface is created and registered when a manager class defines a GraphQLProperty attribute.
        """

        class TestManager:
            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @staticmethod
                def get_attribute_types():
                    return {"test_field": {"type": str}}

            @classmethod
            def all(cls):
                return []

        def prop_func() -> int:
            return 42

        mock_interface.get_attribute_types.return_value = {"test_field": {"type": str}}
        with patch("general_manager.api.graphql.issubclass", return_value=True):
            TestManager.test_prop = GraphQLProperty(prop_func)
            GraphQL.create_graphql_interface(TestManager)
            self.assertIn("TestManager", GraphQL.graphql_type_registry)

    def test_list_resolver_with_invalid_filter_exclude(self):
        """
        Test that the list resolver returns the original queryset when filter or exclude arguments are invalid JSON.

        If JSON decoding fails for the filter or exclude parameters, ensures the resolver returns the unfiltered queryset under the "items" key.
        """
        mock_instance = MagicMock()
        mock_qs = MagicMock()
        mock_instance.abc_list = mock_qs
        resolver = GraphQL._create_resolver("abc_list", GeneralManager)
        with (
            patch("json.loads", side_effect=ValueError),
            patch(
                "general_manager.api.graphql_resolvers.get_read_permission_filter",
                return_value=ReadPermissionPlan(
                    filters=[],
                    requires_instance_check=False,
                ),
            ),
        ):
            result = resolver(mock_instance, self.info, filter="bad", exclude="bad")
            self.assertEqual(result["items"], mock_qs)

    def test_create_filter_options_includes_scalar_filter_variants(self):
        """
        Tests that filter options are generated for numeric, string, and measurement fields.
        """

        class RelatedManager(GeneralManager):
            pass

        class DummyManager:
            __name__ = "DummyManager"

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @staticmethod
                def get_attribute_types():
                    return {
                        "num_field": {"type": int},
                        "str_field": {"type": str},
                        "measurement_field": {"type": Measurement},
                        "gm_field": {"type": RelatedManager},
                    }

        GraphQL.graphql_filter_type_registry.clear()
        filter_cls = GraphQL._create_filter_options(DummyManager)
        fields = filter_cls._meta.fields
        for key in [
            "num_field",
            *[f"num_field__{opt}" for opt in ["exact", "gt", "gte", "lt", "lte"]],
        ]:
            self.assertIn(key, fields)
        for key in [
            "str_field",
            *[
                f"str_field__{opt}"
                for opt in [
                    "exact",
                    "icontains",
                    "contains",
                    "in",
                    "startswith",
                    "endswith",
                ]
            ],
        ]:
            self.assertIn(key, fields)

    def test_create_filter_options_registry_cache(self):
        """
        Test that repeated calls to `_create_filter_options` with the same manager class and name return the same cached filter input type instance.

        Ensures the filter options registry caches and reuses filter input types for identical manager class and name combinations.
        """

        class DummyManager2:
            __name__ = "DummyManager2"

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @staticmethod
                def get_attribute_types():
                    return {"num_field": {"type": int}}

        GraphQL.graphql_filter_type_registry.clear()
        first = GraphQL._create_filter_options(DummyManager2)
        second = GraphQL._create_filter_options(DummyManager2)
        self.assertIs(first, second)

    def test_create_filter_options_exposes_direct_relation_filter(self):
        class RelatedManager:
            __name__ = "RelatedManager"

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @staticmethod
                def get_attribute_types():
                    return {"id": {"type": int}, "name": {"type": str}}

        class DummyManager:
            __name__ = "DummyManagerWithRelation"

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @staticmethod
                def get_attribute_types():
                    return {
                        "id": {"type": int},
                        "related": {
                            "type": RelatedManager,
                            "relation_kind": "direct",
                        },
                    }

        GraphQL.graphql_filter_type_registry.clear()

        def relation_safe_issubclass(candidate, parent):
            if parent is GeneralManager and candidate is RelatedManager:
                return True
            return isinstance(candidate, type) and issubclass(candidate, parent)

        with patch(
            "general_manager.api.graphql_relations.safe_issubclass",
            side_effect=relation_safe_issubclass,
        ):
            filter_type = GraphQL._create_filter_options(DummyManager)

        self.assertIsNotNone(filter_type)
        self.assertIn("related", filter_type._meta.fields)
        related_type = filter_type._meta.fields["related"].type
        self.assertIn("id", related_type._meta.fields)
        self.assertIn("name__icontains", related_type._meta.fields)

    def test_create_filter_options_infers_direct_relation_for_manager_input(self):
        class RelatedManager:
            __name__ = "ManagerInputRelatedManager"

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @staticmethod
                def get_attribute_types():
                    return {"id": {"type": int}, "name": {"type": str}}

        class CalculationManager:
            __name__ = "ManagerInputCalculationManager"

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {
                    "project": Input(RelatedManager),
                }

                @staticmethod
                def get_attribute_types():
                    return {"project": {"type": RelatedManager}}

        GraphQL.graphql_filter_type_registry.clear()

        def relation_safe_issubclass(candidate, parent):
            if parent is GeneralManager and candidate is RelatedManager:
                return True
            return isinstance(candidate, type) and issubclass(candidate, parent)

        with patch(
            "general_manager.api.graphql_relations.safe_issubclass",
            side_effect=relation_safe_issubclass,
        ):
            filter_type = GraphQL._create_filter_options(CalculationManager)

        self.assertIsNotNone(filter_type)
        self.assertIn("project", filter_type._meta.fields)
        self.assertNotIn("project__id", filter_type._meta.fields)
        project_type = filter_type._meta.fields["project"].type
        self.assertIn("id", project_type._meta.fields)

    def test_manager_input_explicit_relation_metadata_remains_authoritative(self):
        class RelatedManager:
            __name__ = "ExplicitManagerInputRelatedManager"

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {"id": Input(int)}

                @staticmethod
                def get_attribute_types():
                    return {"id": {"type": int}}

        class CalculationManager:
            __name__ = "ExplicitManagerInputCalculationManager"

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {
                    "project": Input(RelatedManager),
                }

                @staticmethod
                def get_attribute_types():
                    return {
                        "project": {
                            "type": RelatedManager,
                            "relation_kind": "collection",
                            "filter_lookup": "projects",
                        },
                    }

        GraphQL.graphql_filter_type_registry.clear()

        def relation_safe_issubclass(candidate, parent):
            if parent is GeneralManager and candidate is RelatedManager:
                return True
            return isinstance(candidate, type) and issubclass(candidate, parent)

        with patch(
            "general_manager.api.graphql_relations.safe_issubclass",
            side_effect=relation_safe_issubclass,
        ):
            filter_type = GraphQL._create_filter_options(CalculationManager)
            normalized = GraphQL._normalize_filter_input(
                CalculationManager,
                {"project": {"any": {"id": 1}}},
            )

        self.assertIsNotNone(filter_type)
        project_type = filter_type._meta.fields["project"].type
        self.assertIn("any", project_type._meta.fields)
        self.assertIn("none", project_type._meta.fields)
        self.assertEqual(
            normalized,
            {"filter": {"projects__id": 1}, "exclude": {}},
        )

    def test_create_filter_options_exposes_collection_any_none_filter(self):
        class ChildManager:
            __name__ = "ChildManager"

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @staticmethod
                def get_attribute_types():
                    return {"id": {"type": int}, "title": {"type": str}}

        class ParentManager:
            __name__ = "ParentManagerWithCollection"

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @staticmethod
                def get_attribute_types():
                    return {
                        "id": {"type": int},
                        "child_list": {
                            "type": Bucket[ChildManager],
                            "relation_kind": "collection",
                            "filter_lookup": "child",
                        },
                    }

        GraphQL.graphql_filter_type_registry.clear()

        def relation_safe_issubclass(candidate, parent):
            if parent is GeneralManager and candidate is ChildManager:
                return True
            return isinstance(candidate, type) and issubclass(candidate, parent)

        with patch(
            "general_manager.api.graphql_relations.safe_issubclass",
            side_effect=relation_safe_issubclass,
        ):
            filter_type = GraphQL._create_filter_options(ParentManager)

        self.assertIsNotNone(filter_type)
        relation_type = filter_type._meta.fields["child_list"].type
        self.assertIn("any", relation_type._meta.fields)
        self.assertIn("none", relation_type._meta.fields)
        child_filter_type = relation_type._meta.fields["any"].type
        self.assertIn("title__icontains", child_filter_type._meta.fields)

    def test_normalize_relation_filter_input_flattens_direct_and_any_filters(self):
        class ChildManager:
            __name__ = "NormalizeChildManager"

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @staticmethod
                def get_attribute_types():
                    return {"name": {"type": str}}

        class ParentManager:
            __name__ = "NormalizeParentManager"

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @staticmethod
                def get_attribute_types():
                    return {
                        "child": {
                            "type": ChildManager,
                            "relation_kind": "direct",
                            "filter_lookup": "child",
                        },
                        "child_list": {
                            "type": ChildManager,
                            "relation_kind": "collection",
                            "filter_lookup": "child",
                        },
                    }

        def relation_safe_issubclass(candidate, parent):
            if parent is GeneralManager and candidate is ChildManager:
                return True
            return isinstance(candidate, type) and issubclass(candidate, parent)

        with patch(
            "general_manager.api.graphql_relations.safe_issubclass",
            side_effect=relation_safe_issubclass,
        ):
            normalized = GraphQL._normalize_filter_input(
                ParentManager,
                {
                    "child": {"name__icontains": "alpha"},
                    "child_list": {"any": {"name": "beta"}},
                },
            )

        self.assertEqual(
            normalized,
            {
                "filter": {
                    "child__name__icontains": "alpha",
                    "child__name": "beta",
                },
                "exclude": {},
            },
        )

    def test_normalize_manager_input_infers_direct_relation(self):
        class RelatedManager:
            __name__ = "NormalizeManagerInputRelatedManager"

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {"id": Input(int)}

                @staticmethod
                def get_attribute_types():
                    return {"id": {"type": int}}

        class CalculationManager:
            __name__ = "NormalizeManagerInputCalculationManager"

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {
                    "project": Input(RelatedManager),
                }

                @staticmethod
                def get_attribute_types():
                    return {"project": {"type": RelatedManager}}

        def relation_safe_issubclass(candidate, parent):
            if parent is GeneralManager and candidate is RelatedManager:
                return True
            return isinstance(candidate, type) and issubclass(candidate, parent)

        with patch(
            "general_manager.api.graphql_relations.safe_issubclass",
            side_effect=relation_safe_issubclass,
        ):
            normalized = GraphQL._normalize_filter_input(
                CalculationManager,
                {"project": {"id": 1}},
            )

        self.assertEqual(
            normalized,
            {"filter": {"project__id": 1}, "exclude": {}},
        )

    def test_normalize_relation_filter_input_flattens_none_to_exclude(self):
        class ChildManager:
            __name__ = "NormalizeNoneChildManager"

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @staticmethod
                def get_attribute_types():
                    return {"name": {"type": str}}

        class ParentManager:
            __name__ = "NormalizeNoneParentManager"

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @staticmethod
                def get_attribute_types():
                    return {
                        "child_list": {
                            "type": ChildManager,
                            "relation_kind": "collection",
                            "filter_lookup": "child",
                        },
                    }

        def relation_safe_issubclass(candidate, parent):
            if parent is GeneralManager and candidate is ChildManager:
                return True
            return isinstance(candidate, type) and issubclass(candidate, parent)

        with patch(
            "general_manager.api.graphql_relations.safe_issubclass",
            side_effect=relation_safe_issubclass,
        ):
            normalized = GraphQL._normalize_filter_input(
                ParentManager,
                {"child_list": {"none": {"name__icontains": "blocked"}}},
            )

        self.assertEqual(normalized["filter"], {})
        self.assertEqual(normalized["exclude"], {"child__name__icontains": "blocked"})

    def test_normalize_filter_input_casts_id_equality_values(self):
        class IdentifierManager:
            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {"id": Input(int)}

                @staticmethod
                def get_attribute_types():
                    return {"id": {"type": int}}

        normalized = GraphQL._normalize_filter_input(
            IdentifierManager,
            {"id": "7", "id__exact": "8"},
        )

        self.assertEqual(
            normalized,
            {"filter": {"id": 7, "id__exact": 8}, "exclude": {}},
        )

    def test_normalize_filter_input_casts_each_id_in_value(self):
        class IdentifierManager:
            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {"id": Input(int)}

                @staticmethod
                def get_attribute_types():
                    return {"id": {"type": int}}

        normalized = GraphQL._normalize_filter_input(
            IdentifierManager,
            {"id__in": ["7", "8"]},
        )

        self.assertEqual(
            normalized,
            {"filter": {"id__in": [7, 8]}, "exclude": {}},
        )

    def test_build_identification_arguments_respects_optional_inputs(self):
        class DependencyManager(GeneralManager):
            pass

        class DummyManager:
            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {
                    "id": Input(int, required=True),
                    "as_of": Input(date, required=False),
                    "dependency": Input(DependencyManager, required=False),
                }

                @staticmethod
                def get_attribute_types():
                    return {}

        arguments = GraphQL._build_identification_arguments(DummyManager)

        self.assertIsInstance(arguments["id"].type, graphene.NonNull)
        self.assertNotIsInstance(arguments["as_of"].type, graphene.NonNull)
        self.assertNotIsInstance(arguments["dependency_id"].type, graphene.NonNull)


class GraphQLDirectiveRegistrationTests(TestCase):
    def setUp(self) -> None:
        GraphQL.reset_registry()

    def tearDown(self) -> None:
        GraphQL.reset_registry()
        super().tearDown()

    @staticmethod
    def _directive(name: str) -> GraphQLDirective:
        return GraphQLDirective(name=name, locations=[DirectiveLocation.FIELD])

    def _build_bootstrap_schema(
        self, *, with_subscription: bool = False
    ) -> graphene.Schema:
        GraphQL._query_fields = {
            "ping": graphene.String(),
            "resolve_ping": lambda *_args, **_kwargs: "pong",
        }
        if with_subscription:
            GraphQL._subscription_fields = {
                "ping": graphene.String(),
                "resolve_ping": lambda *_args, **_kwargs: "pong",
            }
        with (
            patch.object(gm_bootstrap.GraphQL, "register_search_query", autospec=True),
            patch("general_manager.bootstrap.add_graphql_url"),
        ):
            gm_bootstrap.handle_graph_ql([])
        schema = GraphQL.get_schema()
        self.assertIsNotNone(schema)
        return schema  # type: ignore[return-value]

    def test_bootstrap_registers_manager_names_before_building_interfaces(self):
        class FirstManager(GeneralManager):
            pass

        class SecondManager(GeneralManager):
            pass

        FirstManager.Interface = object()
        SecondManager.Interface = object()
        GraphQL._query_fields = {"ping": graphene.String()}

        def assert_all_managers_registered(_manager):
            self.assertIs(GraphQL.manager_registry["FirstManager"], FirstManager)
            self.assertIs(GraphQL.manager_registry["SecondManager"], SecondManager)

        with (
            patch.object(
                gm_bootstrap.GraphQL,
                "create_graphql_interface",
                side_effect=assert_all_managers_registered,
            ),
            patch.object(gm_bootstrap.GraphQL, "create_graphql_mutation"),
            patch.object(gm_bootstrap.GraphQL, "register_file_upload_mutation"),
            patch.object(gm_bootstrap.GraphQL, "register_search_query"),
            patch.object(
                gm_bootstrap.GraphQL,
                "register_current_user_capabilities",
            ),
            patch("general_manager.uploads.urls.add_file_upload_urls"),
            patch("general_manager.bootstrap.add_graphql_url"),
        ):
            gm_bootstrap.handle_graph_ql([FirstManager, SecondManager])

    def test_build_schema_directives_uses_specified_directives_by_default(self) -> None:
        directives = gm_bootstrap._build_schema_directives()
        self.assertEqual(directives, specified_directives)

    def test_build_schema_directives_merges_custom_directives_with_builtins(
        self,
    ) -> None:
        custom = self._directive("scenario")

        directives = gm_bootstrap._build_schema_directives([custom])

        self.assertEqual(
            [directive.name for directive in directives[:-1]],
            [directive.name for directive in specified_directives],
        )
        self.assertEqual(directives[-1].name, "scenario")

    def test_build_schema_directives_rejects_invalid_scalar_setting(self) -> None:
        with self.assertRaisesRegex(
            TypeError,
            "GRAPHQL_DIRECTIVES must be an iterable of GraphQLDirective instances",
        ):
            gm_bootstrap._normalize_graphql_directives("scenario")

    def test_build_schema_directives_rejects_invalid_entry(self) -> None:
        with self.assertRaisesRegex(
            TypeError,
            "GRAPHQL_DIRECTIVES must contain GraphQLDirective instances",
        ):
            gm_bootstrap._build_schema_directives(["scenario"])  # type: ignore[list-item]

    def test_build_schema_directives_rejects_duplicate_custom_names(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "Duplicate GraphQL directive name 'scenario' is not allowed",
        ):
            gm_bootstrap._build_schema_directives(
                [self._directive("scenario"), self._directive("scenario")]
            )

    def test_build_schema_directives_rejects_builtin_name_collision(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "Duplicate GraphQL directive name 'include' is not allowed",
        ):
            gm_bootstrap._build_schema_directives([self._directive("include")])

    def test_handle_graphql_registers_query_only_as_of_with_schema_datetime(
        self,
    ) -> None:
        schema = self._build_bootstrap_schema()

        directive = schema.graphql_schema.get_directive("asOf")

        self.assertIsNotNone(directive)
        assert directive is not None
        self.assertEqual(str(directive.args["date"].type), "DateTime!")
        self.assertEqual(directive.locations, (DirectiveLocation.QUERY,))
        self.assertIs(
            directive.args["date"].type.of_type,
            schema.graphql_schema.get_type("DateTime"),
        )

    def test_repeated_schema_builds_do_not_duplicate_as_of_or_datetime(self) -> None:
        first_schema = self._build_bootstrap_schema()
        second_schema = self._build_bootstrap_schema()

        for schema in (first_schema, second_schema):
            self.assertEqual(
                [
                    directive.name
                    for directive in schema.graphql_schema.directives
                    if directive.name == "asOf"
                ],
                ["asOf"],
            )
            self.assertEqual(
                [
                    type_.name
                    for type_ in schema.graphql_schema.type_map.values()
                    if type_.name == "DateTime"
                ],
                ["DateTime"],
            )

    @override_settings(
        GENERAL_MANAGER={
            "GRAPHQL_DIRECTIVES": [
                GraphQLDirective(name="asOf", locations=[DirectiveLocation.QUERY])
            ]
        }
    )
    def test_handle_graphql_rejects_custom_as_of_directive(self) -> None:
        with self.assertRaisesRegex(
            gm_bootstrap.DuplicateGraphQLDirectiveError,
            "Duplicate GraphQL directive name 'asOf' is not allowed",
        ):
            self._build_bootstrap_schema()

    @override_settings(
        GENERAL_MANAGER={
            "GRAPHQL_DIRECTIVES": [
                GraphQLDirective(name="scenario", locations=[DirectiveLocation.FIELD])
            ]
        }
    )
    def test_handle_graphql_merges_custom_directives_and_http_execution_works(
        self,
    ) -> None:
        schema = self._build_bootstrap_schema()

        directive_names = [
            directive.name for directive in schema.graphql_schema.directives
        ]
        self.assertIn("scenario", directive_names)
        self.assertIn("include", directive_names)
        self.assertIn("skip", directive_names)

        view = GeneralManagerGraphQLView(schema=schema)
        request = MagicMock()
        request.method = "POST"

        result = view.execute_graphql_request(
            request,
            {},
            "query { ping @scenario }",
            None,
            None,
            False,
        )
        self.assertIsNone(result.errors)
        self.assertEqual(result.data, {"ping": "pong"})

    @override_settings(
        GENERAL_MANAGER={
            "GRAPHQL_DIRECTIVES": [
                GraphQLDirective(name="scenario", locations=[DirectiveLocation.FIELD])
            ]
        }
    )
    def test_schema_with_subscription_root_exposes_merged_directives(self) -> None:
        schema = self._build_bootstrap_schema(with_subscription=True)

        self.assertIsNotNone(schema.graphql_schema.subscription_type)
        directive_names = [
            directive.name for directive in schema.graphql_schema.directives
        ]
        self.assertIn("scenario", directive_names)
        self.assertIn("include", directive_names)


class TestGetReadPermissionFilter(TestCase):
    def test_get_read_permission_filter(self):
        """
        Verify that get_read_permission_filter returns a read-permission plan from a manager's permission class.
        """

        class DummyManager:
            __name__ = "DummyManager"

            class Permission:
                def __init__(self, *args, **_kwargs):
                    self.args = args

                def get_permission_filter(self):
                    return [{"filter": {"num_field__exact": 42}, "exclude": {}}]

        info = MagicMock()
        info.context.user = AnonymousUser()
        result = get_read_permission_filter(DummyManager, info)
        self.assertEqual(
            result.filters,
            [{"filter": {"num_field__exact": 42}, "exclude": {}}],
        )
        self.assertTrue(result.requires_instance_check)
        self.assertEqual(result.instance_check_reasons, ("no_prefilter_backend",))


class TestGrapQlMutation(TestCase):
    def setUp(self) -> None:
        """
        Set up dummy manager classes and reset the GraphQL mutation registry for mutation-related tests.

        Defines mock manager classes with various interface methods to simulate different mutation scenarios, assigns them to instance attributes, and clears the GraphQL mutation registry to ensure test isolation.
        """

        class DummyManager:
            class Interface:
                input_fields: ClassVar[dict] = {}

                @classmethod
                def get_capabilities(cls):
                    """
                    Return the set of capabilities supported by the interface.

                    Returns:
                        frozenset: A frozenset containing "create", "update", and "delete".
                    """
                    return frozenset({"create", "update", "delete"})

                @classmethod
                def create(cls, *_args, **kwargs):
                    """
                    Create a new instance of the class using the provided positional and keyword arguments.

                    Parameters:
                        *args: Positional arguments forwarded to the class constructor.
                        **kwargs: Keyword arguments forwarded to the class constructor.

                    Returns:
                        instance: A newly created instance of `cls`.
                    """
                    pass

                def update(self, *_args, **kwargs):
                    """
                    Apply provided attribute updates to this instance and return the modified instance.

                    Updates attributes on the instance using keyword arguments where keys are attribute names and values are the new values to set. Positional arguments are ignored.

                    Parameters:
                        **kwargs: Mapping of attribute names to values to assign on the instance.

                    Returns:
                        The same instance after applying the updates.
                    """
                    pass

                def delete(self, *_args, **kwargs):
                    """
                    Delete this manager instance and return it.

                    Returns:
                        self: The same manager instance after deletion.
                    """
                    pass

        class DummyManager2:
            class Interface(InterfaceBase):
                def get_data(self, search_date: datetime | None = None):
                    """
                    Raises NotImplementedError to indicate that data retrieval is not implemented.

                    Parameters:
                        search_date (datetime, optional): An optional date to specify the context for data retrieval.
                    """
                    raise NotImplementedError

                @classmethod
                def get_attribute_types(cls):
                    """
                    Raises a NotImplementedError to indicate that subclasses must implement this method to return attribute type information.
                    """
                    raise NotImplementedError

                @classmethod
                def get_attributes(cls):
                    """
                    Raises a NotImplementedError to indicate that subclasses must implement the get_attributes method.
                    """
                    raise NotImplementedError

                @classmethod
                def filter(cls, **kwargs) -> None:
                    """
                    Raises a NotImplementedError to indicate that the filter method must be implemented by subclasses.
                    """
                    raise NotImplementedError

                @classmethod
                def exclude(cls, **kwargs) -> None:
                    """
                    Raises a NotImplementedError to indicate that the exclude operation is not implemented for this class.
                    """
                    raise NotImplementedError

                @classmethod
                def handle_interface(
                    cls,
                ) -> None:
                    """
                    Initializes or registers interface-related components for the class.

                    Intended to be called on a class to perform setup required for its interface functionality.
                    """
                    pass

                @classmethod
                def get_field_type(cls, field_name: str) -> None:
                    """
                    Get the declared type of the named attribute on the class.

                    Parameters:
                        field_name (str): The attribute name whose declared type should be retrieved.

                    Returns:
                        The attribute's type if available, otherwise None.
                    """
                    pass

                @classmethod
                def get_capabilities(cls):
                    """
                    Provide the set of capability names supported by this interface.

                    Returns:
                        frozenset: A frozenset of capability name strings (e.g., "create", "update", "delete"). Empty frozenset if no capabilities are supported.
                    """
                    return frozenset()

        self.manager = DummyManager
        self.manager2 = DummyManager2
        GraphQL._mutations = {}

    def _assert_unexpected_mutation_exception_is_sanitized(
        self,
        mutation_class: type[graphene.Mutation],
        **mutation_kwargs: object,
    ) -> None:
        error_id = "0123456789abcdef0123456789abcdef"
        private_message = "database host=secret"
        info = MagicMock()
        info.context.user = AnonymousUser()

        with (
            patch("general_manager.api.graphql_errors.uuid4") as uuid4_mock,
            self.assertRaises(GraphQLError) as caught,
        ):
            uuid4_mock.return_value.hex = error_id
            mutation_class.mutate(None, info, **mutation_kwargs)

        self.assertEqual(caught.exception.message, "An internal server error occurred.")
        self.assertEqual(
            caught.exception.extensions,
            {"code": "INTERNAL_SERVER_ERROR", "errorId": error_id},
        )
        self.assertNotIn(private_message, str(caught.exception.formatted))

    @patch("general_manager.api.graphql.GraphQL.generate_create_mutation_class")
    @patch("general_manager.api.graphql.GraphQL.generate_update_mutation_class")
    @patch("general_manager.api.graphql.GraphQL.generate_delete_mutation_class")
    def test_create_graphql_mutation(
        self, mock_delete: MagicMock, mock_update: MagicMock, mock_create: MagicMock
    ):
        """
        Tests that GraphQL.create_graphql_mutation generates and registers create, update, and delete mutation classes for a manager with the corresponding methods, and that the mutation generation methods are called exactly once.
        """
        GraphQL.create_graphql_mutation(self.manager)
        mock_create.assert_called_once()
        mock_update.assert_called_once()
        mock_delete.assert_called_once()
        self.assertEqual(
            list(GraphQL._mutations.keys()),
            ["createDummyManager", "updateDummyManager", "deleteDummyManager"],
        )

    @patch("general_manager.api.graphql.GraphQL.generate_create_mutation_class")
    @patch("general_manager.api.graphql.GraphQL.generate_update_mutation_class")
    @patch("general_manager.api.graphql.GraphQL.generate_delete_mutation_class")
    def test_create_graphql_mutation_skips_none_factory_results(
        self, mock_delete: MagicMock, mock_update: MagicMock, mock_create: MagicMock
    ):
        """Mutation registration skips failed factories and keeps later mutations."""

        class UpdateMutation(graphene.Mutation):
            """Minimal mutation class used as a registry sentinel."""

            @staticmethod
            def mutate(_root: object, _info: object) -> "UpdateMutation":
                return UpdateMutation()

        mock_create.return_value = None
        mock_update.return_value = UpdateMutation
        mock_delete.return_value = None

        GraphQL.create_graphql_mutation(self.manager)

        mock_create.assert_called_once()
        mock_update.assert_called_once()
        mock_delete.assert_called_once()
        self.assertEqual(GraphQL._mutations, {"updateDummyManager": UpdateMutation})

    @patch("general_manager.api.graphql.GraphQL.generate_create_mutation_class")
    @patch("general_manager.api.graphql.GraphQL.generate_update_mutation_class")
    @patch("general_manager.api.graphql.GraphQL.generate_delete_mutation_class")
    def test_create_graphql_mutation_with_undefined_create_update_delete(
        self, mock_delete: MagicMock, mock_update: MagicMock, mock_create: MagicMock
    ):
        """
        Test that no mutation classes are generated if the manager lacks create, update, and delete methods.

        Ensures that the mutation generation functions for create, update, and delete are not called when the manager does not define these methods.
        """
        GraphQL.create_graphql_mutation(self.manager2)
        mock_create.assert_not_called()
        mock_update.assert_not_called()
        mock_delete.assert_not_called()

    def test_create_write_fields(self):
        """
        Tests that `GraphQL.create_write_fields` returns input fields only for editable, non-derived attributes, mapping their types correctly and excluding derived fields.
        """

        class DummyInterface:
            @staticmethod
            def get_attribute_types():
                """
                Return metadata for each attribute, including type, requirement, derivation, default value, and editability.

                Returns:
                    dict: Maps attribute names to metadata describing their type, whether they are required or derived, their default value, and if they are editable.
                """
                return {
                    "field1": {
                        "type": str,
                        "is_required": True,
                        "is_derived": False,
                        "default": "default_value",
                        "is_editable": True,
                    },
                    "field2": {
                        "type": int,
                        "is_required": False,
                        "is_derived": False,
                        "default": None,
                        "is_editable": False,
                    },
                    "field3": {
                        "type": int,
                        "graphql_scalar": "bigint",
                        "is_required": False,
                        "is_derived": False,
                        "default": None,
                        "is_editable": True,
                    },
                    "created_at": {
                        "type": datetime,
                        "is_required": False,
                        "is_derived": True,
                        "default": None,
                        "is_editable": False,
                    },
                    "derived_field": {
                        "type": str,
                        "is_required": False,
                        "is_derived": True,
                        "default": None,
                        "is_editable": False,
                    },
                }

        fields = GraphQL.create_write_fields(DummyInterface)
        self.assertIn("field1", fields)
        self.assertIn("field2", fields)
        self.assertIn("field3", fields)
        self.assertIsInstance(fields["field1"], graphene.String)
        self.assertIsInstance(fields["field2"], graphene.Int)
        self.assertIsInstance(fields["field3"], BigIntScalar)
        self.assertNotIn("created_at", fields)
        self.assertNotIn("derived_field", fields)

    def test_create_filter_options_uses_bigint_scalar(self):
        class DummyInterface:
            @staticmethod
            def get_attribute_types():
                return {
                    "large_value": {
                        "type": int,
                        "graphql_scalar": "bigint",
                        "is_required": False,
                        "is_derived": False,
                        "default": None,
                        "is_editable": True,
                    }
                }

            @staticmethod
            def get_graph_ql_properties():
                return {}

        class DummyManagerWithBigInt:
            __name__ = "DummyManagerWithBigInt"
            Interface = DummyInterface

        GraphQL.graphql_filter_type_registry.clear()
        filter_type = GraphQL._create_filter_options(DummyManagerWithBigInt)

        self.assertIsNotNone(filter_type)
        self.assertIsInstance(filter_type.large_value, BigIntScalar)
        self.assertIsInstance(filter_type.large_value__gt, BigIntScalar)

    def test_create_filter_options_handles_generic_alias_type(self):
        class DummyInterface:
            @staticmethod
            def get_attribute_types():
                return {
                    "labels": {
                        "type": list[str],
                        "is_required": False,
                        "is_derived": False,
                        "default": None,
                        "is_editable": True,
                    }
                }

            @staticmethod
            def get_graph_ql_properties():
                return {}

        class DummyManagerWithGenericAlias:
            __name__ = "DummyManagerWithGenericAlias"
            Interface = DummyInterface

        GraphQL.graphql_filter_type_registry.clear()
        filter_type = GraphQL._create_filter_options(DummyManagerWithGenericAlias)

        self.assertIsNotNone(filter_type)
        self.assertIsInstance(filter_type.labels, graphene.String)

    def test_create_filter_options_handles_any_type(self):
        class DummyInterface:
            @staticmethod
            def get_attribute_types():
                return {
                    "metadata": {
                        "type": Any,
                        "is_required": False,
                        "is_derived": False,
                        "default": None,
                        "is_editable": True,
                    }
                }

            @staticmethod
            def get_graph_ql_properties():
                return {}

        class DummyManagerWithAny:
            __name__ = "DummyManagerWithAny"
            Interface = DummyInterface

        GraphQL.graphql_filter_type_registry.clear()
        filter_type = GraphQL._create_filter_options(DummyManagerWithAny)

        self.assertIsNotNone(filter_type)
        self.assertIsInstance(filter_type.metadata, graphene.String)

    def test_create_write_fields_with_manager(self):
        """
        Test that `GraphQL.create_write_fields` generates correct input fields for attributes of type `GeneralManager`, mapping single instances to `graphene.ID` and lists to `graphene.List`.
        """

        class DummyInterface:
            @staticmethod
            def get_attribute_types():
                """
                Return a dictionary describing attribute metadata for 'manager' and 'manager_list', including type, requirement, derivation, default value, and editability.
                """
                return {
                    "manager": {
                        "type": GeneralManager,
                        "is_required": True,
                        "is_derived": False,
                        "default": "default_value",
                        "is_editable": True,
                    },
                    "manager_list": {
                        "type": GeneralManager,
                        "is_required": False,
                        "is_derived": False,
                        "default": None,
                        "is_editable": False,
                    },
                }

        fields = GraphQL.create_write_fields(DummyInterface)
        self.assertIn("manager", fields)
        self.assertIn("manager_list", fields)
        self.assertIsInstance(fields["manager"], graphene.ID)
        self.assertIsInstance(fields["manager_list"], graphene.List)

    def test_create_write_fields_resolves_annotated_manager_relations(self):
        class RelatedManager(GeneralManager):
            pass

        class DummyInterface:
            @staticmethod
            def get_attribute_types():
                return {
                    "manager": {
                        "type": RelatedManager | None,
                        "is_required": False,
                        "is_derived": False,
                        "default": None,
                        "is_editable": True,
                    },
                    "manager_list": {
                        "type": Bucket[RelatedManager],
                        "is_required": False,
                        "is_derived": False,
                        "default": None,
                        "is_editable": True,
                    },
                }

        fields = GraphQL.create_write_fields(DummyInterface)

        self.assertIsInstance(fields["manager"], graphene.ID)
        self.assertIsInstance(fields["manager_list"], graphene.List)

        class DummyManager:
            Interface = DummyInterface

        self.assertEqual(
            _normalize_mutation_kwargs_for_manager(
                DummyManager,
                {"manager": "1", "manager_list": ["2"]},
            ),
            {"manager_id": "1", "manager_id_list": ["2"]},
        )

    def test_mutation_helpers_resolve_named_manager_relations(self):
        class RelatedManager(GeneralManager):
            pass

        def field_info(
            field_type,
            *,
            relation_kind=None,
        ):
            return {
                "type": field_type,
                "is_required": False,
                "is_derived": False,
                "default": None,
                "is_editable": True,
                "relation_kind": relation_kind,
            }

        class DummyInterface:
            @staticmethod
            def get_attribute_types():
                return {
                    "manager": field_info(
                        "RelatedManager",
                        relation_kind="direct",
                    ),
                    "manager_id": field_info(int),
                    "manager_list": field_info(
                        "RelatedManager",
                        relation_kind="collection",
                    ),
                    "manager_id_list": field_info(int),
                }

        class DummyManager:
            Interface = DummyInterface

        previous_registry = GraphQL.manager_registry
        GraphQL.manager_registry = {"RelatedManager": RelatedManager}
        self.addCleanup(setattr, GraphQL, "manager_registry", previous_registry)

        fields = GraphQL.create_write_fields(DummyInterface)
        normalized = _normalize_mutation_kwargs_for_manager(
            DummyManager,
            {"manager": "1", "manager_list": ["2"]},
        )

        self.assertIsInstance(fields["manager"], graphene.ID)
        self.assertIsInstance(fields["manager_list"], graphene.List)
        self.assertNotIn("manager_id", fields)
        self.assertNotIn("manager_id_list", fields)
        self.assertEqual(
            normalized,
            {"manager_id": "1", "manager_id_list": ["2"]},
        )
        self.assertEqual(
            _graphql_mutation_field_name(DummyManager, "manager_id"),
            "manager",
        )
        self.assertEqual(
            _graphql_mutation_field_name(DummyManager, "manager_id_list"),
            "managerList",
        )

    def test_generate_create_mutation_class(self):
        """
        Test that the generated create mutation class defines correct arguments, applies default values, and enforces mutation behavior.

        This test verifies that the mutation class generated for creating an instance:
        - Inherits from `graphene.Mutation`.
        - Defines required arguments with correct types and default values.
        - Returns a success flag and the created instance when invoked with valid input and context.
        - Raises a `GraphQLError` if the mutation context (`info`) is missing.
        """

        class DummyManager:
            def __init__(self, *_, **kwargs):
                """
                Initialize the instance and set the value of `field1` from keyword arguments if provided.
                """
                self.field1 = kwargs.get("field1")

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @classmethod
                def get_attribute_types(cls):
                    return {
                        "field1": {
                            "type": str,
                            "is_required": True,
                            "is_editable": True,
                            "is_derived": False,
                            "default": "test123",
                        }
                    }

            @classmethod
            def create(cls, *_args, **kwargs):
                return DummyManager(**kwargs)

        default_return_values = {
            "success": graphene.Boolean(),
            "instance": graphene.Field(DummyManager),
        }
        mutation_class = GraphQL.generate_create_mutation_class(
            DummyManager, default_return_values
        )
        self.assertTrue(issubclass(mutation_class, graphene.Mutation))
        self.assertIn("field1", mutation_class._meta.arguments)
        self.assertIsInstance(mutation_class._meta.arguments["field1"], graphene.String)
        self.assertTrue(mutation_class._meta.arguments["field1"].kwargs["required"])
        self.assertEqual(
            mutation_class._meta.arguments["field1"].kwargs["default_value"],
            "test123",
        )
        self.assertIn("success", mutation_class._meta.fields)
        self.assertIn("instance", mutation_class._meta.fields)

        info = MagicMock()
        info.context.user = AnonymousUser()

        mutation_result: dict = mutation_class.mutate(None, info, field1="test_value")
        self.assertTrue(mutation_result["success"])
        self.assertIsInstance(mutation_result["DummyManager"], DummyManager)
        self.assertEqual(mutation_result["DummyManager"].field1, "test_value")

        info = None
        with self.assertRaises(GraphQLError):
            mutation_result = mutation_class.mutate(None, info, field1="test_value")

    def test_generate_create_mutation_unexpected_exception_is_sanitized(self):
        private_message = "database host=secret"

        class DummyManager:
            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @classmethod
                def get_attribute_types(cls):
                    return {}

            @classmethod
            def create(cls, **_kwargs):
                raise OSError(private_message)

        mutation_class = GraphQL.generate_create_mutation_class(
            DummyManager, {"success": graphene.Boolean()}
        )
        self._assert_unexpected_mutation_exception_is_sanitized(mutation_class)

    def test_create_and_update_mutations_exclude_raw_relation_id_aliases(self):
        class DummyManager:
            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @classmethod
                def get_attribute_types(cls):
                    return {
                        "relation": {
                            "type": GeneralManager,
                            "is_required": True,
                            "is_editable": True,
                            "is_derived": False,
                            "default": None,
                            "relation_kind": "direct",
                            "filter_lookup": "relation",
                        },
                        "relation_id": {
                            "type": int,
                            "is_required": True,
                            "is_editable": True,
                            "is_derived": False,
                            "default": None,
                            "filter_lookup": "relation_id",
                        },
                        "external_id": {
                            "type": int,
                            "is_required": True,
                            "is_editable": True,
                            "is_derived": False,
                            "default": None,
                        },
                        "member_list": {
                            "type": GeneralManager,
                            "is_required": False,
                            "is_editable": True,
                            "is_derived": False,
                            "default": None,
                        },
                        "member_id_list": {
                            "type": list,
                            "is_required": False,
                            "is_editable": True,
                            "is_derived": False,
                            "default": None,
                        },
                    }

        default_return_values = {"success": graphene.Boolean()}

        create_mutation = GraphQL.generate_create_mutation_class(
            DummyManager, default_return_values
        )
        update_mutation = GraphQL.generate_update_mutation_class(
            DummyManager, default_return_values
        )

        self.assertIn("relation", create_mutation._meta.arguments)
        self.assertNotIn("relation_id", create_mutation._meta.arguments)
        self.assertIn("external_id", create_mutation._meta.arguments)
        self.assertIn("member_list", create_mutation._meta.arguments)
        self.assertNotIn("member_id_list", create_mutation._meta.arguments)
        self.assertIn("relation", update_mutation._meta.arguments)
        self.assertNotIn("relation_id", update_mutation._meta.arguments)
        self.assertIn("external_id", update_mutation._meta.arguments)
        self.assertIn("member_list", update_mutation._meta.arguments)
        self.assertNotIn("member_id_list", update_mutation._meta.arguments)

    def test_create_mutation_excludes_non_editable_canonical_relation(self):
        class DummyManager:
            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @classmethod
                def get_attribute_types(cls):
                    return {
                        "relation": {
                            "type": GeneralManager,
                            "is_required": True,
                            "is_editable": False,
                            "is_derived": False,
                            "default": None,
                            "relation_kind": "direct",
                            "filter_lookup": "relation",
                        },
                    }

        mutation_class = GraphQL.generate_create_mutation_class(
            DummyManager, {"success": graphene.Boolean()}
        )

        self.assertNotIn("relation", mutation_class._meta.arguments)

    def test_generate_create_mutation_class_forwards_history_comment(self):
        class DummyManager:
            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @classmethod
                def get_attribute_types(cls):
                    return {
                        "field1": {
                            "type": str,
                            "is_required": False,
                            "is_editable": True,
                            "is_derived": False,
                            "default": None,
                        }
                    }

            @classmethod
            def create(cls, *_args, **_kwargs):
                return DummyManager()

        mutation_class = GraphQL.generate_create_mutation_class(
            DummyManager, {"success": graphene.Boolean()}
        )
        info = MagicMock()
        info.context.user = AnonymousUser()

        with patch.object(
            DummyManager, "create", return_value=DummyManager()
        ) as create_mock:
            mutation_class.mutate(
                None,
                info,
                field1="test_value",
                history_comment="created through GraphQL",
            )

        create_mock.assert_called_once_with(
            creator_id=info.context.user.id,
            history_comment="created through GraphQL",
            field1="test_value",
        )

    def test_generate_update_mutation_class(self):
        """
        Test that the generated update mutation class defines correct arguments, applies default values, and enforces mutation behavior.

        This test verifies that the update mutation class produced by `GraphQL.generate_update_mutation_class`:
        - Inherits from `graphene.Mutation`.
        - Defines arguments and fields with appropriate types and default values.
        - Returns a success flag and updated instance when invoked with valid input and context.
        - Raises a `GraphQLError` if the mutation context (`info`) is missing.
        """

        class DummyManager:
            def __init__(self, *_, **kwargs):
                """
                Initialize the instance and set the value of `field1` from keyword arguments if provided.
                """
                self.field1 = kwargs.get("field1")

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @classmethod
                def get_attribute_types(cls):
                    return {
                        "field1": {
                            "type": str,
                            "is_required": True,
                            "is_editable": True,
                            "is_derived": False,
                            "default": "test123",
                        }
                    }

            @classmethod
            def update(cls, *_args, **kwargs):
                return DummyManager(**kwargs)

        default_return_values = {
            "success": graphene.Boolean(),
            "instance": graphene.Field(DummyManager),
        }
        mutation_class = GraphQL.generate_update_mutation_class(
            DummyManager, default_return_values
        )
        self.assertTrue(issubclass(mutation_class, graphene.Mutation))
        self.assertIn("field1", mutation_class._meta.arguments)
        self.assertIsInstance(mutation_class._meta.arguments["field1"], graphene.String)
        self.assertFalse(
            mutation_class._meta.arguments["field1"].kwargs.get("required", False)
        )
        self.assertEqual(
            mutation_class._meta.arguments["field1"].kwargs["default_value"],
            "test123",
        )
        self.assertIn("success", mutation_class._meta.fields)
        self.assertIn("instance", mutation_class._meta.fields)

        info = MagicMock()
        info.context.user = AnonymousUser()

        mutation_result: dict = mutation_class.mutate(
            None, info, field1="test_value", id=1
        )
        self.assertTrue(mutation_result["success"])
        self.assertIsInstance(mutation_result["DummyManager"], DummyManager)
        self.assertEqual(mutation_result["DummyManager"].field1, "test_value")

        info = None
        with self.assertRaises(GraphQLError):
            mutation_result = mutation_class.mutate(None, info, field1="test_value")

    def test_generate_update_mutation_class_filters_not_provided(self):
        class DummyManager:
            def __init__(self, *_, **_kwargs):
                pass

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @classmethod
                def get_attribute_types(cls):
                    return {
                        "field1": {
                            "type": str,
                            "is_required": False,
                            "is_editable": True,
                            "is_derived": False,
                            "default": None,
                        }
                    }

            @classmethod
            def update(cls, *_args, **_kwargs):
                return DummyManager()

        default_return_values = {
            "success": graphene.Boolean(),
            "instance": graphene.Field(DummyManager),
        }
        mutation_class = GraphQL.generate_update_mutation_class(
            DummyManager, default_return_values
        )
        info = MagicMock()
        info.context.user = AnonymousUser()

        with patch.object(
            DummyManager, "update", return_value=DummyManager()
        ) as update_mock:
            mutation_class.mutate(None, info, id="1", field1=NOT_PROVIDED)

        update_mock.assert_called_once_with(creator_id=info.context.user.id)

    def test_generate_update_mutation_unexpected_exception_is_sanitized(self):
        private_message = "database host=secret"

        class DummyManager:
            def __init__(self, **_kwargs):
                pass

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @classmethod
                def get_attribute_types(cls):
                    return {}

            def update(self, **_kwargs):
                raise OSError(private_message)

        mutation_class = GraphQL.generate_update_mutation_class(
            DummyManager, {"success": graphene.Boolean()}
        )
        self._assert_unexpected_mutation_exception_is_sanitized(
            mutation_class,
            id="1",
        )

    def test_generate_update_mutation_class_forwards_history_comment(self):
        class DummyManager:
            def __init__(self, *_, **_kwargs):
                pass

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @classmethod
                def get_attribute_types(cls):
                    return {
                        "field1": {
                            "type": str,
                            "is_required": False,
                            "is_editable": True,
                            "is_derived": False,
                            "default": None,
                        }
                    }

            def update(self, **_kwargs):
                return self

        mutation_class = GraphQL.generate_update_mutation_class(
            DummyManager, {"success": graphene.Boolean()}
        )
        info = MagicMock()
        info.context.user = AnonymousUser()

        with patch.object(
            DummyManager, "update", return_value=DummyManager()
        ) as update_mock:
            mutation_class.mutate(
                None,
                info,
                id="1",
                field1="test_value",
                history_comment="updated through GraphQL",
            )

        update_mock.assert_called_once_with(
            creator_id=info.context.user.id,
            history_comment="updated through GraphQL",
            field1="test_value",
        )

    def test_generate_delete_mutation_class(self):
        """
        Test that the delete mutation class generated by GraphQL has the correct fields and behavior.

        Verifies that the generated mutation class:
        - Inherits from `graphene.Mutation`.
        - Defines a `success` field.
        - Calls the manager's `delete` method and returns a success flag.
        - Raises a `GraphQLError` if the mutation context (`info`) is missing.
        """

        class DummyManager:
            def __init__(self, *_, **kwargs):
                """
                Initialize the instance and set the `field1` attribute from kwargs if provided.

                Parameters:
                    field1: Value to assign to `self.field1` if present in keyword arguments.
                """
                self.field1 = kwargs.get("field1")

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {"id": None}

                @classmethod
                def get_attribute_types(cls):
                    """
                    Provide a mapping of attribute names to their type and metadata for the interface.

                    Each mapping value is a dictionary with the following keys:
                    - `type`: the Python type of the attribute (e.g., `int`, `str`).
                    - `is_required`: `True` if the attribute must be provided.
                    - `is_editable`: `True` if the attribute can be written via mutations.
                    - `is_derived`: `True` if the attribute is computed/derived (and should be excluded from write inputs).
                    - `default`: the default value for the attribute when not provided.

                    Returns:
                        dict: A mapping from attribute name to its metadata dictionary.
                    """
                    return {
                        "id": {
                            "type": int,
                            "is_required": True,
                            "is_editable": True,
                            "is_derived": False,
                            "default": "test123",
                        }
                    }

            def delete(self, *_args, **_kwargs):
                """
                Perform the manager's delete operation and return the instance.

                Parameters:
                    *_args: Additional positional arguments accepted by the delete API; ignored by this implementation.
                    **_kwargs: Additional keyword arguments accepted by the delete API; ignored by this implementation.

                Returns:
                    The same instance after the delete operation.
                """
                return self

        default_return_values = {
            "success": graphene.Boolean(),
        }
        mutation_class = GraphQL.generate_delete_mutation_class(
            DummyManager, default_return_values
        )
        self.assertTrue(issubclass(mutation_class, graphene.Mutation))
        self.assertIn("success", mutation_class._meta.fields)
        self.assertIn("history_comment", mutation_class._meta.arguments)

        info = MagicMock()
        info.context.user = AnonymousUser()

        mutation_result: dict = mutation_class.mutate(None, info, id=1)
        self.assertTrue(mutation_result["success"])

        info = None
        with self.assertRaises(GraphQLError):
            mutation_result = mutation_class.mutate(None, info)

    def test_generate_delete_mutation_class_forwards_history_comment(self):
        class DummyManager:
            def __init__(self, *_, **_kwargs):
                pass

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {"id": None}

                @classmethod
                def get_attribute_types(cls):
                    return {}

            def delete(self, **_kwargs):
                return None

        mutation_class = GraphQL.generate_delete_mutation_class(
            DummyManager, {"success": graphene.Boolean()}
        )
        info = MagicMock()
        info.context.user = AnonymousUser()

        with patch.object(DummyManager, "delete", return_value=None) as delete_mock:
            mutation_class.mutate(
                None,
                info,
                id="1",
                history_comment="deleted through GraphQL",
            )

        delete_mock.assert_called_once_with(
            creator_id=info.context.user.id,
            history_comment="deleted through GraphQL",
        )

    def test_generate_delete_mutation_unexpected_exception_is_sanitized(self):
        private_message = "database host=secret"

        class DummyManager:
            def __init__(self, **_kwargs):
                pass

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @classmethod
                def get_attribute_types(cls):
                    return {}

            def delete(self, **_kwargs):
                raise OSError(private_message)

        mutation_class = GraphQL.generate_delete_mutation_class(
            DummyManager, {"success": graphene.Boolean()}
        )
        self._assert_unexpected_mutation_exception_is_sanitized(
            mutation_class,
            id="1",
        )


class GraphQLPropertyTypeHintTests(TestCase):
    def test_graphql_property_stores_return_type(self):
        def getter() -> int:
            return 1

        prop = GraphQLProperty(getter)
        self.assertEqual(prop.graphql_type_hint, int)

    def test_graphql_property_preserves_explicit_none_return_type(self):
        def getter() -> None:
            return None

        prop = GraphQLProperty(getter)
        self.assertIs(prop.graphql_type_hint, type(None))

    def test_graphql_property_caches_failed_type_hint_resolution(self):
        def getter() -> int:
            return 1

        prop = GraphQLProperty(getter)

        with patch(
            "general_manager.api.property.get_type_hints",
            side_effect=NameError("MissingType"),
        ) as get_hints:
            self.assertIsNone(prop.graphql_type_hint)
            self.assertIsNone(prop.graphql_type_hint)

        get_hints.assert_called_once()

    def test_graphql_property_non_callable_raises_typeerror(self):
        with self.assertRaises(TypeError):
            GraphQLProperty(123)  # type: ignore[arg-type]
