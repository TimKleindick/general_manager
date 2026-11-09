"""Fast search must handle empty native collections without extra relation reads."""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, models
from django.test.utils import CaptureQueriesContext

from general_manager.cache.run_context import (
    CalculationRunContext,
    current_calculation_run_context,
)
from general_manager.bucket.database_bucket import DatabaseBucket
from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.permission import AdditiveManagerPermission
from general_manager.search.backend_registry import configure_search_backend
from general_manager.search.backends.dev import DevSearchBackend
from general_manager.search.config import IndexConfig
from general_manager.search.indexer import SearchIndexer
from general_manager.api.graphql import GraphQL
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestSearchEmptyCollections(GeneralManagerTransactionTestCase):
    """Exercise empty relation probes and their fail-closed fallbacks."""

    @classmethod
    def setUpClass(cls) -> None:
        class EmptyCollectionSource(GeneralManager):
            __module__ = "general_manager.models"

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)
                visibility = models.CharField(max_length=20, default="public")

            class Permission(AdditiveManagerPermission):
                __read__: ClassVar[list[str]] = ["matches:visibility:public"]

        class EmptyCollectionOwner(GeneralManager):
            __module__ = "general_manager.models"

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)
                sources = models.ManyToManyField(
                    "general_manager.EmptyCollectionSource",
                    related_name="empty_collection_owners",
                    blank=True,
                )

            class Permission(AdditiveManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

            class SearchConfig:
                indexes: ClassVar[list[IndexConfig]] = [
                    IndexConfig(
                        name="collections",
                        fields=["name", "source_names", "child_texts"],
                        sorts=["name"],
                    )
                ]
                field_dependencies: ClassVar[dict[str, tuple[str, ...]]] = {
                    "name": ("name",),
                    "source_names": ("sources_list__name",),
                    "child_texts": ("children_list__text",),
                }

                @staticmethod
                def to_document(instance: GeneralManager) -> dict[str, object]:
                    return {
                        "name": instance.name,
                        "source_names": [
                            source.name for source in instance.sources_list
                        ],
                        "child_texts": [child.text for child in instance.children_list],
                    }

        class EmptyCollectionChild(GeneralManager):
            __module__ = "general_manager.models"

            class Interface(DatabaseInterface):
                text = models.CharField(max_length=100)
                visibility = models.CharField(max_length=20, default="public")
                owner = models.ForeignKey(
                    "general_manager.EmptyCollectionOwner",
                    on_delete=models.CASCADE,
                    related_name="children",
                )

            class Permission(AdditiveManagerPermission):
                __read__: ClassVar[list[str]] = ["matches:visibility:public"]

        cls.Source = EmptyCollectionSource
        cls.Owner = EmptyCollectionOwner
        cls.Child = EmptyCollectionChild
        cls.general_manager_classes = [
            EmptyCollectionSource,
            EmptyCollectionOwner,
            EmptyCollectionChild,
        ]
        GeneralManagerMeta.all_classes = cls.general_manager_classes
        super().setUpClass()

    def setUp(self) -> None:
        super().setUp()
        self.backend = DevSearchBackend()
        configure_search_backend(self.backend)
        self.actor = get_user_model()(id=42, username="reader", is_active=True)
        self.info = SimpleNamespace(context=SimpleNamespace(user=self.actor))
        self.resolver = GraphQL._query_fields["search"].resolver

    def tearDown(self) -> None:
        configure_search_backend(None)
        super().tearDown()

    def search(self, **kwargs: object) -> dict[str, object]:
        result = self.resolver(
            None,
            self.info,
            index="collections",
            types=["EmptyCollectionOwner"],
            **kwargs,
        )
        assert isinstance(result, dict)
        return result

    def index(self, owner: GeneralManager) -> None:
        fresh_owner = self.Owner.get(id=owner.id)
        SearchIndexer(self.backend).index_instance(fresh_owner)

    def test_empty_collections_use_one_root_query(self) -> None:
        owner = self.Owner.Factory.create(name="empty needle")
        self.index(owner)

        with (
            CalculationRunContext(),
            CaptureQueriesContext(connection) as queries,
            patch(
                "general_manager.search.projection.projection_source_paths",
                side_effect=AssertionError("Schema already validated these sources"),
            ),
        ):
            result = self.search(query="needle")

        assert result["total"] == 1
        assert [item.id for item in result["results"]] == [owner.id]
        assert len(queries) == 1, list(queries)
        assert "EXISTS" in queries[0]["sql"].upper()

    def test_populated_public_collections_remain_visible(self) -> None:
        source = self.Source.Factory.create(name="visible source", visibility="public")
        owner = self.Owner.Factory.create(name="owner")
        self.Owner.Interface._model.objects.get(pk=owner.id).sources.add(source.id)
        child = self.Child.Factory.create(
            text="visible child", owner=owner, visibility="public"
        )
        self.index(owner)

        result = self.search(query="visible")
        assert result["total"] == 1
        assert [item.id for item in result["results"]] == [owner.id]
        assert child.id in {item.id for item in owner.children_list}

    def test_hidden_related_rows_are_removed_before_count_and_pagination(self) -> None:
        hidden_source = self.Source.Factory.create(
            name="hidden source", visibility="private"
        )
        hidden_source_owner = self.Owner.Factory.create(name="hidden source owner")
        self.Owner.Interface._model.objects.get(pk=hidden_source_owner.id).sources.add(
            hidden_source.id
        )
        hidden_child_owner = self.Owner.Factory.create(name="hidden child owner")
        self.Child.Factory.create(
            text="hidden child", owner=hidden_child_owner, visibility="private"
        )
        visible_owner = self.Owner.Factory.create(name="visible owner")
        self.index(hidden_source_owner)
        self.index(hidden_child_owner)
        self.index(visible_owner)

        result = self.search(
            query="", page=1, page_size=1, order_by=[{"field": "name"}]
        )

        assert result["total"] == 1
        assert [item.id for item in result["results"]] == [visible_owner.id]
        assert result["page_info"]["total_pages"] == 1

    def test_new_hidden_relation_is_authorized_against_current_rows(self) -> None:
        owner = self.Owner.Factory.create(name="owner")
        self.index(owner)

        hidden_child = self.Child.Factory.create(
            text="hidden child", owner=owner, visibility="private"
        )
        assert hidden_child.owner_id == owner.id
        with patch(
            "general_manager.search.indexer._serialize_document",
            side_effect=AssertionError(
                "search must not reserialize the index projection"
            ),
        ):
            assert self.search(query="")["total"] == 0

        self.index(owner)
        with patch(
            "general_manager.search.indexer._serialize_document",
            side_effect=AssertionError(
                "search must not reserialize the index projection"
            ),
        ):
            assert self.search(query="")["total"] == 0

        self.Child.Interface._model.objects.filter(pk=hidden_child.id).update(
            visibility="public"
        )
        # Index freshness is a separate contract. Once the current related row
        # becomes readable, the existing stored document remains a valid hit.
        with patch(
            "general_manager.search.indexer._serialize_document",
            side_effect=AssertionError(
                "search must not reserialize the index projection"
            ),
        ):
            assert self.search(query="")["total"] == 1
        self.index(owner)
        assert self.search(query="")["total"] == 1

    def test_retained_search_result_reloads_relation_after_mutation(self) -> None:
        owner = self.Owner.Factory.create(name="empty owner")
        self.index(owner)

        with CalculationRunContext() as context:
            result = self.search(query="empty")
            retained_owner = result["results"][0]
            generation = context.orm_bucket_invalidation_generation

            child = self.Child.create(
                ignore_permission=True,
                text="new child",
                owner=owner,
                visibility="public",
            )

            assert context.orm_bucket_invalidation_generation > generation

            assert [item.id for item in retained_owner.children_list] == [child.id]

    def test_retained_search_result_reloads_many_to_many_after_direct_update(
        self,
    ) -> None:
        owner = self.Owner.Factory.create(name="empty sources")
        source = self.Source.Factory.create(name="new source", visibility="public")
        self.index(owner)

        with CalculationRunContext() as context:
            retained_owner = self.search(query="sources")["results"][0]
            assert list(retained_owner.sources_list) == []
            generation = context.orm_bucket_invalidation_generation

            self.Owner.get(id=owner.id)._interface.update(sources_id_list=[source.id])

            assert context.orm_bucket_invalidation_generation > generation
            assert [item.id for item in retained_owner.sources_list] == [source.id]

    def test_retained_search_result_reloads_many_to_many_after_manager_update(
        self,
    ) -> None:
        owner = self.Owner.Factory.create(name="empty manager sources")
        source = self.Source.Factory.create(name="new source", visibility="public")
        self.index(owner)

        with CalculationRunContext() as context:
            retained_owner = self.search(query="manager sources")["results"][0]
            assert list(retained_owner.sources_list) == []
            generation = context.orm_bucket_invalidation_generation

            self.Owner.get(id=owner.id).update(
                ignore_permission=True,
                sources_id_list=[source.id],
            )

            assert context.orm_bucket_invalidation_generation > generation
            assert [item.id for item in retained_owner.sources_list] == [source.id]

    def test_cached_row_probe_does_not_cross_mutation_generation(self) -> None:
        first = self.Owner.Factory.create(name="first")
        second = self.Owner.Factory.create(name="second")
        source = self.Source.Factory.create(name="new source", visibility="public")
        original_hydrate = self.Owner._from_trusted_orm_instance
        mutated = False

        def hydrate(
            cls: type[GeneralManager],
            instance: models.Model,
            *,
            search_date: object = None,
        ) -> GeneralManager:
            nonlocal mutated
            if not mutated:
                self.Owner.Interface._model.objects.get(pk=second.id).sources.add(
                    source.id
                )
                context = current_calculation_run_context()
                assert context is not None
                context.clear_orm_bucket_results()
                mutated = True
            return original_hydrate(instance, search_date=search_date)

        with CalculationRunContext():
            bucket = self.Owner.filter(id__in=[first.id, second.id])
            bucket = bucket._with_empty_relation_probes(["sources_list"])
            with patch.object(
                self.Owner,
                "_from_trusted_orm_instance",
                classmethod(hydrate),
            ):
                result = list(bucket)

            second_result = next(item for item in result if item.id == second.id)
            assert [item.id for item in second_result.sources_list] == [source.id]

            # Django retains the over-cap queryset's rows. Their witness must
            # stay tied to the first iterator's snapshot generation rather than
            # being rebound by the later iterator after the mutation.
            repeated_result = list(bucket)
            repeated_second = next(
                item for item in repeated_result if item.id == second.id
            )
            assert [item.id for item in repeated_second.sources_list] == [source.id]

    def test_uncached_row_probe_does_not_cross_mutation_generation(self) -> None:
        first = self.Owner.Factory.create(name="first")
        second = self.Owner.Factory.create(name="second")
        source = self.Source.Factory.create(name="new source", visibility="public")
        original_hydrate = self.Owner._from_trusted_orm_instance
        mutated = False

        def hydrate(
            cls: type[GeneralManager],
            instance: models.Model,
            *,
            search_date: object = None,
        ) -> GeneralManager:
            nonlocal mutated
            if not mutated:
                self.Owner.Interface._model.objects.get(pk=second.id).sources.add(
                    source.id
                )
                context = current_calculation_run_context()
                assert context is not None
                context.clear_orm_bucket_results()
                mutated = True
            return original_hydrate(instance, search_date=search_date)

        with (
            CalculationRunContext(),
            patch(
                "general_manager.bucket.database_bucket."
                "MAX_RUN_SCOPED_BUCKET_RESULT_ROWS",
                1,
            ),
        ):
            bucket = self.Owner.filter(id__in=[first.id, second.id])
            bucket = bucket._with_empty_relation_probes(["sources_list"])
            with patch.object(
                self.Owner,
                "_from_trusted_orm_instance",
                classmethod(hydrate),
            ):
                result = list(bucket)

            second_result = next(item for item in result if item.id == second.id)
            assert [item.id for item in second_result.sources_list] == [source.id]

            # The direct iterator populated Django's queryset cache before the
            # mutation. A later iteration must keep the old row provenance
            # instead of binding its stale EXISTS annotation to the new run
            # generation.
            repeated_result = list(bucket)
            repeated_second = next(
                item for item in repeated_result if item.id == second.id
            )
            assert [item.id for item in repeated_second.sources_list] == [source.id]

    def test_uncached_queryset_rows_keep_original_probe_generation(self) -> None:
        first = self.Owner.Factory.create(name="first")
        second = self.Owner.Factory.create(name="second")
        source = self.Source.Factory.create(name="new source", visibility="public")

        with (
            CalculationRunContext() as context,
            patch(
                "general_manager.bucket.database_bucket."
                "MAX_RUN_SCOPED_BUCKET_RESULT_ROWS",
                1,
            ),
        ):
            bucket = self.Owner.filter(id__in=[first.id, second.id])
            bucket = bucket._with_empty_relation_probes(["sources_list"])
            initial = list(bucket)
            initial_second = next(item for item in initial if item.id == second.id)
            assert list(initial_second.sources_list) == []

            self.Owner.Interface._model.objects.get(pk=second.id).sources.add(source.id)
            context.clear_orm_bucket_results()

            result = list(bucket)
            second_result = next(item for item in result if item.id == second.id)
            assert [item.id for item in second_result.sources_list] == [source.id]

    def test_uncached_queryset_rows_do_not_cross_context_lifetimes(self) -> None:
        first = self.Owner.Factory.create(name="first")
        second = self.Owner.Factory.create(name="second")
        source = self.Source.Factory.create(name="new source", visibility="public")

        with patch(
            "general_manager.bucket.database_bucket.MAX_RUN_SCOPED_BUCKET_RESULT_ROWS",
            1,
        ):
            bucket = self.Owner.filter(id__in=[first.id, second.id])
            bucket = bucket._with_empty_relation_probes(["sources_list"])
            with CalculationRunContext():
                initial = list(bucket)
                initial_second = next(item for item in initial if item.id == second.id)
                assert list(initial_second.sources_list) == []

            self.Owner.Interface._model.objects.get(pk=second.id).sources.add(source.id)

            # Both runs begin at invalidation generation zero. Only a witness
            # bound to the original context lifetime can distinguish the stale
            # queryset rows from a new SQL snapshot.
            with CalculationRunContext():
                result = list(bucket)
                second_result = next(item for item in result if item.id == second.id)
                assert [item.id for item in second_result.sources_list] == [source.id]

    def test_uncached_queryset_rows_do_not_cross_context_reentry(self) -> None:
        first = self.Owner.Factory.create(name="first")
        second = self.Owner.Factory.create(name="second")
        source = self.Source.Factory.create(name="new source", visibility="public")
        context = CalculationRunContext()

        with patch(
            "general_manager.bucket.database_bucket.MAX_RUN_SCOPED_BUCKET_RESULT_ROWS",
            1,
        ):
            bucket = self.Owner.filter(id__in=[first.id, second.id])
            bucket = bucket._with_empty_relation_probes(["sources_list"])
            with context:
                initial = list(bucket)
                initial_second = next(item for item in initial if item.id == second.id)
                assert list(initial_second.sources_list) == []

            self.Owner.Interface._model.objects.get(pk=second.id).sources.add(source.id)

            with context:
                result = list(bucket)
                second_result = next(item for item in result if item.id == second.id)
                assert [item.id for item in second_result.sources_list] == [source.id]

    def test_uncached_queryset_rows_from_outside_context_stay_untrusted(self) -> None:
        first = self.Owner.Factory.create(name="first")
        second = self.Owner.Factory.create(name="second")
        source = self.Source.Factory.create(name="new source", visibility="public")

        with patch(
            "general_manager.bucket.database_bucket.MAX_RUN_SCOPED_BUCKET_RESULT_ROWS",
            1,
        ):
            bucket = self.Owner.filter(id__in=[first.id, second.id])
            bucket = bucket._with_empty_relation_probes(["sources_list"])
            initial = list(bucket)
            initial_second = next(item for item in initial if item.id == second.id)
            assert list(initial_second.sources_list) == []

            self.Owner.Interface._model.objects.get(pk=second.id).sources.add(source.id)

            with CalculationRunContext():
                result = list(bucket)
                second_result = next(item for item in result if item.id == second.id)
                assert [item.id for item in second_result.sources_list] == [source.id]

    def test_count_uses_retained_rows_after_primary_key_eviction(self) -> None:
        self.Owner.Factory.create(name="first")
        self.Owner.Factory.create(name="second")

        with CalculationRunContext() as context:
            bucket = self.Owner.filter()
            assert len(list(bucket)) == 2
            context.discard_prefix(("orm_bucket_result",))
            with CaptureQueriesContext(connection) as queries:
                assert bucket.count() == 2

        assert len(queries) == 0

    def test_retained_search_result_expires_hint_after_context_exit(self) -> None:
        owner = self.Owner.Factory.create(name="context owner")
        self.index(owner)

        with CalculationRunContext():
            retained_owner = self.search(query="context")["results"][0]

        child = self.Child.create(
            ignore_permission=True,
            text="new child",
            owner=owner,
            visibility="public",
        )

        assert [item.id for item in retained_owner.children_list] == [child.id]

    def test_retained_result_without_probe_reloads_relation_after_mutation(
        self,
    ) -> None:
        owner = self.Owner.Factory.create(name="ordinary owner")
        self.index(owner)

        with (
            CalculationRunContext(),
            patch.object(
                DatabaseBucket,
                "_with_empty_relation_probes",
                autospec=True,
                side_effect=lambda bucket, _attributes: bucket,
            ),
        ):
            result = self.search(query="ordinary")
            retained_owner = result["results"][0]
            child = self.Child.create(
                ignore_permission=True,
                text="new child",
                owner=owner,
                visibility="public",
            )

            assert [item.id for item in retained_owner.children_list] == [child.id]

    def test_descriptor_caches_ordinary_none_value(self) -> None:
        owner = self.Owner.Factory.create(name="owner")
        calls = 0

        def none_value(_interface: object) -> None:
            nonlocal calls
            calls += 1
            return None

        with patch.dict(self.Owner._attributes, {"name": none_value}):
            assert owner.name is None
            assert owner.name is None

        assert calls == 1

    def test_custom_child_filter_is_not_replaced_by_empty_probe(self) -> None:
        owner = self.Owner.Factory.create(name="owner")
        unrelated_owner = self.Owner.Factory.create(name="unrelated owner")
        self.Child.Factory.create(
            text="off relation", owner=unrelated_owner, visibility="public"
        )
        original_filter = self.Child.filter

        def all_children(cls: type[GeneralManager], **kwargs: object) -> object:
            del kwargs
            return original_filter()

        with patch.object(self.Child, "filter", classmethod(all_children)):
            self.index(owner)
            result = self.search(query="off relation")

        assert result["total"] == 1
