"""Collection identities must share and invalidate real calculation caches."""

from typing import ClassVar

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import IntegerField

from general_manager import graph_ql_property
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_index import serialize_dependency_identifier
from general_manager.cache.run_context import CalculationRunContext
from general_manager.interface import CalculationInterface, DatabaseInterface
from general_manager.manager import GeneralManager, Input
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestCollectionInputCache(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class CollectionCacheMember(GeneralManager):
            class Interface(DatabaseInterface):
                amount = IntegerField()

        class CollectionCachedSummary(GeneralManager):
            calls: ClassVar[int] = 0

            class Interface(CalculationInterface):
                members = Input(list[CollectionCacheMember], required=False)

            @graph_ql_property(cache="dependency")
            def total(self) -> int:
                type(self).calls += 1
                if self.members is None:
                    return -1
                return sum(member.amount for member in self.members)

        class CollectionRunSummary(GeneralManager):
            calls: ClassVar[int] = 0

            class Interface(CalculationInterface):
                members = Input(list[CollectionCacheMember], required=False)

            @graph_ql_property
            def sequence(self) -> str:
                type(self).calls += 1
                if self.members is None:
                    return "unrestricted"
                return ",".join(str(member.amount) for member in self.members)

        cls.Member = CollectionCacheMember
        cls.Summary = CollectionCachedSummary
        cls.RunSummary = CollectionRunSummary
        cls.general_manager_classes = [
            CollectionCacheMember,
            CollectionCachedSummary,
            CollectionRunSummary,
        ]

    def setUp(self):
        super().setUp()
        cache.clear()
        self.user = get_user_model().objects.create_user(username="collection-cache")
        self.first = self.Member.create(
            amount=10, creator_id=self.user.pk, ignore_permission=True
        )
        self.second = self.Member.create(
            amount=20, creator_id=self.user.pk, ignore_permission=True
        )
        self.Summary.calls = self.RunSummary.calls = 0

    def test_equivalent_reference_forms_share_dependency_cache(self):
        native = self.Summary(members=[self.first, self.second])
        aliases = self.Summary(members_id=[str(self.first.id), str(self.second.id)])
        mappings = self.Summary(
            members=[self.first.identification, self.second.identification]
        )
        self.assertEqual(native.identification, aliases.identification)
        self.assertEqual(native.identification, mappings.identification)
        self.assertEqual([native.total, aliases.total, mappings.total], [30, 30, 30])
        self.assertEqual(self.Summary.calls, 1)

    def test_every_member_update_invalidates_dependency_cached_summary(self):
        ids = [self.first.id, self.second.id]
        original = self.Summary(members_id=ids)
        # Prime the lazy/resolved input path before property dependency tracking.
        self.assertEqual(len(original.members), 2)
        self.assertEqual(original.total, 30)
        self.second.update(amount=25, creator_id=self.user.pk, ignore_permission=True)
        self.assertEqual(self.Summary(members_id=ids).total, 35)
        self.first.update(amount=12, creator_id=self.user.pk, ignore_permission=True)
        self.assertEqual(self.Summary(members_id=ids).total, 37)
        self.assertEqual(self.Summary.calls, 3)

    def test_run_cache_distinguishes_null_empty_order_and_duplicates(self):
        with CalculationRunContext():
            cases = [
                ({}, "unrestricted"),
                ({"members": None}, "unrestricted"),
                ({"members": []}, ""),
                ({"members": [self.first]}, "10"),
                ({"members": [self.first, self.second]}, "10,20"),
                ({"members_id": [str(self.first.id), str(self.second.id)]}, "10,20"),
                ({"members": [self.second, self.first]}, "20,10"),
                ({"members": [self.first, self.first]}, "10,10"),
            ]
            for kwargs, expected in cases:
                self.assertEqual(self.RunSummary(**kwargs).sequence, expected)
        self.assertEqual(self.RunSummary.calls, 6)

    def test_mutable_public_containers_do_not_change_cached_identity(self):
        values = [self.first]
        original = self.Summary(members=values)
        identification = original.identification
        self.assertEqual(original.total, 10)
        values.append(self.second)
        original.members.append(self.second)
        exposed = original.identification
        exposed["members"].append(self.second.identification)
        exposed["members"][0]["id"] = self.second.id
        self.assertEqual(original.identification, identification)
        self.assertEqual(original.members[0].id, self.first.id)
        self.assertEqual(original.total, 10)
        self.assertEqual(self.Summary(members=[self.first]).total, 10)
        self.assertEqual(self.Summary.calls, 1)

    def test_cached_member_access_replays_all_identification_dependencies(self):
        summary = self.Summary(members=[self.first, self.second])
        self.assertEqual(len(summary.members), 2)
        with DependencyTracker() as tracker:
            members = summary.members
        for member in members:
            expected = (
                type(member).__name__,
                "identification",
                serialize_dependency_identifier(member.identification),
            )
            self.assertIn(expected, tracker)
