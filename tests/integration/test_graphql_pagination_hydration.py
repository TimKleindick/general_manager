"""Paged native lists count matches without hydrating the complete result twice."""

from datetime import UTC, datetime
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.db import models

from general_manager.api import graphql_resolvers
from general_manager.api.graphql_resolvers import create_list_resolver
from general_manager.as_of import HistoricalContextConflictError, as_of
from general_manager.bucket.database_bucket import DatabaseBucket
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.run_context import CalculationRunContext
from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.permission import OverrideManagerPermission
from general_manager.utils.testing import GeneralManagerTransactionTestCase


@contextmanager
def record_hydrations(model: type[models.Model]) -> Iterator[list[models.Model]]:
    """Observe real loads while preserving Django's classmethod protocol."""
    hydrated: list[models.Model] = []
    original = model.from_db.__func__

    def from_db(cls, *args, **kwargs):
        instance = original(cls, *args, **kwargs)
        hydrated.append(instance)
        return instance

    # Django 6.1 inspects from_db.__func__ before choosing a fetch strategy.
    with patch.object(model, "from_db", classmethod(from_db)):
        yield hydrated


class TestGraphQLPaginationHydration(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class PaginationHydrationRecord(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=30)
                rank = models.IntegerField()

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

        cls.Record = PaginationHydrationRecord
        cls.general_manager_classes = [PaginationHydrationRecord]
        super().setUpClass()

    def setUp(self) -> None:
        """Create twelve rows spanning the matching and excluded operand domains."""
        super().setUp()
        self.model = self.Record.Interface._model
        self.model.objects.bulk_create(
            [
                self.model(name="match" if rank < 10 else "outside", rank=rank)
                for rank in range(12)
            ]
        )
        self.info = SimpleNamespace(context=SimpleNamespace(user=AnonymousUser()))
        self.resolver = create_list_resolver(lambda *_: self.Record.all(), self.Record)

    def test_paged_public_list_hydrates_only_the_requested_page(self) -> None:
        """Definitive native field rights keep the full matching domain lazy."""
        with (
            CalculationRunContext(),
            record_hydrations(self.model) as hydrated,
        ):
            result = self.resolver(
                None,
                self.info,
                filter={"name": "match"},
                order_by=[{"field": "rank", "direction": "DESC"}],
                page=2,
                page_size=3,
            )

        assert [item.rank for item in result["items"]] == [6, 5, 4]
        assert result["pageInfo"] == {
            "total_count": 10,
            "page_size": 3,
            "current_page": 2,
            "total_pages": 4,
        }
        assert len(hydrated) == 3

    def test_denied_nonmatch_or_offpage_row_is_excluded_before_count(self) -> None:
        """A matching denial changes totals before paging; a nonmatch does not."""
        for denied_rank in (0, 11):
            with self.subTest(denied_rank=denied_rank):
                seen = []

                def check(
                    instance, _info, field, *, seen=seen, denied_rank=denied_rank
                ):
                    seen.append(instance.rank)
                    return instance.rank != denied_rank

                with (
                    CalculationRunContext(),
                    patch.object(
                        graphql_resolvers, "check_read_permission", side_effect=check
                    ),
                ):
                    result = self.resolver(
                        None,
                        self.info,
                        filter={"name": "match"},
                        order_by=[{"field": "rank", "direction": "DESC"}],
                        page=1,
                        page_size=3,
                    )
                assert set(seen) == set(range(10))
                assert [item.rank for item in result["items"]] == [9, 8, 7]
                assert result["pageInfo"]["total_count"] == (
                    9 if denied_rank == 0 else 10
                )

    def test_offpage_callback_error_remains_an_error(self) -> None:
        failure = RuntimeError("offpage callback failed")

        def check(instance, _info, _field):
            if instance.rank == 0:
                raise failure
            return True

        with (
            patch.object(graphql_resolvers, "check_read_permission", side_effect=check),
            self.assertRaises(RuntimeError) as observed,
        ):
            self.resolver(
                None,
                self.info,
                filter={"name": "match"},
                order_by=[{"field": "rank", "direction": "DESC"}],
                page=1,
                page_size=3,
            )
        assert observed.exception is failure

    def test_unpaged_list_hydrates_the_matching_domain_once(self) -> None:
        """Omitting pagination must return and hydrate the complete filtered domain."""
        with (
            CalculationRunContext(),
            record_hydrations(self.model) as hydrated,
        ):
            result = self.resolver(
                None,
                self.info,
                filter={"name": "match"},
                order_by=[{"field": "rank", "direction": "DESC"}],
            )

        assert [item.rank for item in result["items"]] == list(range(9, -1, -1))
        assert result["pageInfo"] == {
            "total_count": 10,
            "page_size": None,
            "current_page": 1,
            "total_pages": 1,
        }
        assert len(hydrated) == 10

    def test_custom_count_and_length_remain_authoritative(self) -> None:
        """Custom count, length, and dynamic count hooks must control page totals."""

        class CustomCount(DatabaseBucket):
            def count(self) -> int:
                return 31

        class CustomLength(DatabaseBucket):
            def __len__(self) -> int:
                return 41

        class DynamicCount(DatabaseBucket):
            def __getattribute__(self, name):
                """Expose a dynamic count hook to exercise the custom-count fallback."""
                if name == "count":
                    return lambda: 51
                return super().__getattribute__(name)

        for bucket_class, expected_count in (
            (CustomCount, 31),
            (CustomLength, 41),
            (DynamicCount, 51),
        ):
            with self.subTest(bucket_class=bucket_class.__name__):
                source = bucket_class(self.model.objects.all(), self.Record)
                resolver = create_list_resolver(
                    lambda *_, source=source: source, self.Record
                )
                with CalculationRunContext():
                    result = resolver(
                        None,
                        self.info,
                        filter={"name": "match"},
                        order_by=[{"field": "rank", "direction": "DESC"}],
                        page=1,
                        page_size=3,
                    )
                assert result["pageInfo"]["total_count"] == expected_count
                assert [item.rank for item in result["items"]] == [9, 8, 7]

    def test_page_count_reuses_exact_snapshots_and_honors_invalidation(self) -> None:
        """Reuse exact row or key snapshots until explicit context invalidation."""
        source = self.Record.filter(name="match")
        with CalculationRunContext() as context:
            tuple(source)
            with self.assertNumQueries(0), DependencyTracker() as dependencies:
                assert source._count_for_page() == 10
            assert any(item[1] == "filter" for item in dependencies)

            # A primary-key-only snapshot is sufficient to answer the count.
            context.clear_orm_bucket_results()
            source._get_run_scoped_primary_keys()
            with self.assertNumQueries(0):
                assert source._count_for_page() == 10

            self.model.objects.filter(rank=0).update(name="outside")
            context.clear_orm_bucket_results()
            with self.assertNumQueries(1):
                assert source._count_for_page() == 9

    def test_instance_count_override_remains_authoritative(self) -> None:
        source = self.Record.all()
        resolver = create_list_resolver(lambda *_: source, self.Record)
        with patch.object(source, "count", return_value=31) as count:
            result = resolver(None, self.info, page=1, page_size=3)
        count.assert_called_once_with()
        assert result["pageInfo"]["total_count"] == 31
        assert len(result["items"]) == 3

    def test_page_count_rejects_incompatible_historical_context(self) -> None:
        source = self.Record.all()
        with (
            as_of(datetime(2025, 1, 1, tzinfo=UTC)),
            self.assertNumQueries(0),
            self.assertRaises(HistoricalContextConflictError),
        ):
            source._count_for_page()
