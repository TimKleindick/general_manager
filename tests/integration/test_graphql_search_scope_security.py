"""Search hits must belong to the field-authorized native candidate domain."""

from types import SimpleNamespace
from typing import ClassVar

from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from general_manager.api.graphql import GraphQL
from general_manager.api.property import graph_ql_property
from general_manager.interface import CalculationInterface
from general_manager.manager import GeneralManager, Input
from general_manager.permission import OverrideManagerPermission
from general_manager.search import backend_registry
from general_manager.search.backend_registry import configure_search_backend
from general_manager.search.backends.dev import DevSearchBackend
from general_manager.search.config import IndexConfig
from general_manager.search.indexer import SearchIndexer
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestGraphQLSearchScopeSecurity(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class ScopedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                key = Input(int, possible_values=[1])

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

                def check_permission(self, action, attribute):
                    return attribute != "secret" or self.instance.key == 1

            class SearchConfig:
                indexes: ClassVar[list] = [
                    IndexConfig(name="global", fields=["secret"])
                ]

            @graph_ql_property
            def secret(self) -> str:
                return "ordinary" if self.key == 1 else "needle-private"

        cls.Calculation = ScopedCalculation
        cls.general_manager_classes = [ScopedCalculation]
        super().setUpClass()

    @override_settings(DEBUG=False, GENERAL_MANAGER_VALIDATE_INPUT_VALUES=False)
    def test_indexed_outside_domain_cannot_reveal_denied_operand_matches(self):
        previous_backend = backend_registry._backend
        backend = DevSearchBackend()
        configure_search_backend(backend)
        try:
            assert [item.key for item in self.Calculation.all()] == [1]
            hidden = self.Calculation(key=2)
            user = AnonymousUser()
            assert not hidden.Permission(hidden, user).check_permission(
                "read", "secret"
            )
            indexer = SearchIndexer(backend)
            indexer.index_instance(hidden)
            indexer.index_instance(self.Calculation(key=1))
            resolver = GraphQL._query_fields["search"].resolver
            info = SimpleNamespace(context=SimpleNamespace(user=user))
            for query, expected in (
                ("needle-private", []),
                ("absent-value", []),
                ("ordinary", [1]),
                ("", [1]),
            ):
                with self.subTest(query=query):
                    result = resolver(
                        None, info, query=query, types=["ScopedCalculation"]
                    )
                    assert result["total"] == len(expected)
                    assert [item.key for item in result["results"]] == expected
        finally:
            configure_search_backend(previous_backend)
