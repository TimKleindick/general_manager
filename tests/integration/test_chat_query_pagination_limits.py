"""Chat query windows respect the generated GraphQL schema's server bounds."""

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models import CharField
from django.test import override_settings
from graphql import parse

from general_manager.chat.graphql_contract import ChatReadContractError

from general_manager.api.graphql import GraphQL
from general_manager.chat.tools import query
from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestChatQueryPaginationLimits(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class Chatrecord(GeneralManager):
            chat_exposed = True

            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

        cls.general_manager_classes = [Chatrecord]
        cls.manager = Chatrecord

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username="chat-pagination")
        self.manager.Factory.create_batch(10)

    def run_query(self, **pagination):
        return query(
            manager="Chatrecord",
            filters={},
            fields=["name"],
            context=SimpleNamespace(user=self.user),
            **pagination,
        )

    @override_settings(GENERAL_MANAGER={})
    def test_default_chat_query_works_with_default_graphql_maximum(self):
        result = self.run_query()
        self.assertEqual(len(result["data"]), 10)
        self.assertEqual(result["total_count"], 10)
        self.assertFalse(result["has_more"])

    def test_configured_chat_and_graphql_limits_use_the_smaller_window(self):
        for chat_max, graphql_max in [
            (200, 3),
            (3, 6),
            (None, 3),
            (True, 3),
            (False, 3),
        ]:
            with (
                self.subTest(chat_max=chat_max, graphql_max=graphql_max),
                override_settings(
                    GENERAL_MANAGER={
                        "CHAT": {"max_results": chat_max},
                        "GRAPHQL_DEFAULT_PAGE_SIZE": 2,
                        "GRAPHQL_MAX_PAGE_SIZE": graphql_max,
                    }
                ),
            ):
                for limit in (None, 1000):
                    result = self.run_query(limit=limit)
                    self.assertEqual(len(result["data"]), 3)
                    self.assertEqual(result["total_count"], 10)
                    self.assertTrue(result["has_more"])

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {"max_results": 200},
            "GRAPHQL_DEFAULT_PAGE_SIZE": 2,
            "GRAPHQL_MAX_PAGE_SIZE": 3,
        }
    )
    def test_offsets_use_at_most_two_bounded_native_pages(self):
        schema = GraphQL.get_schema()
        expected = [{"name": item.name} for item in self.manager.all()]
        for offset, limit in [(1, 2), (2, 2), (3, 1), (1, None), (100, 1)]:
            with (
                self.subTest(offset=offset, limit=limit),
                patch.object(schema, "execute", wraps=schema.execute) as execute,
            ):
                result = self.run_query(offset=offset, limit=limit)
                self.assertEqual(
                    result["data"], expected[offset : offset + (limit or 3)]
                )
                for call in execute.call_args_list:
                    document = parse(call.args[0])
                    root = document.definitions[0].selection_set.selections[0]
                    size_argument = next(
                        arg for arg in root.arguments if arg.name.value == "pageSize"
                    )
                    size = call.kwargs["variable_values"][
                        size_argument.value.name.value
                    ]
                    self.assertLessEqual(size, 3)
                self.assertLessEqual(execute.call_count, 2)
                self.assertEqual(result["total_count"], 10)

    def test_out_of_range_native_page_is_rejected_before_execute(self):
        schema = GraphQL.get_schema()
        with patch.object(schema, "execute", wraps=schema.execute) as execute:
            with self.assertRaises(ChatReadContractError):
                self.run_query(offset=10**12, limit=1)
            execute.assert_not_called()

    def test_invalid_graphql_maximum_fails_before_execution(self):
        schema = GraphQL.get_schema()
        for maximum in (False, 0, "100"):
            with (
                self.subTest(maximum=maximum),
                override_settings(GENERAL_MANAGER={"GRAPHQL_MAX_PAGE_SIZE": maximum}),
                patch.object(schema, "execute", wraps=schema.execute) as execute,
            ):
                with self.assertRaisesRegex(ValueError, "GRAPHQL_MAX_PAGE_SIZE"):
                    self.run_query()
                execute.assert_not_called()
