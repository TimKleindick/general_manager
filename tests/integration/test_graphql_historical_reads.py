from datetime import timedelta
from typing import ClassVar

from django.db import models
from django.utils import timezone

from general_manager.api.property import graph_ql_property
from general_manager.as_of import current_as_of_date
from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission import ManagerBasedPermission
from general_manager.permission.permission_checks import register_permission
from general_manager.utils.testing import GeneralManagerTransactionTestCase


@register_permission("historicalRecordVisible")
def historical_record_visible(instance, _user, _config):
    return instance.visible


class TestGraphQLHistoricalReads(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class SnapshotParent(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)

        class SnapshotRecord(GeneralManager):
            cache_calls: ClassVar[int] = 0

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)
                visible = models.BooleanField(default=True)
                parent = models.ForeignKey(
                    "general_manager.SnapshotParent",
                    on_delete=models.CASCADE,
                    related_name="records",
                )

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["historicalRecordVisible"]

            @graph_ql_property(cache="dependency")
            def cached_name(self) -> str:
                type(self).cache_calls += 1
                return self.name

        cls.Parent = SnapshotParent
        cls.Record = SnapshotRecord
        cls.general_manager_classes = [SnapshotParent, SnapshotRecord]
        super().setUpClass()

    def setUp(self):
        super().setUp()
        self.before_creation = timezone.now() - timedelta(microseconds=1)
        self.parent = self.Parent.create(ignore_permission=True, name="Old parent")
        self.record = self.Record.create(
            ignore_permission=True, name="Old record", parent=self.parent, visible=True
        )
        self.before_change = timezone.now()
        self.parent.update(ignore_permission=True, name="New parent")
        self.record.update(ignore_permission=True, name="New record")
        self.after_change = timezone.now()
        self.Record.cache_calls = 0
        history_dates = list(
            self.Record.Interface._model.history.filter(id=self.record.id)
            .order_by("history_date")
            .values_list("history_date", flat=True)
        )
        self.assertLessEqual(history_dates[0], self.before_change)
        self.assertGreater(history_dates[-1], self.before_change)
        self.assertLessEqual(history_dates[-1], self.after_change)

    def detail(self, at=None):
        declaration = "$id: ID!" + (", $at: DateTime!" if at else "")
        directive = "@asOf(date: $at)" if at else ""
        variables = {"id": self.record.id}
        if at:
            variables["at"] = at.isoformat()
        response = self.query(
            f"query({declaration}) {directive} {{ snapshotRecord(id: $id) "
            "{ name cachedName parent { name } } }",
            variables=variables,
        )
        self.assertIsNone(current_as_of_date())
        return response

    def test_http_details_and_cache_are_separated_by_snapshot_and_request(self):
        for at, prefix, calls in [
            (None, "New", 1),
            (self.before_change, "Old", 2),
            (self.after_change, "New", 3),
            (self.before_change, "Old", 3),
            (None, "New", 3),
        ]:
            with self.subTest(at=at):
                response = self.detail(at)
                self.assertResponseNoErrors(response)
                self.assertEqual(
                    response.json()["data"]["snapshotRecord"],
                    {
                        "name": f"{prefix} record",
                        "cachedName": f"{prefix} record",
                        "parent": {"name": f"{prefix} parent"},
                    },
                )

                self.assertEqual(self.Record.cache_calls, calls)

    def test_explicit_search_dates_use_exact_recent_history(self):
        for at, name in (
            (self.before_change, "Old record"),
            (self.after_change, "New record"),
        ):
            self.assertEqual(self.Record(self.record.id, search_date=at).name, name)
            self.assertEqual(
                [
                    item.name
                    for item in self.Record.filter(id=self.record.id, search_date=at)
                ],
                [name],
            )
            self.assertEqual(
                [
                    item.name
                    for item in self.Record.exclude(name="unrelated", search_date=at)
                ],
                [name],
            )

    def test_http_lists_and_reverse_relations_use_snapshot(self):
        response = self.query(
            "query($at: DateTime!) @asOf(date: $at) { "
            "snapshotRecordList { items { name } } "
            "snapshotParentList { items { name recordsList { items { name } } } } }",
            variables={"at": self.before_change.isoformat()},
        )
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"],
            {
                "snapshotRecordList": {"items": [{"name": "Old record"}]},
                "snapshotParentList": {
                    "items": [
                        {
                            "name": "Old parent",
                            "recordsList": {"items": [{"name": "Old record"}]},
                        }
                    ]
                },
            },
        )

    def test_http_permissions_use_historical_row(self):
        self.record.update(ignore_permission=True, visible=False)
        denied_at = timezone.now()
        response = self.detail(self.before_change)
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["snapshotRecord"]["name"], "Old record"
        )
        for at in (denied_at, None):
            response = self.detail(at)
            self.assertResponseNoErrors(response)
            self.assertEqual(
                response.json()["data"]["snapshotRecord"],
                {
                    "name": None,
                    "cachedName": None,
                    "parent": None,
                },
            )
        self.record.update(ignore_permission=True, visible=True)
        response = self.detail(denied_at)
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["snapshotRecord"],
            {
                "name": None,
                "cachedName": None,
                "parent": None,
            },
        )
        for at, names in ((self.before_change, ["Old record"]), (denied_at, [])):
            response = self.query(
                "query($at: DateTime!) @asOf(date: $at) { "
                "snapshotRecordList { items { name } pageInfo { totalCount } } "
                "snapshotParentList { items { recordsList { items { name } pageInfo { totalCount } } } } }",
                variables={"at": at.isoformat()},
            )
            self.assertResponseNoErrors(response)
            self.assertEqual(
                response.json()["data"]["snapshotRecordList"],
                {
                    "items": [{"name": name} for name in names],
                    "pageInfo": {"totalCount": len(names)},
                },
            )
            self.assertEqual(
                response.json()["data"]["snapshotParentList"]["items"],
                [
                    {
                        "recordsList": {
                            "items": [{"name": name} for name in names],
                            "pageInfo": {"totalCount": len(names)},
                        }
                    }
                ],
            )

    def test_http_before_creation_does_not_fall_back_to_current(self):
        response = self.detail(self.before_creation)
        self.assertResponseHasErrors(response)
        self.assertIsNone(response.json()["data"]["snapshotRecord"])
