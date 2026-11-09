"""Backend-first search must preserve native membership and related read gates."""

from types import SimpleNamespace
from typing import ClassVar, cast
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import connection, models
from django.test.utils import CaptureQueriesContext
from django.test import override_settings
import graphene
from graphql import GraphQLError

from general_manager.api.graphql import GraphQL
from general_manager.api.graphql_query_permissions import authorize_query_fields
from general_manager.api.property import graph_ql_property
from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.permission import AdditiveManagerPermission
from general_manager.search.backend_registry import configure_search_backend
from general_manager.search.backends.dev import DevSearchBackend
from general_manager.search.backend import SearchDocument, SearchHit, SearchResult
from general_manager.search.config import IndexConfig
from general_manager.search.indexer import SearchIndexer
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestSearchPermissionRouting(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class RouteRecord(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)
                visibility = models.CharField(max_length=20, default="public")
                restricted = models.CharField(max_length=100, default="hidden")

            class Permission(AdditiveManagerPermission):
                __read__: ClassVar[list[str]] = [
                    "isAuthenticated&matches:visibility:public"
                ]
                restricted: ClassVar[dict] = {"read": ["isAdmin"]}

            class SearchConfig:
                indexes: ClassVar[list] = [
                    IndexConfig(
                        "global", ["name"], filters=["visibility"], sorts=["name"]
                    ),
                    IndexConfig("restricted", ["restricted"]),
                ]

        class RouteRelated(GeneralManager):
            class Interface(DatabaseInterface):
                text = models.CharField(max_length=100)
                visibility = models.CharField(max_length=20, default="public")

            class Permission(AdditiveManagerPermission):
                __read__: ClassVar[list[str]] = ["matches:visibility:public"]

        class RouteOwner(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)
                related = models.ForeignKey(
                    "general_manager.RouteRelated",
                    on_delete=models.CASCADE,
                    null=True,
                    blank=True,
                )

            class Permission(AdditiveManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

            class SearchConfig:
                indexes: ClassVar[list] = [
                    IndexConfig(
                        "relations", ["name", "related__text"], sorts=["related__text"]
                    ),
                    IndexConfig("relation_identity", ["related_id"]),
                    IndexConfig("relation_value", ["related"]),
                ]

        class RouteDerived(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)
                restricted = models.CharField(max_length=100)

            class Permission(AdditiveManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]
                restricted: ClassVar[dict] = {"read": ["isAdmin"]}

            @graph_ql_property
            def label(self) -> str:
                return self.restricted

            class SearchConfig:
                indexes: ClassVar[list] = [IndexConfig("derived", ["label"])]
                to_document = staticmethod(lambda instance: {"label": instance.label})
                field_dependencies: ClassVar[dict] = {"label": ("label", "restricted")}

        cls.Record = RouteRecord
        cls.Related = RouteRelated
        cls.Owner = RouteOwner
        cls.Derived = RouteDerived
        cls.general_manager_classes = [
            RouteRecord,
            RouteRelated,
            RouteOwner,
            RouteDerived,
        ]

    def setUp(self):
        super().setUp()
        self.backend = DevSearchBackend()
        configure_search_backend(self.backend)
        self.actor = get_user_model()(id=42, username="reader", is_active=True)
        self.info = SimpleNamespace(context=SimpleNamespace(user=self.actor))
        self.resolver = GraphQL._query_fields["search"].resolver

    def tearDown(self):
        configure_search_backend(None)
        super().tearDown()

    def search(self, *, index="global", types=None, **kwargs):
        """Resolve the global search field for the test actor."""
        return self.resolver(
            None, self.info, index=index, types=types or ["RouteRecord"], **kwargs
        )

    def test_native_fast_path_skips_domain_field_scan_and_keeps_output_checks(self):
        """Fast search filters the source domain while retaining output checks."""
        for name, visibility in [
            ("Alpha", "public"),
            ("Beta", "private"),
            ("Gamma", "public"),
        ]:
            self.Record.Factory.create(name=name, visibility=visibility)
        SearchIndexer(self.backend).reindex_manager(self.Record)
        with patch(
            "general_manager.api.graphql_query_permissions.authorize_query_fields",
            wraps=authorize_query_fields,
        ) as authorize:
            result = self.search(query="")
        authorize.assert_not_called()
        assert [item.name for item in result["results"]] == ["Alpha", "Gamma"]
        assert result["total"] == 2
        payload = graphene.Schema(query=GraphQL._query_class).execute(
            '{ search(query: "", types: ["RouteRecord"]) { results { ... on RouteRecordType { name restricted } } } }',
            context_value=self.info.context,
        )
        assert payload.errors is None
        assert all(
            item["restricted"] is None for item in payload.data["search"]["results"]
        )
        self.info.context.user = AnonymousUser()
        assert self.search(query="")["total"] == 0

    def test_restricted_index_checks_nonhits_before_backend(self):
        self.Record.Factory.create(
            name="ordinary", restricted="classified", visibility="public"
        )
        with patch.object(self.backend, "search", wraps=self.backend.search) as backend:
            with self.assertRaisesRegex(GraphQLError, "Permission denied"):
                self.search(index="restricted", query="absent")
        backend.assert_not_called()

    def test_mutated_index_lists_cannot_reuse_startup_fast_approval(self):
        """Runtime index mutations invalidate the startup fast-path proof."""
        self.Record.Factory.create(name="ordinary", visibility="public")
        index = self.Record.SearchConfig.indexes[0]
        for key in ("fields", "filters", "sorts"):
            with self.subTest(collection=key):
                values = getattr(index, key)
                values.append("restricted")
                try:
                    with patch.object(
                        self.backend, "search", wraps=self.backend.search
                    ) as backend:
                        with self.assertRaisesRegex(GraphQLError, "Permission denied"):
                            self.search(query="absent")
                    backend.assert_not_called()
                finally:
                    values.pop()

    def test_declared_property_dependency_keeps_full_source_check(self):
        self.Derived.Factory.create(name="ordinary", restricted="classified")
        with patch.object(self.backend, "search", wraps=self.backend.search) as backend:
            with self.assertRaisesRegex(GraphQLError, "Permission denied"):
                self.search(index="derived", types=["RouteDerived"], query="absent")
        backend.assert_not_called()

    def test_sparse_query_over_large_source_only_hydrates_backend_candidates(self):
        """Sparse backend matches hydrate only the candidate primary keys."""
        model = self.Record.Interface._model
        model.objects.bulk_create(
            [
                model(
                    name=f"ordinary-{number}", visibility="public", restricted="hidden"
                )
                for number in range(1005)
            ]
        )
        match = self.Record.Factory.create(name="needle", visibility="public")
        SearchIndexer(self.backend).index_instance(match)
        with CaptureQueriesContext(connection) as captured:
            result = self.search(query="needle")
        assert result["total"] == 1
        assert [item.id for item in result["results"]] == [match.id]
        assert len(captured) == 1, list(captured)
        assert " IN " in captured[0]["sql"]

    def test_empty_backend_does_not_scan_database(self):
        self.Record.Factory.create(name="ordinary", visibility="public")
        with CaptureQueriesContext(connection) as captured:
            result = self.search(query="absent")
        assert result["results"] == []
        assert result["total"] == 0
        assert len(captured) == 0

    def test_native_source_restriction_and_stale_index_ids_are_retained(self):
        """Native source restrictions exclude stale and out-of-domain IDs."""
        visible = self.Record.Factory.create(name="visible", visibility="public")
        outside = self.Record.Factory.create(name="outside", visibility="public")
        stale = self.Record.Factory.create(name="stale", visibility="public")
        SearchIndexer(self.backend).reindex_manager(self.Record)
        self.Record.Interface._model.objects.filter(pk=stale.id).delete()
        source = self.Record.all().exclude(id=outside.id)
        with patch.object(self.Record, "all", return_value=source):
            result = self.search(query="")
        assert [item.id for item in result["results"]] == [visible.id]
        assert result["total"] == 1

    def test_hidden_related_text_cannot_affect_results_count_sort_or_pages(self):
        """Unreadable related text cannot affect count, order, or pagination."""
        owners = []
        for text, visibility in [
            ("A", "public"),
            ("hidden-needle", "private"),
            ("C", "public"),
            ("D", "public"),
        ]:
            related = self.Related.Factory.create(text=text, visibility=visibility)
            owners.append(self.Owner.Factory.create(name="owner", related=related))
        indexer = SearchIndexer(self.backend)
        indexer.reindex_manager(self.Owner)
        for hidden_value in ["000-hidden-needle", "zzz-hidden-needle"]:
            model = self.Related.Interface._model
            model.objects.filter(pk=owners[1].related.id).update(text=hidden_value)
            indexer.index_instance(self.Owner(id=owners[1].id))
            for page, expected in [
                (1, [owners[0].id, owners[2].id]),
                (2, [owners[3].id]),
                (3, []),
            ]:
                result = self.search(
                    index="relations",
                    types=["RouteOwner"],
                    query="",
                    page=page,
                    page_size=2,
                    order_by=[{"field": "related__text", "direction": "ASC"}],
                )
                assert [item.id for item in result["results"]] == expected
                assert result["total"] == 3
                assert result["page_info"]["total_pages"] == 2
            assert (
                self.search(
                    index="relations", types=["RouteOwner"], query="hidden-needle"
                )["total"]
                == 0
            )

    def test_related_permissions_are_reapplied_on_later_requests(self):
        related = self.Related.Factory.create(text="needle", visibility="public")
        self.Owner.Factory.create(name="owner", related=related)
        SearchIndexer(self.backend).reindex_manager(self.Owner)
        assert (
            self.search(index="relations", types=["RouteOwner"], query="needle")[
                "total"
            ]
            == 1
        )
        self.Related.Interface._model.objects.filter(pk=related.id).update(
            visibility="private"
        )
        assert (
            self.search(index="relations", types=["RouteOwner"], query="needle")[
                "total"
            ]
            == 0
        )

    def test_fast_path_relation_authorization_shares_one_resolver_budget(self):
        """Relation authorization consumes one candidate budget across backend pages."""
        for text in ("first", "second"):
            related = self.Related.Factory.create(text=text, visibility="public")
            self.Owner.Factory.create(name="owner", related=related)
        SearchIndexer(self.backend).reindex_manager(self.Owner)

        with (
            patch(
                "general_manager.api.graphql_query_permissions.MAX_QUERY_PERMISSION_CANDIDATES",
                3,
            ),
            patch.object(self.backend, "search", wraps=self.backend.search) as search,
            self.assertRaisesMessage(GraphQLError, "candidate limit"),
        ):
            self.search(
                index="relations",
                types=["RouteOwner"],
                query="",
                page=1,
                page_size=1,
            )
        assert search.call_count == 2

    def test_nullable_related_source_remains_searchable(self):
        """Rows with a null relation remain searchable through the native source."""
        owner = self.Owner.Factory.create(name="needle", related=None)
        SearchIndexer(self.backend).index_instance(owner)
        result = self.search(index="relations", types=["RouteOwner"], query="needle")
        assert [item.id for item in result["results"]] == [owner.id]

    @override_settings(GENERAL_MANAGER={"GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT": 2})
    def test_bounded_scan_never_exposes_a_hidden_dependent_prefix(self):
        """Bounded scans reject prefixes whose hidden relation values can change."""
        for text in ("A", "C"):
            related = self.Related.Factory.create(text=text, visibility="public")
            self.Owner.Factory.create(name="owner", related=related)
        hidden = self.Related.Factory.create(text="000", visibility="private")
        owner = self.Owner.Factory.create(name="owner", related=hidden)
        indexer = SearchIndexer(self.backend)
        indexer.reindex_manager(self.Owner)
        for value in ("000", "zzz"):
            self.Related.Interface._model.objects.filter(pk=hidden.id).update(
                text=value
            )
            indexer.index_instance(self.Owner(id=owner.id))
            for mode in ("exact", "bounded"):
                with self.assertRaises(GraphQLError) as raised:
                    self.search(
                        index="relations",
                        types=["RouteOwner"],
                        query="",
                        page_size=2,
                        total_mode=mode,
                        order_by=[{"field": "related__text", "direction": "ASC"}],
                    )
                assert (
                    raised.exception.extensions["code"] == "SEARCH_SCAN_LIMIT_EXCEEDED"
                )

    def test_separate_index_maintenance_removes_replaced_private_relation(self):
        hidden = self.Related.Factory.create(
            text="private-needle", visibility="private"
        )
        visible = self.Related.Factory.create(text="ordinary", visibility="public")
        owner = self.Owner.Factory.create(name="owner", related=hidden)
        indexer = SearchIndexer(self.backend)
        indexer.index_instance(owner)
        self.Owner.Interface._model.objects.filter(pk=owner.id).update(
            related_id=visible.id
        )
        # Index maintenance owns freshness; searching never reserializes rows.
        indexer.index_instance(self.Owner(id=owner.id))
        with patch(
            "general_manager.search.indexer._serialize_document",
            side_effect=AssertionError("Search must not rebuild index documents"),
        ):
            result = self.search(
                index="relations", types=["RouteOwner"], query="private-needle"
            )
        assert result["total"] == 0
        assert result["results"] == []
        assert (
            self.search(index="relations", types=["RouteOwner"], query="ordinary")[
                "total"
            ]
            == 1
        )

    def test_native_relation_id_checks_target_on_thorough_path(self):
        hidden = self.Related.Factory.create(text="private", visibility="private")
        owner = self.Owner.Factory.create(name="owner", related=hidden)
        SearchIndexer(self.backend).index_instance(owner)
        with patch.object(self.backend, "search", wraps=self.backend.search) as backend:
            with self.assertRaisesRegex(GraphQLError, "traverse query relation"):
                self.search(index="relation_identity", types=["RouteOwner"], query="")
        backend.assert_not_called()

    def test_terminal_relation_value_checks_target_before_counting(self):
        hidden = self.Related.Factory.create(text="private", visibility="private")
        owner = self.Owner.Factory.create(name="owner", related=hidden)
        SearchIndexer(self.backend).index_instance(owner)
        assert (
            self.search(index="relation_value", types=["RouteOwner"], query="")["total"]
            == 0
        )

    def test_native_property_declaration_cannot_remove_its_own_field_gate(self):
        self.Derived.Factory.create(name="ordinary", restricted="classified")
        from general_manager.search.projection import native_search_source_paths
        from general_manager.search.config import SearchConfigSpec

        config = SearchConfigSpec(
            indexes=(IndexConfig("native", ["label"]),),
            field_dependencies={"label": ("restricted",)},
        )
        assert native_search_source_paths(self.Derived, config, ["label"]) == (
            "label",
            "restricted",
        )

    def test_unknown_native_property_dependencies_fail_before_backend(self):
        from general_manager.api.graphql_search import register_search_query

        with (
            patch.object(self.Derived.SearchConfig, "to_document", None),
            patch.object(self.Derived.SearchConfig, "field_dependencies", None),
        ):
            fields = {}
            register_search_query(
                fields,
                GraphQL.manager_registry,
                GraphQL.graphql_type_registry,
                None,
                None,
            )
            with patch.object(
                self.backend, "search", wraps=self.backend.search
            ) as backend:
                with self.assertRaisesRegex(
                    GraphQLError, "complete field_dependencies"
                ):
                    fields["search"].resolver(
                        None,
                        self.info,
                        index="derived",
                        types=["RouteDerived"],
                        query="absent",
                    )
        backend.assert_not_called()

    def test_removed_index_field_cannot_match_or_rank_after_schema_rebuild(self):
        """Stored fields removed from the rebuilt schema cannot match or rank."""
        record = self.Record.Factory.create(name="ordinary", visibility="public")
        self.backend.upsert(
            "global",
            [
                SearchDocument(
                    id="old-schema",
                    type="RouteRecord",
                    identification=record.identification,
                    index="global",
                    data={
                        "name": "ordinary",
                        "visibility": "public",
                        "restricted": "private-needle",
                    },
                    field_boosts={},
                )
            ],
        )
        for query in ("private-needle", "ordinary", ""):
            result = self.search(query=query)
            assert result["total"] == 0
            assert result["results"] == []

    def test_backend_without_stored_projection_cannot_release_matches(self):
        record = self.Record.Factory.create(name="ordinary", visibility="public")
        result = SearchResult(
            hits=[
                SearchHit(
                    id="missing-data",
                    type="RouteRecord",
                    identification=record.identification,
                )
            ],
            total=1,
        )
        with patch.object(self.backend, "search", return_value=result):
            with self.assertRaisesRegex(GraphQLError, "complete stored data"):
                self.search(query="private-needle")

    def test_malformed_fast_path_identifications_are_discarded(self):
        """Malformed native IDs are discarded before source hydration."""
        self.Record.Factory.create(name="ordinary", visibility="public")
        expected_data = {"name": "ordinary", "visibility": "public"}
        for malformed_identification in (
            True,
            {"id": True},
            {"id": "not-an-integer"},
        ):
            with self.subTest(identification=malformed_identification):
                result = SearchResult(
                    hits=[
                        SearchHit(
                            id="malformed",
                            type="RouteRecord",
                            identification=cast(
                                dict[str, object], malformed_identification
                            ),
                            data=expected_data,
                        )
                    ],
                    total=1,
                )
                with patch.object(self.backend, "search", return_value=result):
                    response = self.search(query="ordinary")

                assert response["results"] == []
                assert response["total"] == 0

    def test_custom_identity_converter_errors_remain_observable(self):
        """Application identity converter errors remain visible to callers."""
        self.Record.Factory.create(name="ordinary", visibility="public")
        result = SearchResult(
            hits=[
                SearchHit(
                    id="custom-converter-error",
                    type="RouteRecord",
                    identification={"id": "not-an-integer"},
                    data={"name": "ordinary", "visibility": "public"},
                )
            ],
            total=1,
        )
        with patch(
            "general_manager.api.graphql_search._identity_filter_converter",
            side_effect=ValueError("custom converter failed"),
        ):
            with self.assertRaisesRegex(ValueError, "custom converter failed"):
                with patch.object(self.backend, "search", return_value=result):
                    self.search(query="ordinary")

    def test_identity_metadata_errors_remain_observable(self):
        """Identity metadata provider errors are not mistaken for bad IDs."""
        record = self.Record.Factory.create(name="ordinary", visibility="public")
        result = SearchResult(
            hits=[
                SearchHit(
                    id="metadata-error",
                    type="RouteRecord",
                    identification={"id": str(record.id)},
                    data={"name": "ordinary", "visibility": "public"},
                )
            ],
            total=1,
        )

        class RaisingIdentityMetadata(dict):
            """Raise only when identity metadata is selected for conversion."""

            class MetadataUnavailableError(ValueError):
                """Signal that identity metadata could not be resolved."""

            def get(self, key, default=None):
                """Raise for identity metadata while preserving other lookups."""
                if key == "id":
                    raise self.MetadataUnavailableError
                return super().get(key, default)

        metadata = RaisingIdentityMetadata(self.Record.Interface.get_attribute_types())
        with patch.object(
            self.Record.Interface, "get_attribute_types", return_value=metadata
        ):
            with self.assertRaises(RaisingIdentityMetadata.MetadataUnavailableError):
                with patch.object(self.backend, "search", return_value=result):
                    self.search(query="ordinary")

    def test_custom_input_cast_and_normalize_errors_remain_observable(self):
        """Input cast and normalization callbacks retain their error semantics."""
        self.Record.Factory.create(name="ordinary", visibility="public")
        result = SearchResult(
            hits=[
                SearchHit(
                    id="custom-input-error",
                    type="RouteRecord",
                    identification={"id": "12"},
                    data={"name": "ordinary", "visibility": "public"},
                )
            ],
            total=1,
        )
        input_field = self.Record.Interface.input_fields["id"]

        class CustomCastError(ValueError):
            """Signal that the application-owned cast callback was invoked."""

        class CustomNormalizeError(ValueError):
            """Signal that the application-owned normalizer was invoked."""

        def raising_cast(value, *args, **kwargs):
            raise CustomCastError

        def raising_normalize(value, *args, **kwargs):
            raise CustomNormalizeError

        for attribute, callback, error_type in (
            ("cast", raising_cast, CustomCastError),
            ("normalize", raising_normalize, CustomNormalizeError),
        ):
            with self.subTest(attribute=attribute):
                with patch.object(input_field, attribute, callback):
                    with self.assertRaises(error_type):
                        with patch.object(self.backend, "search", return_value=result):
                            self.search(query="ordinary")

    def test_replaced_native_descriptor_requires_complete_dependencies(self):
        from general_manager.api.graphql_search import register_search_query

        replacement = property(lambda instance: instance.restricted)
        with patch.object(self.Record, "name", replacement):
            fields = {}
            register_search_query(
                fields,
                GraphQL.manager_registry,
                GraphQL.graphql_type_registry,
                None,
                None,
            )
            with patch.object(
                self.backend, "search", wraps=self.backend.search
            ) as backend:
                with self.assertRaisesRegex(
                    GraphQLError, "complete field_dependencies"
                ):
                    fields["search"].resolver(
                        None, self.info, types=["RouteRecord"], query="classified"
                    )
            backend.assert_not_called()

    def test_declared_replaced_descriptor_enforces_underlying_field_gate(self):
        from general_manager.api.graphql_search import register_search_query

        self.Record.Factory.create(
            name="ordinary", restricted="classified", visibility="public"
        )
        replacement = property(lambda instance: instance.restricted)
        dependencies = {
            "name": ("name", "restricted"),
            "visibility": ("visibility",),
            "restricted": ("restricted",),
        }
        with (
            patch.object(self.Record, "name", replacement),
            patch.object(
                self.Record.SearchConfig,
                "field_dependencies",
                dependencies,
                create=True,
            ),
        ):
            fields = {}
            register_search_query(
                fields,
                GraphQL.manager_registry,
                GraphQL.graphql_type_registry,
                None,
                None,
            )
            with patch.object(
                self.backend, "search", wraps=self.backend.search
            ) as backend:
                with self.assertRaisesRegex(GraphQLError, "Permission denied"):
                    fields["search"].resolver(
                        None, self.info, types=["RouteRecord"], query="absent"
                    )
            backend.assert_not_called()
