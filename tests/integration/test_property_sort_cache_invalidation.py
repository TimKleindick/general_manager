# type: ignore

from typing import ClassVar, cast

from django.core.cache import caches
from django.db.models.fields import CharField, IntegerField

from general_manager.api.property import graph_ql_property
from general_manager.cache.cache_decorator import _SENTINEL
from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.permission.manager_based_permission import ManagerBasedPermission
from general_manager.utils.testing import (
    GeneralManagerTransactionTestCase,
    LoggingCache,
)


class TestPropertySortCacheInvalidation(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()

        class RankedItem(GeneralManager):
            name: str
            rank: int

            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                rank = IntegerField()

                class Meta:
                    app_label = "general_manager"
                    use_soft_delete = True

            class Permission(ManagerBasedPermission):
                __create__: ClassVar[list[str]] = ["public"]

            @graph_ql_property(sortable=True, filterable=True)
            def score(self) -> int:
                return -self.rank

        cls.RankedItem = RankedItem
        cls.general_manager_classes = [RankedItem]

    def setUp(self) -> None:
        super().setUp()
        self.alpha = self.RankedItem.create(name="Item Alpha", rank=10)
        self.beta = self.RankedItem.create(name="Item Beta", rank=20)
        self.RankedItem.create(name="Other", rank=999)
        cast(LoggingCache, caches["default"]).ops = []

    def test_sort_cache_is_created_invalidated_and_rebuilt_after_create(self) -> None:
        initial_sorted = self.RankedItem.filter(name__contains="Item").sort("score")
        initial_ids = [item.identification["id"] for item in initial_sorted]
        self.assertEqual(
            initial_ids,
            [self.beta.identification["id"], self.alpha.identification["id"]],
        )

        cache_backend = cast(LoggingCache, caches["default"])
        sort_cache_keys = [
            op_entry[1]
            for op_entry in cache_backend.ops
            if len(op_entry) >= 2
            and op_entry[0] == "set"
            and str(op_entry[1]).startswith("gm:database_bucket:python_sort_ids:")
        ]
        self.assertTrue(
            sort_cache_keys, "Expected python sort cache key to be created."
        )
        cache_key = str(sort_cache_keys[0])
        self.assertIsNot(cache_backend.get(cache_key, _SENTINEL), _SENTINEL)

        cache_backend.ops = []
        gamma = self.RankedItem.create(name="Item Gamma", rank=30)
        self.assertIs(cache_backend.get(cache_key, _SENTINEL), _SENTINEL)

        cache_backend.ops = []
        rebuilt_sorted = self.RankedItem.filter(name__contains="Item").sort("score")
        rebuilt_ids = [item.identification["id"] for item in rebuilt_sorted]
        self.assertEqual(
            rebuilt_ids,
            [
                gamma.identification["id"],
                self.beta.identification["id"],
                self.alpha.identification["id"],
            ],
        )
        self.assertIn(("set", cache_key), cache_backend.ops)
