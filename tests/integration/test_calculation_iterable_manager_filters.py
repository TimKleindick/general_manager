# type: ignore

from typing import ClassVar
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import CharField, IntegerField
from django.utils.crypto import get_random_string

from general_manager.api.property import graph_ql_property
from general_manager.interface import CalculationInterface, DatabaseInterface
from general_manager.manager import GeneralManager, Input
from general_manager.permission import ManagerBasedPermission
from general_manager.permission.base_permission import ReadPermissionPlan
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestCalculationIterableManagerFilters(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class FilterParent(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                amount = IntegerField()

        class ListCalculation(GeneralManager):
            calls: ClassVar[int] = 0

            class Interface(CalculationInterface):
                parent = Input(
                    FilterParent, possible_values=lambda: list(FilterParent.all())
                )

            @graph_ql_property(cache="dependency")
            def amount(self) -> int:
                type(self).calls += 1
                return self.parent.amount * 2

        class BucketCalculation(GeneralManager):
            class Interface(CalculationInterface):
                parent = Input(FilterParent, possible_values=lambda: FilterParent.all())

        cls.Parent = FilterParent
        cls.ListCalculation = ListCalculation
        cls.BucketCalculation = BucketCalculation
        cls.general_manager_classes = [FilterParent, ListCalculation, BucketCalculation]

    def setUp(self):
        super().setUp()
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="iterable-filters", password=get_random_string(12)
        )
        self.client.force_login(self.user)
        self.first = self.Parent.create(
            name="Alpha", amount=10, creator_id=self.user.id
        )
        self.second = self.Parent.create(
            name="Beta", amount=20, creator_id=self.user.id
        )
        self.ListCalculation.calls = 0

    def test_manager_filters_match_bucket_domains(self):
        for manager in (self.BucketCalculation, self.ListCalculation):
            for parent in (self.second, self.first):
                for criteria in (
                    {"parent": parent},
                    {"parent__id": parent.id},
                    {"parent_id": parent.id},
                    {"parent__id__in": [parent.id]},
                    {"parent__name__startswith": parent.name[:1]},
                ):
                    with self.subTest(manager=manager.__name__, criteria=criteria):
                        bucket = manager.filter(**criteria)
                        self.assertEqual(bucket.count(), 1)
                        self.assertEqual(
                            [item.parent.id for item in bucket], [parent.id]
                        )
                        self.assertEqual(bucket[0].parent.id, parent.id)

    def test_manager_excludes_match_bucket_domains(self):
        for manager in (self.BucketCalculation, self.ListCalculation):
            for criteria in ({"parent": self.first}, {"parent__name__startswith": "A"}):
                with self.subTest(manager=manager.__name__, criteria=criteria):
                    self.assertEqual(
                        [item.parent.id for item in manager.exclude(**criteria)],
                        [self.second.id],
                    )

    def test_intersections_and_grouped_excludes_match_bucket_domains(self):
        for manager in (self.BucketCalculation, self.ListCalculation):
            with self.subTest(manager=manager.__name__):
                self.assertEqual(
                    list(manager.filter(parent=self.first).filter(parent=self.second)),
                    [],
                )
                self.assertEqual(
                    list(manager.filter(parent=self.first).exclude(parent=self.first)),
                    [],
                )
                self.assertEqual(
                    [
                        item.parent.id
                        for item in manager.exclude(
                            parent__id=self.first.id, parent__name="Beta"
                        )
                    ],
                    [self.first.id, self.second.id],
                )
                self.assertEqual(
                    list(manager.filter(parent__name="missing")),
                    [],
                )

    def test_generated_relations_are_scoped_to_each_parent(self):
        self.assertEqual(
            [item.parent.id for item in self.second.list_calculation_list],
            [self.second.id],
        )
        self.assertEqual(
            [item.parent.id for item in self.first.list_calculation_list],
            [self.first.id],
        )

    def _query_relations(self, direction="ASC", paginate=False):
        arguments = ", pageSize: 2" if paginate else ""
        relation_arguments = "(pageSize: 1)" if paginate else ""
        return self.query(
            "{ filterParentList(orderBy: [{field: name, direction: "
            + direction
            + "}]"
            + arguments
            + ") { items { id listCalculationList"
            + relation_arguments
            + " { items { parent { id } amount } } } } }"
        )

    def test_http_relations_respect_parent_order_pagination_and_cache_identity(self):
        for paginate in (False, True):
            for direction in ("ASC", "DESC", "ASC"):
                with self.subTest(paginate=paginate, direction=direction):
                    response = self._query_relations(direction, paginate)
                    self.assertResponseNoErrors(response)
                    parents = [self.first, self.second]
                    if direction == "DESC":
                        parents.reverse()
                    self.assertEqual(
                        response.json()["data"]["filterParentList"]["items"],
                        [
                            {
                                "id": str(parent.id),
                                "listCalculationList": {
                                    "items": [
                                        {
                                            "parent": {"id": str(parent.id)},
                                            "amount": parent.amount * 2,
                                        }
                                    ]
                                },
                            }
                            for parent in parents
                        ],
                    )
        # Repeated requests reuse each parent's cached property separately.
        self.assertEqual(self.ListCalculation.calls, 2)

    def test_http_relation_denied_rows_stay_denied_after_cache_warmup(self):
        response = self._query_relations()
        self.assertResponseNoErrors(response)
        denied_id = self.second.id

        class RestrictedPermission(ManagerBasedPermission):
            __read__: ClassVar[list[str]] = ["public"]

            def get_read_permission_plan(self):
                return ReadPermissionPlan(
                    filters=[{"filter": {}, "exclude": {}}],
                    requires_instance_check=True,
                )

            def can_read_instance(self):
                return self.instance.parent.id != denied_id

        with patch.object(self.ListCalculation, "Permission", RestrictedPermission):
            response = self._query_relations("DESC", True)
        self.assertResponseNoErrors(response)
        rows = response.json()["data"]["filterParentList"]["items"]
        self.assertEqual(
            rows[0], {"id": str(self.second.id), "listCalculationList": {"items": []}}
        )
        self.assertEqual(
            rows[1]["listCalculationList"]["items"],
            [{"parent": {"id": str(self.first.id)}, "amount": 20}],
        )
