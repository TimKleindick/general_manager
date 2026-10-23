"""Chat query windows respect the generated GraphQL schema's server bounds."""

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models import CharField
from django.test import override_settings

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
        for chat_max, graphql_max in [(200, 3), (3, 6), (None, 3)]:
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
    def test_offset_boundary_and_overflow_use_the_graphql_window(self):
        result = self.run_query(offset=1, limit=2)
        self.assertEqual(len(result["data"]), 2)
        self.assertEqual(result["total_count"], 10)
        self.assertTrue(result["has_more"])
        schema = GraphQL.get_schema()
        with patch.object(schema, "execute", wraps=schema.execute) as execute:
            for offset, limit in [(2, 2), (3, 1), (1, None), (10**12, 1)]:
                with (
                    self.subTest(offset=offset, limit=limit),
                    self.assertRaisesRegex(ValueError, "effective query window"),
                ):
                    self.run_query(offset=offset, limit=limit)
            execute.assert_not_called()
