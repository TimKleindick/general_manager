"""Manager-valued history preserves one snapshot across dependent reads."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, timedelta
from typing import ClassVar
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.db import models
from django.utils import timezone

from general_manager.api.property import GraphQLProperty, graph_ql_property
from general_manager.as_of import (
    HistoricalContextConflictError,
    HistoricalMutationError,
    HistoricalReadNotSupportedError,
    as_of,
    current_as_of_date,
)
from general_manager.cache.signals import pre_data_change
from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission import ManagerBasedPermission
from general_manager.permission.permission_checks import register_permission
from general_manager.utils.testing import GeneralManagerTransactionTestCase


@register_permission("historyVersionVisible")
def history_version_visible(instance, _user, _config):
    return instance.visible


class TestManagerHistory(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """Build real historical models with plain and custom properties."""

        class HistoryParent(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=50)

        class ParentProperty(property):
            def __get__(self, instance, owner=None):
                """Re-query once inside fget and once in custom descriptor work."""
                if instance is None:
                    return self
                return (
                    super().__get__(instance, owner),
                    HistoryParent(instance.parent_id).name,
                )

        class HistoryProperties:
            @ParentProperty
            def inherited_custom_parent(self):
                return HistoryParent(self.parent_id).name

            @property
            def inherited_parent(self):
                return HistoryParent(self.parent_id).name

            @property
            def deferred_parent_names(self):
                yield HistoryParent(self.parent_id).name

            @property
            async def deferred_snapshots(self):
                yield current_as_of_date()

        class HistoryRecord(HistoryProperties, GeneralManager):
            cache_calls: ClassVar[int] = 0

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=50)
                visible = models.BooleanField(default=True)
                parent = models.ForeignKey(
                    "general_manager.HistoryParent",
                    related_name="records",
                    on_delete=models.CASCADE,
                )

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["historyVersionVisible"]

            @property
            def label(self) -> str:
                return f"{self.name}/{self.parent.name}"

            @property
            def requeried_parent(self) -> str:
                return HistoryParent(self.parent_id).name

            @ParentProperty
            def custom_parent(self):
                return HistoryParent(self.parent_id).name

            @property
            def parent_query(self):
                return HistoryParent.filter(id=self.parent_id)

            @property
            async def deferred_snapshot(self):
                return current_as_of_date()

            @graph_ql_property(cache="dependency")
            def cached_parent_name(self) -> str:
                type(self).cache_calls += 1
                return HistoryParent(self.parent_id).name

        class IdOnlyHistoryRecord(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=50)

            def __init__(self, id):
                self.constructed_at = current_as_of_date()
                super().__init__(id)

        cls.Parent, cls.Record = HistoryParent, HistoryRecord
        cls.IdOnly = IdOnlyHistoryRecord
        cls.general_manager_classes = [
            HistoryParent,
            HistoryRecord,
            IdOnlyHistoryRecord,
        ]
        super().setUpClass()

    def setUp(self):
        super().setUp()
        self.parent = self.Parent.create(name="Old parent", ignore_permission=True)
        self.record = self.Record.create(
            name="Old record", parent=self.parent, ignore_permission=True
        )
        self.explicit = timezone.now()
        self.parent.update(name="Middle parent", ignore_permission=True)
        self.record.update(name="New record", visible=False, ignore_permission=True)
        rows = list(self.raw.order_by("history_date", "history_id"))
        self.start = rows[0].history_date
        self.end = rows[1].history_date
        self.cutoff = self.end.astimezone(UTC) - timedelta(microseconds=1)
        self.Record.cache_calls = 0

    @property
    def raw(self):
        return self.Record.Interface._model.history.filter(
            id=self.record.identification["id"]
        )

    def test_history_returns_original_manager_properties_and_nested_snapshot(self):
        historical = self.record.history.order_by("history_date", "history_id").first()
        self.assertIs(type(historical), self.Record)
        self.assertEqual(historical.identification, self.record.identification)
        self.assertEqual(historical.name, "Old record")
        self.assertEqual(historical.label, "Old record/Middle parent")
        self.assertEqual(historical._effective_search_date, self.cutoff)
        self.assertEqual(historical.parent._effective_search_date, self.cutoff)
        nested = historical.parent.records_list.get(id=historical.id)
        self.assertEqual(nested.name, "Old record")
        self.assertEqual(nested._effective_search_date, self.cutoff)

    def test_filtered_history_uses_successor_from_complete_timeline(self):
        """Filtering versions cannot shift the selected version's closing date."""
        historical = self.record.history.filter(name="Old record").get()
        self.assertEqual(historical._effective_search_date, self.cutoff)
        self.assertEqual(historical.name, "Old record")
        self.assertTrue(all(type(item) is self.Record for item in self.record.history))
        self.assertIs(type(self.record.history[0]), self.Record)
        self.assertEqual(len(list(self.record.history[:1])), 1)

    def test_id_only_constructor_preserves_closed_and_open_snapshots(self):
        current = self.IdOnly.create(name="Old custom", ignore_permission=True)
        current.update(name="New custom", ignore_permission=True)
        closing = current.history.raw.first().history_date - timedelta(microseconds=1)
        historical = current.history.order_by("history_date", "history_id").first()
        self.assertIs(type(historical), self.IdOnly)
        self.assertEqual(historical.name, "Old custom")
        self.assertEqual(historical.constructed_at, closing)
        self.assertEqual(historical._effective_search_date, closing)
        latest = current.history.first()
        self.assertEqual(latest.name, "New custom")
        self.assertIsNotNone(latest._effective_search_date)
        self.assertEqual(latest.constructed_at, latest._effective_search_date)
        self.assertIsNone(current_as_of_date())

    def test_id_only_constructor_preserves_explicit_date_and_rejects_conflict(self):
        current = self.IdOnly.create(name="Old explicit", ignore_permission=True)
        record_id = current.id
        snapshot = timezone.now()
        current.update(name="New explicit", ignore_permission=True)
        with as_of(snapshot):
            dated = self.IdOnly(record_id)
            self.assertEqual(dated.history.first().constructed_at, snapshot)
        historical = dated.history.first()
        self.assertEqual(historical.name, "Old explicit")
        self.assertEqual(historical._effective_search_date, snapshot)
        self.assertEqual(historical.constructed_at, snapshot)
        with as_of(timezone.now()), self.assertRaises(HistoricalContextConflictError):
            dated.history.first()

    def test_raw_audit_and_scalar_projections_remain_available(self):
        history = self.record.history
        self.assertEqual(history.raw.first().history_type, "~")
        self.assertEqual(history.raw.first().history_date, self.end)
        self.assertEqual(history.count(), 2)
        self.assertTrue(history.exists())
        self.assertEqual(
            list(history.order_by("history_date").values_list("name", flat=True)),
            ["Old record", "New record"],
        )

    def test_query_selection_methods_and_dated_slices_keep_manager_results(self):
        """Selection and slicing retain manager results and exact dated reads."""
        history = self.record.history
        self.assertEqual(history.all().reverse().first().name, "Old record")
        self.assertEqual(history.exclude(name="New record").last().name, "Old record")
        self.assertEqual(
            history.earliest("history_date", "history_id").name, "Old record"
        )
        self.assertEqual(
            history.latest("history_date", "history_id").name, "New record"
        )
        self.assertEqual(
            list(history.values("name")),
            [{"name": "New record"}, {"name": "Old record"}],
        )
        dated = self.Record(self.record.id, search_date=self.explicit)
        self.assertEqual([item.name for item in dated.history[:1]], ["Old record"])
        self.assertEqual(dated.history[0]._effective_search_date, self.explicit)

    def test_equivalent_timezone_offsets_preserve_the_explicit_instant(self):
        offset = self.explicit.astimezone(timezone.get_fixed_timezone(120))
        dated = self.Record(self.record.id, search_date=offset)
        with as_of(self.explicit):
            historical = dated.history.first()
            self.assertEqual(historical._effective_search_date, offset)
            self.assertEqual(historical.requeried_parent, "Old parent")
        self.assertIsNone(current_as_of_date())

    def test_history_query_retains_ambient_date_after_context_exit(self):
        record_id = self.record.id
        with as_of(self.explicit):
            delayed = self.record.history.filter(id=record_id)
        historical = delayed.first()
        self.assertEqual(historical.name, "Old record")
        self.assertEqual(historical._effective_search_date, self.explicit)

    def test_inherited_plain_property_uses_snapshot_without_changing_iteration(self):
        historical = self.Record(self.record.id, search_date=self.explicit)
        self.assertEqual(historical.inherited_parent, "Old parent")
        values = dict(historical)
        self.assertNotIn("inherited_parent", values)
        self.assertEqual(asyncio.run(values["deferred_snapshot"]), self.explicit)

    def test_custom_property_getter_and_descriptor_share_the_history_snapshot(self):
        self.parent.update(name="Latest parent", ignore_permission=True)
        historical = self.record.history.order_by("history_date", "history_id").first()
        self.assertEqual(historical.custom_parent, ("Middle parent", "Middle parent"))
        self.assertEqual(
            historical.inherited_custom_parent, ("Middle parent", "Middle parent")
        )
        self.assertEqual(self.record.custom_parent, ("Latest parent", "Latest parent"))
        self.assertIsNone(current_as_of_date())

    def test_custom_property_conflicts_before_its_descriptor_executes(self):
        historical = self.Record(self.record.id, search_date=self.explicit)
        with as_of(self.cutoff), self.assertRaises(HistoricalContextConflictError):
            _ = historical.custom_parent
        self.assertIsNone(current_as_of_date())

    def test_generator_properties_fail_closed_before_their_bodies_run(self):
        """Reject raw deferred bodies before any dependent read can escape."""
        historical = self.Record(self.record.id, search_date=self.explicit)
        entered = []

        def names(manager) -> Iterator[str]:
            entered.append(current_as_of_date())
            yield self.Parent(manager.parent_id).name

        graphql = GraphQLProperty(names)
        for read in (
            lambda: historical.deferred_parent_names,
            lambda: graphql.__get__(historical, self.Record),
        ):
            with self.assertRaises(HistoricalReadNotSupportedError):
                read()
        self.assertEqual(entered, [])
        self.assertIsNone(current_as_of_date())

    def test_awaitable_property_retains_snapshot_and_restores_context(self):
        historical = self.Record(self.record.id, search_date=self.explicit)
        self.assertEqual(asyncio.run(historical.deferred_snapshot), self.explicit)
        self.assertIsNone(current_as_of_date())

    def test_async_generator_properties_fail_closed_before_deferred_execution(self):
        historical = self.Record(self.record.id, search_date=self.explicit)
        with self.assertRaises(HistoricalReadNotSupportedError):
            _ = historical.deferred_snapshots
        self.assertIsNone(current_as_of_date())

    def test_returned_iterator_is_closed_under_snapshot_before_rejection(self):
        historical = self.Record(self.record.id, search_date=self.explicit)
        closed = []

        def values():
            try:
                yield "ready"
            finally:
                closed.append(current_as_of_date())

        iterator = values()
        self.assertEqual(next(iterator), "ready")

        def returned(manager) -> Iterator[str]:
            return iterator

        with self.assertRaises(HistoricalReadNotSupportedError):
            GraphQLProperty(returned).__get__(historical, self.Record)
        self.assertEqual(closed, [self.explicit])
        self.assertIsNone(current_as_of_date())

    def test_current_generator_property_keeps_its_original_protocol(self):
        """Current generator properties preserve send, throw and close behavior."""
        closed = []

        def receiver(manager) -> Iterator[object]:
            try:
                sent = yield "ready"
                yield sent, current_as_of_date()
            except ValueError as error:
                yield str(error), current_as_of_date()
            finally:
                closed.append(current_as_of_date())

        values = GraphQLProperty(receiver).__get__(self.record, self.Record)
        self.assertEqual(next(values), "ready")
        self.assertEqual(values.send("payload"), ("payload", None))
        self.assertEqual(values.throw(ValueError("forwarded")), ("forwarded", None))
        values.close()
        self.assertEqual(closed, [None])

    def test_explicit_date_is_preserved_and_does_not_expose_future_version(self):
        dated = self.Record(self.record.id, search_date=self.explicit)
        historical = dated.history.first()
        self.assertEqual(historical.name, "Old record")
        self.assertEqual(historical._effective_search_date, self.explicit)
        self.assertEqual(historical.parent.name, "Old parent")
        with as_of(self.explicit):
            self.assertEqual(
                dated.history.first()._effective_search_date, self.explicit
            )
        with as_of(self.end), self.assertRaises(HistoricalContextConflictError):
            dated.history.first()

    def test_properties_requeries_and_lazy_results_reuse_snapshot_and_cache(self):
        historical = self.Record(self.record.id, search_date=self.explicit)
        self.assertEqual(historical.requeried_parent, "Old parent")
        self.assertEqual(historical.parent_query.first().name, "Old parent")
        self.assertEqual(historical.cached_parent_name, "Old parent")
        self.assertEqual(historical.cached_parent_name, "Old parent")
        self.assertEqual(self.record.cached_parent_name, "Middle parent")
        self.assertEqual(self.Record.cache_calls, 2)
        self.assertIsNone(current_as_of_date())
        self.assertTrue(
            self.Record.Permission(historical, AnonymousUser()).can_read_instance()
        )
        self.assertFalse(
            self.Record.Permission(self.record, AnonymousUser()).can_read_instance()
        )

    def test_latest_version_freezes_now_once_across_later_related_updates(self):
        historical = self.record.history.first()
        frozen = historical._effective_search_date
        self.assertIsNotNone(frozen)
        self.parent.update(name="Later parent", ignore_permission=True)
        self.assertEqual(historical.parent.name, "Middle parent")
        self.assertEqual(historical.requeried_parent, "Middle parent")
        self.assertEqual(historical.parent._effective_search_date, frozen)

    def test_update_boundary_is_exclusive_and_explicit_boundary_selects_new_version(
        self,
    ):
        before = self.Record(self.record.id, search_date=self.cutoff)
        at = self.Record(self.record.id, search_date=self.end)
        self.assertEqual(before.name, "Old record")
        self.assertEqual(at.name, "New record")
        self.assertEqual(at.history.first()._effective_search_date, self.end)

    def test_deletion_closes_live_interval_and_tombstone_is_only_raw_audit(self):
        history = self.record.history
        record_id = self.record.id
        self.record.delete(ignore_permission=True)
        deletion = history.raw.first()
        self.assertEqual(deletion.history_type, "-")
        before = history.filter(history_type="~").first()
        self.assertEqual(
            before._effective_search_date,
            deletion.history_date - timedelta(microseconds=1),
        )
        with self.assertRaises(self.Record.Interface._model.DoesNotExist):
            history.first()
        with self.assertRaises(self.Record.Interface._model.DoesNotExist):
            self.Record(record_id, search_date=deletion.history_date)
        self.assertEqual(
            list(self.Record.filter(id=record_id, search_date=deletion.history_date)),
            [],
        )

    def test_historical_mutations_fail_before_signals_and_direct_interface_dispatch(
        self,
    ):
        """Bound historical writes fail before signals or backend dispatch."""
        historical = self.Record(self.record.id, search_date=self.explicit)
        events = []

        def record_event(sender, **kwargs):
            events.append(kwargs)

        pre_data_change.connect(record_event, weak=False)
        self.addCleanup(pre_data_change.disconnect, record_event)
        for mutate in (
            lambda: historical.update(name="Forbidden", ignore_permission=True),
            lambda: historical.delete(ignore_permission=True),
            lambda: historical._interface.update(name="Forbidden"),
            historical._interface.delete,
        ):
            with (
                self.subTest(mutate=mutate),
                self.assertRaises(HistoricalMutationError),
            ):
                mutate()
        self.assertEqual(events, [])
        self.assertEqual(self.Record(self.record.id).name, "New record")

    def test_equal_timestamp_uses_existing_winner_and_rejects_zero_duration_version(
        self,
    ):
        tied = timezone.now()
        with patch("django.utils.timezone.now", return_value=tied):
            self.record.update(name="First tie", ignore_permission=True)
            self.record.update(name="Second tie", ignore_permission=True)
        self.assertEqual(
            self.Record(self.record.id, search_date=tied).name, "Second tie"
        )
        with self.assertRaises(HistoricalContextConflictError):
            self.record.history.filter(name="First tie").get()
        self.assertEqual(self.record.history.raw.filter(history_date=tied).count(), 2)
